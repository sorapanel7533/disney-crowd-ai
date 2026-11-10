"""Versioned, data-change-only summaries; no automatic precision claim/adoption."""
import hashlib
import json
import os
import measured as m
import reliability as r
from measured_store import GitHubStore

def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(x) for x in value]
    if hasattr(value,'item'):return clean(value.item())
    if isinstance(value,float) and not __import__('math').isfinite(value):return None
    return value

def rebuild(db,now=None):
    now=r.iso(now);f=m.frame(db,as_of=now)
    source=sorted((x['id'],x['updated_at']) for x in m.records(db) if x['status']=='approved' and not x['is_test'])
    digest=hashlib.sha256(json.dumps([m.VERSION,source]).encode()).hexdigest()
    existing=db.execute('SELECT payload FROM measured_models WHERE id=?',(digest,)).fetchone()
    if existing:return json.loads(existing[0])
    stats,bins=m.statistics(f);e=m.evaluate(f);comparisons=[]
    if len(e):
        for fields in (['park','method'],['park','ride_id','method'],['park','weekday','method'],['park','hour','method']):
            for keys,g in e.groupby(fields):
                keys=keys if isinstance(keys,tuple) else (keys,)
                baseline=m.metrics(g.baseline_error);corrected=m.metrics(g.error)
                comparisons.append({**dict(zip(fields,keys)),'evaluation_days':g.date.nunique(),'baseline':baseline,'corrected':corrected,
                  'improvement_pct':(baseline['MAE']-corrected['MAE'])/baseline['MAE']*100 if baseline['MAE'] else None})
    model=clean({'id':digest,'version':m.VERSION,'created_at':now,'training_records':len(f),
        'training_days':f.date.nunique() if len(f) else 0,'attractions':f.ride_id.nunique() if len(f) else 0,
        'period':[str(f.date.min()),str(f.date.max())] if len(f) else None,'training_ids':[x[0] for x in source],
        'statistics':stats,'distribution':bins.to_dict('records'),'comparisons':comparisons,
        'evaluation_days':e.date.nunique() if len(e) else 0,'adopted':False,
        'decision':'検証済みとして未採用。独立した複数日・施設・季節の実測誤差が不足。暫定統計のみ。',
        'base_display_model':r.MODEL_VERSION,'training_policy':'approved non-test records available as-of issue; daily walk-forward'})
    with db:db.execute('INSERT INTO measured_models VALUES (?,?)',(digest,json.dumps(model,ensure_ascii=False,allow_nan=False)))
    return model

def main():
    backend=GitHubStore(os.environ.get('GITHUB_TOKEN',''))
    db,head,_=backend.load()
    try:
        if not head:print('No submissions database; skip');return
        n=db.execute('SELECT COUNT(*) FROM measured_models').fetchone()[0]
        report=rebuild(db)
        if db.execute('SELECT COUNT(*) FROM measured_models').fetchone()[0]==n:
            print('Data unchanged; no write or retraining');return
    finally:db.close()
    def verify(db,result):return db.execute('SELECT 1 FROM measured_models WHERE id=?',(result['id'],)).fetchone() is not None
    result,commit=backend.transact(rebuild,verify)
    print(json.dumps({'model':result['id'],'training_records':result['training_records'],'evaluation_days':result['evaluation_days'],'adopted':False,'commit':commit}))

if __name__=='__main__':main()
