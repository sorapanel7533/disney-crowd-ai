"""Public submission and shared analysis. Only moderation requires admin login."""
import json
import os
import secrets
import uuid
from datetime import datetime,timedelta
from pathlib import Path
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
    except Unavailable as exc:
        st.warning(str(exc));return None,None

def analytics(st,db,park):
    st.subheader('みんなの実測・乖離統計')
    include_test=st.checkbox('動作確認用の投稿だけを表示',key='mw_test_view')
    approved=st.radio('集計対象',['承認済み','未承認を含む参考集計'],horizontal=True)
    f=m.frame(db,approved=approved=='承認済み',include_test=include_test)
    if not f.empty:f=f[(f.park==park)&f.is_test.eq(include_test)]
    st.caption('未承認は自己申告の参考集計です。隔離・無効・計測途中は除外し、推定の学習には使いません。')
    if f.empty:
        st.info('対象の記録は0件です。推定準備中です。');return
    ids=sorted(f.ride_id.unique());selected=st.selectbox('分析する施設',['すべて']+ids,format_func=lambda x:x if x=='すべて' else r.japanese_name(m.attraction(park,x)['name']))
    if selected!='すべて':f=f[f.ride_id==selected]
    dates=st.date_input('集計する日付範囲',(f.date.min(),f.date.max()),key='mw_dates')
    if len(dates)==2:f=f[f.date.between(*dates)]
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

def render(st,park,is_admin=False):
    st.subheader('実測待ち時間')
    st.caption('ログイン不要。氏名・メール・位置情報は入力しません。匿名の日時・施設・待ち時間は共有保存されます。未承認の投稿はAI学習に使いません。')
    backend=configured(st)
    if not backend.token:st.warning('投稿の外部保存設定は準備中です。入力はできますが、投稿完了にはなりません。')
    is_test=st.checkbox('動作確認用の投稿（本番統計・学習から除外）',key='mw_test_submission')
    receipt=st.query_params.get('measurement_key','')
    if len(receipt)!=64:
        receipt=secrets.token_hex(32);st.query_params['measurement_key']=receipt
    st.caption('計測途中の再開は、このページのURLを自分用に保存してください。再開キー付きURLは他人と共有しないでください。')
    if 'mw_record_id' not in st.session_state:st.session_state['mw_record_id']=str(uuid.uuid4())
    db,head=shared(st)
    if db is None:return
    try:
        allrows=m.records(db)
        own=[x for x in allrows if x['receipt_hash']==m.digest(receipt)]
        for row in own:
            st.caption('あなたの記録: '+row['id']+' / '+row['status'])
            if row['status']=='draft':
                st.info(r.japanese_name(row['attraction'])+' / 開始 '+str(r.utc(row['started_at']).tz_convert(r.JST)))
                if st.button('今、乗車した',key='finish_'+row['id'],disabled=not backend.token):
                    try:
                        result,commit=backend.finish(row['id'],receipt,r.iso())
                        st.success('外部保存を確認しました。記録ID: '+result['id']);st.session_state['mw_last_saved']=result['id'];st.rerun()
                    except (Unavailable,ValueError,PermissionError) as exc:st.error(str(exc))
        if st.session_state.get('mw_last_saved'):st.success('保存完了（外部保存・読み戻し確認済み）: '+st.session_state['mw_last_saved'])
        catalog={int(k):v for k,v in r.load_catalog().items() if v['park']==park}
        ride=st.selectbox('アトラクション',list(catalog),format_func=lambda k:catalog[k].get('ja',catalog[k]['name']),key='mw_ride')
        now=datetime.now(r.JST)
        if st.button('現在時刻を入力'):
            st.session_state['mw_day']=now.date();st.session_state['mw_start']=now.time().replace(tzinfo=None,second=0,microsecond=0)
        day=st.date_input('並び始めた日（JST）',now.date(),max_value=now.date(),key='mw_day')
        start_time=st.time_input('並び始めた時刻（JST）',now.time().replace(tzinfo=None,second=0,microsecond=0),key='mw_start')
        start=datetime.combine(day,start_time,tzinfo=r.JST)
        if st.button('今から並ぶ',disabled=not backend.token):
            try:
                from utils import fetch_wait_times,PARK_SETTINGS
                import requests
                try:full,_=fetch_wait_times(dict(PARK_SETTINGS[park],park=park))
                except (requests.RequestException,ValueError,RuntimeError):
                    full=pd.DataFrame();st.warning('開始時点の表示値は取得できませんでした。計測自体は記録します。')
                start_now=datetime.now(r.JST)
                source=full.rename(columns={'RideID':'ride_id','UpdatedUTC':'observed_at_utc','FetchedUTC':'fetched_at_utc','Quality':'quality','Open':'is_open','Wait':'wait_time'}).assign(park=park)
                display=m.displayed(source,park,ride,start_now)
                if display:display['display_source']='queue-times-live'
                row=m.make(park,ride,start_now,display=display,receipt=receipt,record_id=st.session_state['mw_record_id'],is_test=is_test)
                saved,_=backend.submit(row);st.session_state['mw_last_saved']=saved['id'];st.session_state['mw_record_id']=str(uuid.uuid4());st.rerun()
            except (Unavailable,ValueError,RuntimeError,PermissionError) as exc:st.error(str(exc))
        method=st.radio('入力方法',['開始・乗車時刻から計算','待ち時間を直接入力'],horizontal=True)
        end=None;minutes=None
        if method=='開始・乗車時刻から計算':
            end_day=st.date_input('乗車した日（翌日なら日付も変更）',day,key='mw_end_day',max_value=now.date())
            end_time=st.time_input('乗車した時刻（JST）',now.time().replace(tzinfo=None,second=0,microsecond=0),key='mw_end_time')
            end=datetime.combine(end_day,end_time,tzinfo=r.JST)
            st.caption('計算した実測: '+str(round((end-start).total_seconds()/60,1))+'分')
        else:
            minutes=st.number_input('実際に並んだ時間（分）',0.,1440.,45.,step=1.)
            st.caption('計算上の乗車日時: '+str(start+timedelta(minutes=minutes)))
        if st.button('開始時点の表示値を履歴から確認'):
            st.session_state['mw_match']=m.displayed(history_for(park),park,ride,start)
            st.session_state['mw_match_key']=(park,ride,m.stamp(start))
        match=st.session_state.get('mw_match') if st.session_state.get('mw_match_key')==(park,ride,m.stamp(start)) else None
        if match:st.info('開始以前の観測: '+str(match['displayed_wait'])+'分 / '+str(r.utc(match['display_observed_at']).tz_convert(r.JST)))
        manual=st.checkbox('現地の案内表示を手動入力（履歴がない場合）',disabled=bool(match))
        value=st.number_input('現地で確認した開始時点の表示（分）',0.,1440.,0.,disabled=not manual or bool(match))
        if st.button('実測を投稿する',type='primary',disabled=not backend.token):
            try:
                # Resolve against fresh local history at submission, never a current fallback.
                resolved=m.displayed(history_for(park),park,ride,start)
                if not resolved and manual:resolved={'displayed_wait':value,'display_source':'manual-sign','display_observed_at':m.stamp(start)}
                row=m.make(park,ride,start,end=end,minutes=minutes,display=resolved,receipt=receipt,record_id=st.session_state['mw_record_id'],is_test=is_test)
                saved,commit=backend.submit(row)
                st.session_state['mw_last_saved']=saved['id'];st.session_state['mw_record_id']=str(uuid.uuid4());st.rerun()
            except (Unavailable,ValueError,PermissionError) as exc:st.error(str(exc))
        if st.button('みんなの集計を再取得'):st.rerun()
        st.caption('外部DBの確認コミット: '+str(head or '初回投稿前'))
        analytics(st,db,park)
    finally:db.close()

def moderation(st,is_admin):
    st.subheader('実測投稿の審査・修正')
    if not is_admin:st.info('投稿は「実測待ち時間」から全員が利用できます。審査・修正だけ管理者認証が必要です。');return
    db,head=shared(st)
    if db is None:return
    try:
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
