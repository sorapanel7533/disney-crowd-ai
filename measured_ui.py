"""Public submission and shared analysis. Only moderation requires admin login."""
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime,timedelta
import matplotlib.pyplot as plt
import pandas as pd
import measured as m
import reliability as r
from measured_store import configured,Unavailable

def history_for(park):
    # Existing snapshot synchronization never writes the new measured database.
    import utils
    from persistence import sync_public_snapshot
    name='disneyland.db' if park=='Disneyland' else 'disneysea.db'
    db,_=utils.connect_db(name)
    try:
        sync_public_snapshot(db,name,os.environ.get('DISNEY_DB_DIR','.runtime'))
        return pd.read_sql_query('SELECT * FROM wait_times WHERE park=?',db,params=(park,))
    finally:db.close()

def shared(st):
    if os.environ.get('DISNEY_DISABLE_REMOTE_SYNC')=='1':return m.connect(),'isolated-test-environment'
    try:
        db,head,_=configured(st).load()
        return db,head
    except Unavailable:
        st.warning('実測記録を読み込めません。時間をおいて開き直してください。')
        return None,None

def analytics(st,db,park):
    approved=st.radio('集計対象',['承認済み','未承認を含む参考集計'],horizontal=True)
    f=m.frame(db,approved=approved=='承認済み')
    if not f.empty:f=f[f.park==park]
    st.caption('未承認は自己申告の参考集計です。隔離・無効・計測途中は除外し、推定の学習には使いません。')
    if f.empty:
        st.info('対象の記録は0件です。推定準備中です。');return
    ids=sorted(f.ride_id.unique());selected=st.selectbox('分析する施設',['すべて']+ids,format_func=lambda x:x if x=='すべて' else r.japanese_name(m.attraction(park,x)['name']))
    if selected!='すべて':f=f[f.ride_id==selected]
    days=sorted(f.date.unique())
    if len(days)>1:
        dates=st.select_slider('集計する日付範囲',options=days,value=(days[0],days[-1]),key='mw_dates')
        f=f[f.date.between(*dates)]
    conditions={'weekday':'曜日','holiday':'平日・土日祝','hour':'時間帯','band':'表示待ち時間帯','month':'月','season':'季節'}
    with st.expander('条件別の絞り込み'):
        for col,label in conditions.items():
            choices=sorted(f[col].dropna().unique().tolist());values=st.multiselect(label,choices,key='mw_filter_'+col)
            if values:f=f[f[col].isin(values)]
    width=st.number_input('乖離の階級幅（分）',1,120,15)
    summary,bins=m.statistics(f,width)
    st.write('使用件数',len(f),'件 / 表示値と照合できる件数',summary['n'],'件')
    labels={'n':'照合件数','ratio_n':'乖離率を計算できる件数','mean':'平均乖離（分）','median':'中央値乖離（分）','std':'標準偏差（分）',
      'mean_pct':'平均乖離率（%）','median_pct':'中央値乖離率（%）','q10':'10パーセンタイル（分）','q25':'25パーセンタイル（分）',
      'q75':'75パーセンタイル（分）','q90':'90パーセンタイル（分）','within_displayed':'表示以内に乗車した割合','shorter_10':'10分以上短い割合','longer_10':'10分以上長い割合'}
    st.dataframe(pd.DataFrame([{'指標':labels[k],'値':v} for k,v in summary.items()]),hide_index=True,width='stretch')
    st.dataframe(bins,hide_index=True,width='stretch')
    if not summary['n']:return
    st.bar_chart(bins.set_index('階級下限')[['件数']]);st.bar_chart(bins.set_index('階級下限')[['相対度数']])
    st.line_chart(bins.set_index('階級下限')[['累積相対度数']])
    fig,ax=plt.subplots();ax.scatter(f.displayed_wait,f.actual_wait,label='Submitted actual')
    hi=max(float(f.displayed_wait.max()),float(f.actual_wait.max()),1);ax.plot([0,hi],[0,hi],'--',label='Actual = displayed')
    ax.set(xlabel='Displayed minutes',ylabel='Actual minutes');ax.legend();st.pyplot(fig);plt.close(fig)
    st.dataframe(f.groupby('band',observed=True).deviation.agg(['count','mean','median']).reset_index(),hide_index=True)
    st.line_chart(f.groupby('date').deviation.agg(['mean','median']))
    st.subheader('施設別の時間推移')
    if selected=='すべて':st.caption('施設を1つ選ぶと時間推移を表示します。');return
    from measured_views import timeline
    timeline(st,db,park,selected,f)

def choose_ride(st, park, key):
    catalog = {int(k): value for k, value in r.load_catalog().items() if value['park'] == park}
    return st.selectbox('アトラクション', list(catalog), key=f'{key}_{park}',
                        format_func=lambda ride: catalog[ride].get('ja', catalog[ride]['name']))


def clock_label(value):
    return r.utc(value).tz_convert(r.JST).strftime('%m/%d %H:%M:%S')


def show_record(st, row):
    st.write(r.japanese_name(row['attraction']))
    st.write(f"開始　{clock_label(row['started_at'])}（日本時間）")
    wait = row['displayed_wait']
    st.write('開始時の表示待ち時間　' + (f'{wait:g}分' if wait is not None else '取得できませんでした'))
    if row['display_observed_at'] and row['display_source'] != 'manual-sign':
        st.caption(f"表示待ち時間の観測　{clock_label(row['display_observed_at'])}（日本時間）")
    if row['boarded_at'] is None:
        return
    st.write(f"乗車　{clock_label(row['boarded_at'])}（日本時間）")
    st.write(f"実測待ち時間　{row['actual_wait']:.1f}分")
    if row['deviation'] is not None:
        difference = f"{row['deviation']:+.1f}分"
        if row['deviation_pct'] is not None:
            difference += f"（{row['deviation_pct']:+.1f}%）"
        st.write(f'表示との差　{difference}')
    labels = {'pending': '承認待ち', 'approved': '承認済み', 'quarantined': '確認が必要', 'invalid': '無効'}
    st.caption(labels[row['status']])
    st.success('保存完了')


def capture_click(st, key):
    # Callbacks run before the page loads any remote data. Retries keep this timestamp.
    if key not in st.session_state:
        st.session_state[key] = r.iso()


def live_display(park, ride, started):
    import requests
    from utils import PARK_SETTINGS, fetch_wait_times

    try:
        waits, _ = fetch_wait_times(dict(PARK_SETTINGS[park], park=park))
    except (requests.RequestException, ValueError, RuntimeError):
        return None
    source = waits.rename(columns={
        'RideID': 'ride_id', 'UpdatedUTC': 'observed_at_utc', 'FetchedUTC': 'fetched_at_utc',
        'Quality': 'quality', 'Open': 'is_open', 'Wait': 'wait_time',
    }).assign(park=park)
    return m.displayed(source, park, ride, started, live=True)


def timer(st, backend, park, receipt, own):
    now = r.utc().tz_convert(r.JST)
    for row in own:
        key = f"mw_started_{park}_{row['ride_id']}"
        pending = st.session_state.get(key + '_record')
        if pending and pending['id'] == row['id']:
            st.session_state.pop(key, None)
            st.session_state.pop(key + '_record', None)
    drafts = [row for row in own if row['status'] == 'draft']
    active = []
    for row in drafts:
        started = r.utc(row['started_at']).tz_convert(r.JST)
        captured = st.session_state.get('mw_boarded_' + row['id'])
        end = r.utc(captured).tz_convert(r.JST) if captured else now
        if started.date() == end.date() and timedelta(0) <= end - started <= timedelta(minutes=m.MAX_WAIT_MINUTES):
            active.append(row)
    if active:
        st.caption('あとで再開する場合は、このページのURLを自分用に保存してください。')
        for row in active:
            show_record(st, row)

            @st.fragment(run_every=15)
            def elapsed(started_at):
                minutes = max(0, int((r.utc() - r.utc(started_at)).total_seconds() // 60))
                st.write(f'経過時間　{minutes}分')

            elapsed(row['started_at'])
            key = 'mw_boarded_' + row['id']
            if st.button('今、乗車した', key='finish_' + row['id'], type='primary', width='stretch',
                         disabled=not backend.token, on_click=capture_click, args=(st, key)):
                try:
                    saved, _ = backend.finish(row['id'], receipt, st.session_state[key])
                except Unavailable:
                    st.error('保存を確認できません。もう一度押すと同じ乗車時刻で確認します。')
                except (ValueError, PermissionError) as exc:
                    st.session_state.pop(key, None)
                    st.error(str(exc))
                else:
                    st.session_state.pop(key, None)
                    st.session_state['mw_last_saved'] = saved['id']
                    st.rerun()
        return

    completed = [row for row in own if row['input_method'] == 'timer' and row['boarded_at'] is not None]
    if completed:
        show_record(st, max(completed, key=lambda row: row['boarded_at']))
    if drafts:
        st.caption('日付が変わった計測と、6時間を過ぎた計測は終了できません。')
    ride = choose_ride(st, park, 'mw_timer_ride')
    key = f'mw_started_{park}_{ride}'
    row_key = key + '_record'
    if st.button('今から並ぶ', type='primary', width='stretch', disabled=not backend.token,
                 on_click=capture_click, args=(st, key)):
        try:
            started = st.session_state[key]
            if row_key not in st.session_state:
                display = live_display(park, ride, started)
                st.session_state[row_key] = m.make(park, ride, started, display=display, receipt=receipt)
            backend.submit(st.session_state[row_key])
        except Unavailable:
            st.error('計測の開始を保存できません。もう一度押すと同じ開始時刻で保存します。')
        except (ValueError, PermissionError) as exc:
            st.session_state.pop(key, None)
            st.session_state.pop(row_key, None)
            st.error(str(exc))
        else:
            st.session_state.pop(key, None)
            st.session_state.pop(row_key, None)
            st.rerun()


def manual_input(st, backend, park, receipt, own):
    last = next((row for row in own if row['id'] == st.session_state.get('mw_last_saved')
                 and row['input_method'] != 'timer'), None)
    if last:
        show_record(st, last)
    ride = choose_ride(st, park, 'mw_manual_ride')
    now = datetime.now(r.JST)
    default_time = now.time().replace(second=0, microsecond=0)
    day = st.date_input('並び始めた日（日本時間）', now.date(), max_value=now.date(), key='mw_day')
    start_time = st.time_input('並び始めた時刻', default_time, key='mw_start')
    start = datetime.combine(day, start_time, tzinfo=r.JST)
    method = st.radio('入力方法', ['開始・乗車時刻から計算', '待ち時間を直接入力'])
    end = None
    minutes = None
    if method == '開始・乗車時刻から計算':
        end_time = st.time_input('乗車した時刻（開始と同じ日）', default_time, key='mw_end_time')
        end = datetime.combine(day, end_time, tzinfo=r.JST)
    else:
        minutes = st.number_input('実際に並んだ時間（分）', 0, m.MAX_WAIT_MINUTES, 0)
    manual = st.checkbox('現地の案内表示も記録する（任意）')
    value = st.number_input('並び始めた時の案内表示（分）', 0, 1440, 0) if manual else None
    if st.button('実測を投稿する', type='primary', width='stretch', disabled=not backend.token):
        try:
            if 'mw_record_id' not in st.session_state:
                st.session_state['mw_record_id'] = str(uuid.uuid4())
            row = m.make(park, ride, start, end=end, minutes=minutes, receipt=receipt,
                         record_id=st.session_state['mw_record_id'])
            try:
                display = m.displayed(history_for(park), park, ride, start)
            except (sqlite3.Error, RuntimeError):
                display = None
            if not display and manual:
                display = {'displayed_wait': value, 'display_source': 'manual-sign',
                           'display_observed_at': m.stamp(start)}
            if display:
                row = m.make(park, ride, start, end=end, minutes=minutes, display=display,
                             receipt=receipt, record_id=row['id'])
            saved, _ = backend.submit(row)
        except Unavailable:
            st.error('保存を確認できません。入力内容を確認して再試行してください。')
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state['mw_last_saved'] = saved['id']
            st.session_state.pop('mw_record_id', None)
            st.rerun()


def render(st, park, is_admin=False):
    st.subheader('実測待ち時間')
    st.caption('ログイン不要。待ち時間の記録は承認後に推定へ使われます。')
    mode = st.radio('記録方法', ['リアルタイム計測', '過去の記録を手動入力'], key='mw_mode', horizontal=True)
    backend = configured(st)
    if not backend.token:
        st.warning('現在、投稿を保存できません。時間をおいてお試しください。')
    receipt = st.query_params.get('measurement_key', '')
    if not re.fullmatch(r'[a-f0-9]{64}', receipt):
        receipt = secrets.token_hex(32)
        st.query_params['measurement_key'] = receipt
    if st.query_params.get('measurement_park') != park:
        st.query_params['measurement_park'] = park
    db, _ = shared(st)
    if db is None:
        return
    try:
        own = [row for row in m.records(db) if not row['is_test'] and row['park'] == park
               and row['receipt_hash'] == m.digest(receipt)]
        if mode == 'リアルタイム計測':
            timer(st, backend, park, receipt, own)
        else:
            manual_input(st, backend, park, receipt, own)
        with st.expander('みんなの実測・乖離統計'):
            analytics(st, db, park)
    finally:
        db.close()

def moderation(st,is_admin):
    st.subheader('実測投稿の審査・修正')
    if not is_admin:st.info('投稿は「実測待ち時間」から全員が利用できます。審査・修正だけ管理者認証が必要です。');return
    db,head=shared(st)
    if db is None:return
    try:
        st.caption('外部DBの確認コミット: '+str(head or '初回投稿前'))
        rows=m.records(db)
        st.write('総件数',len(rows),' / 承認待ち',sum(x['status']=='pending' for x in rows),' / 隔離',sum(x['status']=='quarantined' for x in rows))
        st.dataframe(pd.DataFrame([{k:v for k,v in x.items() if k not in ('receipt_hash','fingerprint')} for x in rows]),hide_index=True)
        if not rows:return
        rid=st.selectbox('審査する記録ID',[x['id'] for x in rows]);status=st.selectbox('変更後の状態',['approved','pending','invalid'])
        change=st.checkbox('実測分数を修正する');correction=st.number_input('修正後の実測（分）',0.,1440.,0.,disabled=not change)
        reason=st.text_input('審査・修正理由（個人情報は記入しない）')
        if st.button('審査結果を外部保存'):
            try:
                configured(st).moderate(rid,status,authorized=is_admin,correction=correction if change else None,reason=reason)
                st.success('審査結果の外部保存と読み戻しを確認しました')
            except (ValueError,Unavailable,PermissionError) as exc:st.error(str(exc))
        audit=pd.read_sql_query('SELECT record_id,at,payload FROM measurement_audit ORDER BY at DESC',db)
        if len(audit):
            def sanitize(payload):
                value=json.loads(payload)
                for key in ('before','after'):
                    if value.get(key):value[key]={k:v for k,v in value[key].items() if k not in ('receipt_hash','fingerprint')}
                return json.dumps(value,ensure_ascii=False)
            audit['payload']=audit.payload.map(sanitize)
            st.dataframe(audit,hide_index=True)
        f=m.frame(db,as_of=r.iso());evaluation=m.evaluate(f)
        st.caption('モデル '+m.VERSION+'。未検証モデルを正式採用しません。承認が評価日より後なら過去の学習には使いません。')
        if evaluation.empty:st.info('独立した評価日・記録が不足しています。実測精度は未検証です。')
        else:
            summaries=[]
            for method,g in evaluation.groupby('method'):
                summaries.append({'method':method,'days':g.date.nunique(),**m.metrics(g.error),'baseline_MAE':m.metrics(g.baseline_error)['MAE']})
            st.dataframe(pd.DataFrame(summaries),hide_index=True)
    finally:db.close()
