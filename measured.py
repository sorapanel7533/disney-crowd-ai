"""Anonymous measurements, audited moderation, and strictly as-of statistics."""
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import jpholiday
import reliability as r

VERSION = 'actual-wait-v1'
METHODS = ('mean', 'median', 'band', 'attraction', 'context', 'hierarchical')
MAX_WAIT_MINUTES = 360
LIVE_CAPTURE_WINDOW = timedelta(minutes=1)

def stamp(value=None):
    return r.iso(value)

def connect(path=':memory:'):
    db=sqlite3.connect(path,timeout=20)
    db.execute('PRAGMA busy_timeout=20000')
    db.executescript('''CREATE TABLE IF NOT EXISTS measurements
      (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS measurement_audit
      (id TEXT PRIMARY KEY, record_id TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS measured_models
      (id TEXT PRIMARY KEY, payload TEXT NOT NULL);''')
    return db

def records(db):
    return [json.loads(x[0]) for x in db.execute('SELECT payload FROM measurements')]

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def attraction(park,ride_id):
    matches=[dict(x,ride_id=int(k)) for k,x in r.load_catalog().items() if x['park']==park and str(k)==str(ride_id)]
    if len(matches)!=1: raise ValueError('パークと施設を選び直してください')
    return matches[0]

def number(value):
    n=r.finite(value)
    if n is None or not 0<=n<=1440: raise ValueError('待ち時間は0〜1440分で入力してください')
    return n

def displayed(history, park, ride_id, start, *, live=False):
    """Match observations at entry; a live request may finish shortly after the click."""
    if history is None or history.empty:return None
    f=history.copy()
    required={'park','ride_id','observed_at_utc','fetched_at_utc','quality','is_open','wait_time'}
    if not required<=set(f):return None
    observed=pd.to_datetime(f.observed_at_utc,utc=True,errors='coerce')
    fetched=pd.to_datetime(f.fetched_at_utc,utc=True,errors='coerce')
    t=r.utc(start)
    keep=(f.park==park)&(pd.to_numeric(f.ride_id,errors='coerce')==int(ride_id))&f.quality.eq('valid')&f.is_open.eq(1)
    acquisition_limit = t + LIVE_CAPTURE_WINDOW if live else t
    keep &= observed.between(t-pd.Timedelta(minutes=20),t)&fetched.between(observed,acquisition_limit)
    keep &= pd.to_numeric(f.wait_time,errors='coerce').between(0,300)
    f=f[keep].assign(_observed=observed[keep]).sort_values('_observed')
    if f.empty:return None
    row=f.iloc[-1]
    return {'displayed_wait':float(row.wait_time),'display_source':'queue-times-live' if live else 'queue-times-history',
            'display_observed_at':stamp(row.observed_at_utc),'display_fetched_at':stamp(row.fetched_at_utc)}

def derive(row):
    out=dict(row);a=out.get('actual_wait');d=out.get('displayed_wait')
    out['deviation']=a-d if a is not None and d is not None else None
    out['deviation_pct']=(a-d)/d*100 if a is not None and d is not None and d>0 else None
    out['ratio']=a/d if a is not None and d is not None and d>0 else None
    out['calculation_version']=VERSION
    return out

def validate_wait(start, end, now, *, allow_zero=False):
    start, end, now = (r.utc(value) for value in (start, end, now))
    if any(pd.isna(value) for value in (start, end, now)):
        raise ValueError('開始・乗車日時を確認してください')
    if start > now or end > now:
        raise ValueError('未来の日時は記録できません')
    if start.tz_convert(r.JST).date() != end.tz_convert(r.JST).date():
        raise ValueError('開始と乗車は同じ日で入力してください')
    if end < start:
        raise ValueError('乗車時刻は開始時刻より後にしてください')
    if end == start and not allow_zero:
        raise ValueError('0分で乗車した場合は、待ち時間の直接入力を使ってください')
    minutes = (end - start).total_seconds() / 60
    if minutes > MAX_WAIT_MINUTES:
        raise ValueError(f'待ち時間は{MAX_WAIT_MINUTES}分以内で入力してください')
    return minutes


def make(park,ride_id,start,*,end=None,minutes=None,display=None,receipt='',record_id=None,now=None,is_test=False):
    now=stamp(now);start=stamp(start);ride=attraction(park,ride_id)
    if pd.isna(r.utc(start)) or r.utc(start)>r.utc(now):raise ValueError('開始日時を確認してください。未来の日時は記録できません')
    if end is not None:
        end=stamp(end);actual=validate_wait(start,end,now);method='timestamps';computed=False
    elif minutes is not None:
        actual=number(minutes);end=stamp(r.utc(start)+pd.Timedelta(minutes=actual));method='minutes';computed=True
        validate_wait(start,end,now,allow_zero=True)
    else:actual=None;end=None;method='timer';computed=False
    if not receipt or len(receipt)<32:raise ValueError('再開キーが不足しています')
    row={'id':record_id or str(uuid.uuid4()),'park':park,'ride_id':int(ride_id),'attraction':ride['name'],
         'started_at':start,'boarded_at':end,'input_method':method,'end_is_calculated':computed,'actual_wait':actual,
         'displayed_wait':None,'display_source':'missing','display_observed_at':None,'display_fetched_at':None,
         'created_at':now,'updated_at':now,'approved_at':None,'receipt_hash':digest(receipt),'is_test':bool(is_test)}
    if display:
        d=number(display['displayed_wait']);source=display['display_source']
        if source not in ('queue-times-live','queue-times-history','manual-sign'):raise ValueError('不正な表示値の取得元です')
        observed=stamp(display['display_observed_at']);fetched=stamp(display.get('display_fetched_at') or observed)
        acquisition_limit = r.utc(start)
        if method == 'timer' and source == 'queue-times-live':
            acquisition_limit = min(r.utc(now), acquisition_limit + LIVE_CAPTURE_WINDOW)
        if not r.utc(start)-pd.Timedelta(minutes=20)<=r.utc(observed)<=r.utc(start) or not r.utc(observed)<=r.utc(fetched)<=acquisition_limit:
            raise ValueError('開始時点の表示待ち時間を確認できません')
        row.update(displayed_wait=d,display_source=source,display_observed_at=observed,display_fetched_at=fetched if source!='manual-sign' else None)
    return derive(row)

def audit(db,row,before,action,now):
    db.execute('INSERT INTO measurement_audit VALUES (?,?,?,?)',
      (str(uuid.uuid4()),row['id'],now,json.dumps({'action':action,'before':before,'after':row},ensure_ascii=False)))

def save(db,row,now=None):
    """One transaction owns deduplication, throttling and append-only audit."""
    now=stamp(now)
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing=next((x for x in records(db) if x['id']==row['id']),None)
        if existing:
            if existing['receipt_hash']!=row['receipt_hash']:raise PermissionError('記録IDが競合しました')
            fields=('park','ride_id','started_at','actual_wait','input_method','displayed_wait','display_source','is_test')
            if any(existing.get(k)!=row.get(k) for k in fields):raise ValueError('この記録IDは保存済みです。再取得して内容を確認してください。')
            return existing
        row=dict(row);flags=[]
        if pd.isna(r.utc(row['started_at'])) or r.utc(row['started_at']) > r.utc(now):
            raise ValueError('開始日時を確認してください')
        if row['actual_wait'] is not None:
            if row['boarded_at'] is None:
                raise ValueError('乗車日時を確認してください')
            duration = validate_wait(row['started_at'], row['boarded_at'], now, allow_zero=row['input_method']=='minutes')
            if not math.isclose(number(row['actual_wait']), duration, abs_tol=1e-6):
                raise ValueError('待ち時間が開始・乗車日時と一致しません')
        elif row['boarded_at'] is not None or row['input_method'] != 'timer':
            raise ValueError('計測の開始情報を確認してください')
        old=records(db)
        if sum(r.utc(x['created_at']).date()==r.utc(now).date() for x in old)>=2000:
            raise ValueError('本日の投稿上限です。時間をおいて再試行してください')
        recent=[x for x in old if x['receipt_hash']==row['receipt_hash'] and r.utc(x['created_at'])>r.utc(now)-pd.Timedelta(hours=1)]
        if len(recent)>=30:raise ValueError('投稿が集中しています。時間をおいて再試行してください')
        if len(recent)>=5:flags.append('rapid_submissions')
        row['fingerprint']=digest('|'.join(str(row.get(k)) for k in ('park','ride_id','started_at','actual_wait','is_test')))
        if any(x.get('fingerprint')==row['fingerprint'] for x in old):flags.append('duplicate_candidate')
        if row['actual_wait'] is not None and row.get('ratio') is not None and not .1<=row['ratio']<=3:
            flags.append('extreme_wait')
        row['quality_flags']=flags
        row['status']='draft' if row['actual_wait'] is None else 'quarantined' if flags else 'pending'
        row['quality']='needs_review' if flags else 'unreviewed'
        db.execute('INSERT INTO measurements VALUES (?,?)',(row['id'],json.dumps(row,ensure_ascii=False)))
        audit(db,row,None,'submit',now)
    return row

def finish(db,record_id,receipt,end,now=None):
    now=stamp(now)
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=next((x for x in records(db) if x['id']==record_id),None)
        if not old or old['receipt_hash']!=digest(receipt):raise PermissionError('再開キーが一致しません')
        if old['status']!='draft':return old
        end=stamp(end);actual=validate_wait(old['started_at'],end,now)
        new=derive({**old,'boarded_at':end,'actual_wait':actual,'updated_at':now})
        flags=list(old['quality_flags'])
        if new.get('ratio') is not None and not .1<=new['ratio']<=3:flags.append('extreme_wait')
        new.update(status='quarantined' if flags else 'pending',quality_flags=flags,quality='needs_review' if flags else 'unreviewed')
        db.execute('UPDATE measurements SET payload=? WHERE id=?',(json.dumps(new,ensure_ascii=False),record_id))
        audit(db,new,old,'finish',now)
    return new

def moderate(db,record_id,status,*,authorized=False,correction=None,reason='',now=None):
    if not authorized:raise PermissionError('管理者だけが承認・修正・無効化できます')
    if status not in ('approved','pending','invalid'):raise ValueError('不正な審査状態です')
    if not reason.strip():raise ValueError('変更理由を入力してください')
    now=stamp(now)
    with db:
        db.execute('BEGIN IMMEDIATE')
        old=next(x for x in records(db) if x['id']==record_id)
        if old['actual_wait'] is None:raise ValueError('未完了の計測は承認できません')
        new=dict(old)
        if correction is not None:
            actual=number(correction);end=stamp(r.utc(old['started_at'])+pd.Timedelta(minutes=actual))
            validate_wait(old['started_at'],end,now,allow_zero=True)
            new.update(actual_wait=actual,boarded_at=end,end_is_calculated=True)
        new=derive({**new,'status':status,'quality':'reviewed','approved_at':now if status=='approved' else None,'updated_at':now})
        db.execute('UPDATE measurements SET payload=? WHERE id=?',(json.dumps(new,ensure_ascii=False),record_id))
        audit(db,new,old,'moderate: '+reason.strip()[:500],now)
    return new

def frame(db,approved=True,include_test=False,as_of=None):
    rows=[x for x in records(db) if x['actual_wait'] is not None and x['status'] in (('approved',) if approved else ('pending','approved')) and (include_test or not x['is_test'])]
    if as_of is not None:
        rows=[x for x in rows if r.utc(x['updated_at'])<=r.utc(as_of) and r.utc(x['boarded_at'])<=r.utc(as_of)]
    f=pd.DataFrame(rows)
    if f.empty:return f
    for col in ('displayed_wait','actual_wait','deviation','deviation_pct','ratio'):
        f[col]=pd.to_numeric(f[col],errors='coerce')
    t=pd.to_datetime(f.started_at,utc=True).dt.tz_convert(r.JST)
    f['date']=t.dt.date;f['weekday']=t.dt.day_of_week;f['hour']=t.dt.hour;f['month']=t.dt.month
    f['season']=f.month.map(lambda m:(m%12)//3)
    f['holiday']=f.date.map(lambda d:d.weekday()>=5 or jpholiday.is_holiday(d))
    f['band']=pd.cut(f.displayed_wait,[-1,15,30,45,60,90,120,np.inf],labels=False)
    return f

def statistics(f,width=15):
    if width<1:raise ValueError('階級幅は1以上です')
    valid=f.dropna(subset=['deviation']) if not f.empty else f
    if valid.empty:return {'n':0,'ratio_n':0},pd.DataFrame(columns=['階級下限','件数','相対度数','累積相対度数'])
    d=valid.deviation.astype(float);pct=valid.deviation_pct.dropna().astype(float)
    summary={'n':len(d),'ratio_n':len(pct),'mean':d.mean(),'median':d.median(),'std':d.std(ddof=0),
      'mean_pct':pct.mean() if len(pct) else None,'median_pct':pct.median() if len(pct) else None,
      'q10':d.quantile(.1),'q25':d.quantile(.25),'q75':d.quantile(.75),'q90':d.quantile(.9),
      'within_displayed':d.le(0).mean(),'shorter_10':d.le(-10).mean(),'longer_10':d.ge(10).mean()}
    counts=(np.floor(d/width)*width).value_counts().sort_index()
    counts=counts.reindex(np.arange(counts.index.min(),counts.index.max()+width,width),fill_value=0)
    bins=pd.DataFrame({'階級下限':counts.index,'件数':counts.values})
    bins['相対度数']=bins['件数']/len(d);bins['累積相対度数']=bins['相対度数'].cumsum()
    return summary,bins

def estimate(training,park,ride_id,displayed_wait,target,method='hierarchical',as_of=None,opened=True):
    base={'estimate':None,'state':'推定準備中','n':0,'days':0,'version':VERSION+'/'+method,'interval':None,'calculated_at':stamp(as_of),'base_model':r.MODEL_VERSION}
    if method not in METHODS:raise ValueError('Unknown model')
    if not opened or r.finite(displayed_wait) is None or training.empty:return base
    cutoff=r.utc(as_of);t=r.utc(target).tz_convert(r.JST)
    # Recheck even when caller supplies a hand-built frame.
    f=training[(training.park==park)&training.displayed_wait.notna()&training.status.eq('approved')&~training.is_test].copy()
    f=f[pd.to_datetime(f.updated_at,utc=True).le(cutoff)&pd.to_datetime(f.boarded_at,utc=True).le(cutoff)]
    base.update(n=len(f),days=f.date.nunique() if len(f) else 0)
    if len(f)<20 or base['days']<5:
        base['state']='データ不足（参考統計のみ）' if len(f) else '推定準備中';return base
    scoped=f[f.ride_id==int(ride_id)];band=int(np.digitize(displayed_wait,[15,30,45,60,90,120],right=True))
    selected=f
    if method=='band':selected=f[f.band==band]
    elif method=='attraction':selected=scoped
    elif method=='context':selected=scoped[(scoped.hour==t.hour)&(scoped.weekday==t.weekday())]
    elif method=='hierarchical':selected=scoped[(scoped.band==band)&(scoped.hour//3==t.hour//3)]
    if selected.empty:selected=f
    overall=float(f.deviation.median());local=float(selected.deviation.median())
    correction=float(f.deviation.mean()) if method=='mean' else local
    if method=='hierarchical':correction=(len(selected)*local+20*overall)/(len(selected)+20)
    correction=float(np.clip(correction,-60,60))
    value=max(0,float(displayed_wait)+correction)
    base.update(estimate=round(value,1),state='暫定推定・精度未検証',n=len(selected),days=selected.date.nunique(),
      period=[str(selected.date.min()),str(selected.date.max())],mean_deviation=float(selected.deviation.mean()),median_deviation=local,
      interval=[round(max(0,displayed_wait+float(selected.deviation.quantile(q))),1) for q in (.1,.9)],
      interval_note='過去乖離の10〜90%範囲。将来の被覆率は未検証')
    return base

def evaluate(f):
    results=[]
    if f.empty:return pd.DataFrame()
    for day in sorted(f.date.unique()):
        issue=pd.Timestamp(day,tz=r.JST)
        train=f[(f.date<day)&pd.to_datetime(f.updated_at,utc=True).lt(issue.tz_convert('UTC'))]
        for row in f[f.date==day].to_dict('records'):
            if row['displayed_wait'] is None:continue
            for method in METHODS:
                p=estimate(train,row['park'],row['ride_id'],row['displayed_wait'],row['started_at'],method,as_of=issue)
                if p['estimate'] is not None:
                    results.append({'date':day,'park':row['park'],'ride_id':row['ride_id'],'weekday':row['weekday'],'hour':row['hour'],'method':method,'error':p['estimate']-row['actual_wait'],'baseline_error':row['displayed_wait']-row['actual_wait']})
    return pd.DataFrame(results)

def metrics(errors):
    e=np.asarray(errors,dtype=float)
    return {'n':len(e),'MAE':float(np.abs(e).mean()),'RMSE':float(np.sqrt((e**2).mean())),'Bias':float(e.mean())} if len(e) else {'n':0}
