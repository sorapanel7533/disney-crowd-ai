"""Bounded Actions worker. Real current observations only; persist after every sample."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import collector
import reliability as r


def next_slot(now):
    now=r.utc(now)
    import pandas as pd
    base=now.floor('h')
    return next(t for t in (base+pd.Timedelta(minutes=m) for m in (7,22,37,52,67)) if t>now)


def persist(directory):
    """Single writer shared by both workflows. Never force-push or rewrite history."""
    directory=Path(directory)
    def git(*args):
        return subprocess.run(['git','-C',str(directory),*args],check=True,capture_output=True,text=True)
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('add','--all')
    if not git('diff','--cached','--name-only').stdout.strip(): return False
    git('commit','-m','Record current observations, evaluations and operational evidence')
    # Bounded transient retry of this same append-only commit, no current-value backfill.
    for attempt in range(3):
        try: git('push','origin','HEAD:crowd-data');return True
        except subprocess.CalledProcessError:
            if attempt==2: raise RuntimeError('DB commit push failed; retained locally for artifact recovery') from None
            time.sleep(2**attempt)


def run(directory,minutes=0,context=False,force=False,push=False,clock=None,sleeper=time.sleep):
    clock=clock or (lambda:r.utc())
    start=clock();deadline=start+__import__('pandas').Timedelta(minutes=min(max(minutes,0),330))
    results=[];index=0;persist_failed=False
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    while True:
        now=clock();local=now.tz_convert(r.JST)
        if index and (now>=deadline or not 8<=local.hour<22): break
        os.environ['COLLECTOR_SAMPLE_ID']=f'{index:03d}'
        due=next_slot(now-__import__('pandas').Timedelta(minutes=15))
        try:
            sample=collector.collect(directory,force=force and index==0,context=context and index==0)
        except Exception as exc: sample=[{'status':'failed','failure_class':'db_or_processing','message':type(exc).__name__}]
        stamp=r.iso(clock());record={'sample':index,'started_at_utc':r.iso(now),'finished_at_utc':stamp,
            'due_slot_utc':r.iso(due),'event':os.environ.get('COLLECTOR_EVENT','local'),
            'github_run_id':os.environ.get('GITHUB_RUN_ID'),'results':sample}
        results.append(record)
        with (directory/'collection-attempts.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        (directory/'last-run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        if push:
            try: persist(directory)
            except RuntimeError: persist_failed=True
        index+=1
        if not minutes or not 8<=local.hour<22: break
        following=next_slot(clock())
        if following>=deadline: break
        # A bounded job sleep (not a Windows service), next sample uses its actual time.
        while clock()<following:
            sleeper(min(30,max(0,(following-clock()).total_seconds())))
    os.environ.pop('COLLECTOR_SAMPLE_ID',None)
    return {'samples':len(results),'failed_samples':sum(any(x.get('status')=='failed' for x in s['results']) for s in results),
            'persistence_failed':persist_failed,'started_at_utc':r.iso(start),'finished_at_utc':r.iso(clock())}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--minutes',type=int,default=0)
    p.add_argument('--context',action='store_true');p.add_argument('--force',action='store_true');p.add_argument('--push',action='store_true')
    a=p.parse_args();result=run(a.data_dir,a.minutes,a.context,a.force,a.push);print(json.dumps(result))
    raise SystemExit(1 if result['failed_samples'] or result['persistence_failed'] else 0)
