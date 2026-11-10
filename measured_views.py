"""Additive actual-wait columns and plots; never replace the legacy prediction."""
import os
import pandas as pd
import matplotlib.pyplot as plt
import measured as m
import reliability as r
from measured_store import configured,Unavailable

def training(st):
    if os.environ.get('DISNEY_DISABLE_REMOTE_SYNC')=='1':return pd.DataFrame()
    try:
        db,_,_=configured(st).load()
        try:return m.frame(db,as_of=r.iso())
        finally:db.close()
    except Unavailable:
        st.caption('実測補正用DBは取得できません。既存の表示値・予測値を表示します。')
        return pd.DataFrame()

def parallel(st,source,park,value_column='Wait',target=None,current=False):
    f=training(st);out=source.copy()
    lookup={v['name']:int(k) for k,v in r.load_catalog().items() if v['park']==park}
    results=[]
    for row in out.to_dict('records'):
        name=row.get('Attraction');ride=lookup.get(name)
        opened=(row.get('Open') is True or row.get('Open')==1) if current else True
        # Live freshness and operating state, rather than an available number alone.
        if current:
            try:opened=opened and row.get('Quality')=='valid' and 0<=(r.utc()-r.utc(row.get('UpdatedUTC'))).total_seconds()<=1200
            except (ValueError,TypeError):opened=False
        at=target or r.iso()
        if target is not None and 'Hour' in row:
            at=pd.Timestamp(target).normalize()+pd.Timedelta(hours=int(row['Hour']),minutes=int(row.get('Minute',0)))
        p=m.estimate(f,park,ride or -1,row.get(value_column),at,as_of=r.iso(),opened=opened and ride is not None)
        results.append(p)
    out['推定実測（分）']=[x['estimate'] for x in results]
    out['実測推定の状態']=[x['state'] for x in results]
    st.caption('表示・予測待ち時間に実測補正を併記します。承認済みの件数・日数が不足すると推定値は表示しません。')
    columns=[c for c in ('日本語名','Attraction','TimeLabel','Time','Wait','Predicted Wait','Status','推定実測（分）','実測推定の状態') if c in out]
    st.dataframe(out[columns].rename(columns={'Wait':'表示待ち時間（分）','Predicted Wait':'予測表示待ち時間（分）'}),hide_index=True,width='stretch')
    with st.expander('推定実測の根拠・件数・期間・モデル'):
        st.caption('表示待ち時間モデル: '+r.MODEL_VERSION+' / 実測補正: '+m.VERSION+' / 階層縮約。20件・5日を暫定的な最低条件とし、精度を保証する基準にはしません。')
        if results:st.dataframe(pd.DataFrame(results),hide_index=True,width='stretch')
    return out

def timeline(st,db,park,ride,actuals):
    from measured_ui import history_for
    h=history_for(park)
    options=['表示履歴','実測（点）','推定実測','将来の表示予測','将来の推定実測']
    chosen=st.multiselect('時間推移に表示する系列',options,default=options,key='mw_series')
    fig,ax=plt.subplots(figsize=(10,4));name=m.attraction(park,ride)['name']
    if len(h):
        h=h[(pd.to_numeric(h.ride_id,errors='coerce')==int(ride))&h.quality.eq('valid')].copy()
        h['t']=pd.to_datetime(h.observed_at_utc,utc=True,errors='coerce').dt.tz_convert(r.JST)
        # Split lines on missing collection intervals; don't interpolate gaps.
        h=h.sort_values('t');h['segment']=(h.t.diff().dt.total_seconds().gt(1200)).cumsum()
        for i,(_,g) in enumerate(h.groupby('segment')):
            if '表示履歴' in chosen:ax.plot(g.t,g.wait_time.where(g.is_open.eq(1)),color='steelblue',label='Displayed history' if i==0 else None)
    if '実測（点）' in chosen:ax.scatter(pd.to_datetime(actuals.started_at,utc=True).dt.tz_convert(r.JST),actuals.actual_wait,color='black',label='Submitted actual (queue entry)')
    approved=m.frame(db,as_of=r.iso())
    if len(h) and '推定実測' in chosen:
        for i,(_,g) in enumerate(h.groupby('segment')):
            y=[m.estimate(approved,park,ride,x.wait_time,x.t,as_of=x.t,opened=x.is_open==1)['estimate'] for x in g.itertuples()]
            ax.plot(g.t,y,color='green',label='As-of actual estimate' if i==0 else None)
    # The existing predictor is used unchanged. Sparse/failed forecasts stay absent.
    if any(x in chosen for x in options[3:]) and len(h):
        import utils
        try:
            conn,_=utils.connect_db('disneyland.db' if park=='Disneyland' else 'disneysea.db')
            try:forecasts=pd.read_sql_query('SELECT * FROM predictions WHERE park=? AND attraction=? AND target_at_utc>=? AND generated_at_utc<=? ORDER BY generated_at_utc',conn,params=(park,name,r.iso(),r.iso()))
            finally:conn.close()
            if len(forecasts):
                forecasts=forecasts.drop_duplicates('target_at_utc',keep='last').sort_values('target_at_utc')
                t=pd.to_datetime(forecasts.target_at_utc,utc=True).dt.tz_convert(r.JST)
                if '将来の表示予測' in chosen:ax.plot(t,forecasts.predicted_wait,'--',color='orange',label='Saved forecast displayed')
                if '将来の推定実測' in chosen:
                    y=[m.estimate(approved,park,ride,v,at,as_of=r.iso())['estimate'] for at,v in zip(t,forecasts.predicted_wait)]
                    ax.plot(t,y,'--',color='purple',label='Forecast actual (unvalidated)')
                st.caption('将来線は保存済みの表示待ち時間予測です。実測と異なります。')
            else:st.caption('この施設の将来予測はまだ保存されていません。')
        except (ValueError,KeyError,TypeError,AttributeError):st.caption('将来線を計算できる履歴が不足しています。')
    ax.set(xlabel='JST queue entry / target time',ylabel='Minutes')
    handles,_=ax.get_legend_handles_labels()
    if handles:ax.legend()
    fig.autofmt_xdate();st.pyplot(fig);plt.close(fig)

def datetime_date():return r.utc().tz_convert(r.JST).date()
