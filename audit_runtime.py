"""Repeatable read-only audit, also run after the scheduled collector."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import requests
import reliability as r
import utils
from data_quality import coverage
from operations import schedule_audit


def audit(directory,runs=None,now=None):
    directory=Path(directory); now=r.utc(now); result={'as_of_utc':r.iso(now),'parks':{},'storage':{}}
    total=0
    for park,settings in utils.PARK_SETTINGS.items():
        path=directory/settings['db']; con=sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True)
        frame=r.load_history(con)
        result['parks'][park]={'integrity':con.execute('PRAGMA integrity_check').fetchone()[0],
            'db_bytes':path.stat().st_size,'quality':coverage(frame,park,settings['rides'],now),
            'fixed_forecasts':[dict(zip(['target_date','generated_at_utc','forecast_kind','model_version','predicted_crowd_index','actual_crowd_index'],row)) for row in con.execute('SELECT target_date,generated_at_utc,forecast_kind,model_version,predicted_crowd_index,actual_crowd_index FROM daily_crowd_predictions WHERE generated_at_utc IS NOT NULL')],
            'collection_attempts':dict(con.execute('SELECT status,count(*) FROM collection_runs GROUP BY status').fetchall())}
        total+=path.stat().st_size; con.close()
    journals=list((directory/'journal').rglob('*.gz'))
    result['storage']={'current_db_bytes':total,'journal_files':len(journals),'journal_bytes':sum(x.stat().st_size for x in journals),
        'planned_runs_per_day':56,'raw_full_snapshot_upper_bound_bytes_per_day':total*56,
        'raw_full_snapshot_upper_bound_bytes_per_30days':total*56*30,
        'warning':'Git delta compression is not guaranteed. This is an uncompressed full-snapshot upper bound, not measured Git growth.',
        'partition_required':total>=20*1024*1024,'partition_warning_threshold_bytes':20*1024*1024,
        'retention_policy':'No destructive prune. Immutable row journals plus original DB and retained Git snapshots. Plan monthly partitions before threshold.'}
    try:
        result['storage']['git_object_statistics']=subprocess.run(['git','count-objects','-v'],cwd=directory,capture_output=True,text=True,check=True).stdout.strip()
        commits=subprocess.run(['git','log','--format=%H %cI','--','*.db'],cwd=directory,capture_output=True,text=True,check=True).stdout.splitlines()
        result['storage']['db_commit_history']=commits[:100]
        sizes=[]
        for line in commits[:10]:
            sha=line.split()[0]
            tree=subprocess.run(['git','ls-tree','-l',sha,'--','disneyland.db','disneysea.db'],cwd=directory,capture_output=True,text=True,check=True).stdout.splitlines()
            sizes.append({'commit':sha,'raw_db_bytes':sum(int(x.split()[3]) for x in tree)})
        result['storage']['recent_snapshot_sizes']=sizes
    except (subprocess.CalledProcessError,OSError): result['storage']['git_object_statistics']='unavailable'
    if runs is not None:
        relevant=[x for x in runs if x.get('name','Collect park observations')=='Collect park observations']
        first=min((r.utc(x['created_at']) for x in relevant),default=now)
        result['schedule']=schedule_audit(relevant,first,now)
        result['schedule']['executed_runs']=[{k:x.get(k) for k in ['id','event','created_at','updated_at','status','conclusion','html_url']} for x in relevant]
        result['schedule']['wall_seconds']=[(r.utc(x['updated_at'])-r.utc(x.get('run_started_at') or x['created_at'])).total_seconds() for x in relevant if x.get('status')=='completed']
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--runs',type=Path);p.add_argument('--output',type=Path);a=p.parse_args()
    runs=json.loads(a.runs.read_text(encoding='utf-8')) if a.runs else None
    schedule_error=None
    if runs is None and os.environ.get('GITHUB_TOKEN'):
        try:
            response=requests.get('https://api.github.com/repos/sorapanel7533/disney-crowd-ai/actions/runs',params={'per_page':100},headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'},timeout=(3,10))
            response.raise_for_status();runs=response.json()['workflow_runs']
        except requests.RequestException:
            schedule_error='Actions API audit unavailable; no schedule success inferred'
    report=audit(a.data_dir,runs)
    if schedule_error: report['schedule_error']=schedule_error
    output=a.output or a.data_dir/'runtime-audit.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'as_of_utc':report['as_of_utc'],'db_bytes':report['storage']['current_db_bytes'],'partition_required':report['storage']['partition_required'],'schedule_not_observed':report.get('schedule',{}).get('not_observed_slots')},ensure_ascii=False))
