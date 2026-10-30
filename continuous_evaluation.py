"""Immutable prospective shadow predictions. This never switches the UI model."""
import hashlib
import json
import numpy as np
import pandas as pd
import reliability as r
import model_registry as models
from data_quality import classify


def migrate(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS model_issues (issue_key TEXT PRIMARY KEY,park TEXT,
            generated_at_utc TEXT,target_date TEXT,kind TEXT,feature_cutoff_utc TEXT,features_json TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS model_predictions (prediction_key TEXT PRIMARY KEY,issue_key TEXT,
            park TEXT,attraction TEXT,target_at_utc TEXT,model_version TEXT,predicted REAL,actual REAL,error REAL,
            evaluated_at_utc TEXT,status TEXT,cohort TEXT)''')
        conn.execute('CREATE INDEX IF NOT EXISTS model_pending ON model_predictions(status,target_at_utc)')


def issue(conn,history,settings,park,now=None):
    migrate(conn); now=r.utc(now); local=now.tz_convert(r.JST)
    kinds=[('intraday',local.date(),[now.ceil('h')+pd.Timedelta(hours=i) for i in (1,2)])]
    if local.hour==21:
        tomorrow=(local+pd.Timedelta(days=1)).date()
        kinds.append(('previous_evening',tomorrow,[pd.Timestamp(tomorrow,tz=r.JST)+pd.Timedelta(hours=h) for h in range(9,21)]))
    made=0
    for kind,day,stamps in kinds:
        key=f'{park}|{local.date()}|{kind}'
        if conn.execute('SELECT 1 FROM model_issues WHERE issue_key=?',(key,)).fetchone(): continue
        training=models.prepare(history,now)
        if training.empty: continue
        names=sorted(training.attraction.unique())
        targets=[(name,r.iso(t)) for name in names for t in stamps if 9<=t.tz_convert(r.JST).hour<21 and t>now]
        if not targets: continue
        features={'training_rows':len(training),'training_days':int(training.date.nunique()),
            'training_max_local':str(training.datetime.max()),'policy':'verified; available before origin; no future weather/price/feedback',
            'versions':{v:models.source_fingerprint(v) for v in models.MODELS}}
        predictions={v:models.predict(v,training,settings,targets,now) for v in models.MODELS}
        # Compute first, then issue. No partly written issue can prevent a retry.
        with conn:
            conn.execute('INSERT OR IGNORE INTO model_issues VALUES (?,?,?,?,?,?,?)',
                (key,park,r.iso(now),str(day),kind,r.iso(now),json.dumps(features,sort_keys=True)))
            for version,values in predictions.items():
                for (name,target),value in values.items():
                    pk=hashlib.sha256(f'{key}|{version}|{name}|{target}'.encode()).hexdigest()
                    made+=conn.execute('INSERT OR IGNORE INTO model_predictions VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?,?)',
                        (pk,key,park,name,target,version,value,'pending','major' if name in settings['rides'] else 'nonmajor')).rowcount
    return made


def evaluate(conn,history,now=None):
    migrate(conn); now=r.utc(now); df=classify(history,now)
    if df.empty: return 0
    valid=df[df.eligible].copy(); count=0
    pending=pd.read_sql_query("SELECT * FROM model_predictions WHERE status='pending'",conn)
    with conn:
        for row in pending.to_dict('records'):
            target=r.utc(row['target_at_utc'])
            if target+pd.Timedelta(hours=1)>now: continue
            found=valid[(valid.park==row['park'])&(valid.attraction==row['attraction'])&valid.stamp.between(target,target+pd.Timedelta(hours=1),inclusive='left')]
            # Repeated five-minute records cannot create artificial coverage.
            if found.slot.nunique()<3:
                if target+pd.Timedelta(days=7)<now:
                    conn.execute("UPDATE model_predictions SET status='insufficient_actual' WHERE prediction_key=?",(row['prediction_key'],))
                continue
            actual=float(found.groupby('slot').wait_time.mean().mean())
            conn.execute("UPDATE model_predictions SET actual=?,error=?,status='evaluated',evaluated_at_utc=? WHERE prediction_key=? AND status='pending'",
                (actual,row['predicted']-actual,r.iso(now),row['prediction_key']))
            count+=1
    return count


def report(conn):
    migrate(conn)
    raw=pd.read_sql_query("SELECT p.*,i.generated_at_utc,i.kind FROM model_predictions p JOIN model_issues i USING(issue_key) WHERE p.status='evaluated'",conn)
    counts=dict(conn.execute('SELECT status,count(*) FROM model_predictions GROUP BY status').fetchall())
    if raw.empty: return {'counts':counts,'metrics':[],'paired_metrics':[],'adoption':models.adoption_gate({}),'limitation':'No evaluated prospective observations yet'}
    raw['target_day']=pd.to_datetime(raw.target_at_utc,utc=True).dt.tz_convert(r.JST).dt.strftime('%Y-%m-%d')
    raw['hour']=pd.to_datetime(raw.target_at_utc,utc=True).dt.tz_convert(r.JST).dt.hour
    lead=(pd.to_datetime(raw.target_at_utc,utc=True)-pd.to_datetime(raw.generated_at_utc,utc=True)).dt.total_seconds()/3600
    raw['lead_bucket']=pd.cut(lead,[-.001,3,12,24,48,1e6],labels=['0-3h','3-12h','12-24h','24-48h','48h+']).astype(str)
    def summarize(frame,dimensions):
        out=[]
        for keys,rows in frame.groupby(dimensions,observed=True):
            if not isinstance(keys,tuple): keys=(keys,)
            e=rows.predicted-rows.actual
            out.append({**dict(zip(dimensions,keys)),'n':len(rows),'independent_days':int(rows.target_day.nunique()),
                'MAE':float(e.abs().mean()),'RMSE':float((e.pow(2).mean())**.5),'Bias':float(e.mean())})
        return out
    key=['issue_key','attraction','target_at_utc']
    required={'legacy-corrected-v1','weekday-hour-median-v1','weighted-history-candidate-v1'}
    complete=raw.groupby(key).model_version.apply(lambda x:required<=set(x))
    matched=set(complete[complete].index)
    paired=raw[[tuple(x) in matched for x in raw[key].itertuples(index=False,name=None)]]
    evidence={}
    for (park,cohort),rows in paired.groupby(['park','cohort']):
        comparison=rows[rows.model_version.isin(['legacy-corrected-v1','weighted-history-candidate-v1'])].copy()
        comparison['absolute_error']=(comparison.predicted-comparison.actual).abs()
        daily=comparison.groupby(['target_day','model_version']).absolute_error.mean().unstack().dropna()
        from data_quality import coverage
        import utils
        quality=coverage(r.load_history(conn),park,utils.PARK_SETTINGS[park]['rides'])
        evaluated_names=set(rows.attraction)
        details=pd.DataFrame(quality.get('daily_attraction',[]))
        qualified=set()
        if len(details):
            for day,items in details.groupby('date'):
                required_names=set(utils.PARK_SETTINGS[park]['rides']) if cohort=='major' else evaluated_names
                ready=set(items.loc[items.evaluation_ready,'attraction'])
                if required_names and required_names<=ready: qualified.add(day)
        daily=daily[daily.index.isin(qualified)]
        days=len(daily); delta=daily['legacy-corrected-v1']-daily['weighted-history-candidate-v1']
        lower=None
        if days>=14:
            rng=np.random.default_rng(20261030)
            lower=float(np.quantile(rng.choice(delta.to_numpy(),size=(2000,days),replace=True).mean(axis=1),.05))
        periods=sum(float(part.mean())>0 for part in np.array_split(delta.to_numpy(),3) if len(part)) if days>=3 else 0
        base=float(daily['legacy-corrected-v1'].mean()) if days else 0
        all_points=raw[(raw.park==park)&(raw.cohort==cohort)]
        all_points=all_points[all_points.model_version.isin(required)][key].drop_duplicates()
        evidence.setdefault(park,{})[cohort]={'independent_days':days,'periods_improved':periods,
            'mae_improvement':float(delta.mean()/base) if base else 0,
            'paired_complete':len(rows[key].drop_duplicates())==len(all_points),
            'day_bootstrap_lower_bound_positive':lower is not None and lower>0,
            'day_bootstrap_lower_bound_minutes':lower,'coverage_qualified_dates':sorted(qualified)}
    return {'counts':counts,'metrics':summarize(raw,['park','cohort','model_version']),
        'paired_metrics':summarize(paired,['park','cohort','model_version']),
        'by_day':summarize(paired,['park','cohort','model_version','target_day']),
        'by_hour':summarize(paired,['park','cohort','model_version','hour']),
        'by_lead':summarize(paired,['park','cohort','model_version','lead_bucket']),
        'paired_points':len(matched),'promotion_evidence':evidence,'adoption':models.adoption_gate(evidence),
        'limitation':'Promotion remains manual and blocked until independent days, disjoint periods and day-level uncertainty have been reviewed. Unpaired metrics are descriptive only.'}
