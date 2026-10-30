"""Bounded, weekly official factual menu survey. Never runs from a page visit."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import alcohol as a
import reliability as r
from persistence import snapshot
from data_journal import write_delta


from official_source import OfficialClient


def survey(conn,client=None,force=False,now=None,run_id=None):
    real_clock=now is None
    now=r.iso(now);week=r.utc(now).tz_convert(r.JST).strftime('%G-W%V')
    history=a.records(conn,'alcohol_runs')
    # Initial browser verification does not suppress the first automated survey.
    this_week=[x for k,x in history.items() if k.startswith('weekly:'+week)]
    existing=max((x for x in this_week if x['status']=='success'),key=lambda x:x['finished_at_utc'],default=None)
    latest=max(this_week,key=lambda x:x['finished_at_utc'],default=None)
    if existing and latest['finished_at_utc']<=existing['finished_at_utc'] and not force:
        return {**existing,'skipped_same_week':True,'collection_performed':False,
                'source_requests_this_attempt':0,'attempt_finished_at_utc':r.iso() if real_clock else now}
    run_id=run_id or 'weekly:'+week+':'+r.utc().strftime('%Y%m%dT%H%M%S%f')
    own_client=client is None
    client=client or OfficialClient()
    def recorded(result):
        if hasattr(client,'summary'):
            result={**result,'source':client.summary(),'collection_performed':True,
                    'source_requests_this_attempt':client.calls,
                    'execution_environment':'github_actions' if os.environ.get('GITHUB_ACTIONS')=='true' else 'local'}
            with conn:a.put(conn,'alcohol_runs',result['run_id'],result,r.iso())
        return result
    try:
        client.check_robots()
        client.check_terms()
        stores=[]
        for park in a.PARKS: stores.extend(a.parse_stores(client.get(a.BASE+f'/{park}/restaurant/list/'),park))
        previous=a.records(conn,'alcohol_stores')
        for park in a.PARKS.values():
            old=sum(x['park']==park for x in previous.values());new=sum(x['park']==park for x in stores)
            if old and new<old*.8: raise ValueError('Abnormal store-list decrease; previous catalog retained')
        menus={};failures={}
        for store in stores:
            sid=store['store_id']
            try:
                if not store['menu_url']: raise ValueError('Official menu link unavailable')
                menus[sid]=a.parse_menu(client.get(store['menu_url']),store)
            except (ValueError,RuntimeError,PermissionError) as exc: failures[sid]=str(exc)
        return recorded(a.apply_survey(conn,stores,menus,failures,r.iso() if real_clock else now,run_id,force))
    except (ValueError,RuntimeError,PermissionError,OSError) as exc: return recorded(a.failure(conn,str(exc),r.iso() if real_clock else now,run_id))
    finally:
        if own_client:client.close()


def run(directory,force=False):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    conn=a.connect(directory/'alcohol.db');a.bootstrap(conn)
    rid='alcohol-'+os.environ.get('GITHUB_RUN_ID',r.utc().strftime('%Y%m%dT%H%M%S%f'))+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        before_path=Path(temp)/'before.db';snapshot(conn,before_path)
        result=survey(conn,force=force)
        if os.environ.get('ADMIN_ALCOHOL_NOTE','').strip():
            import alcohol_admin
            try: result['admin_acknowledged']=alcohol_admin.apply(conn,json.loads(os.environ['ADMIN_ALCOHOL_NOTE']))
            except (ValueError,PermissionError,TypeError):
                result['admin_error']='Invalid administrator note; no annotation applied'
                result['status']='failed'
        # The first bootstrap is already in the DB before a weekly attempt.
        # Keep one complete, independently replayable catalog checkpoint too.
        manifest=directory/'alcohol-checkpoint.json'
        if not manifest.exists():
            empty=a.connect(Path(temp)/'empty.db')
            try: checkpoint=write_delta(empty,conn,directory,'Alcohol',rid+'-checkpoint')
            finally: empty.close()
            manifest.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding='utf-8')
            result['checkpoint']=checkpoint
        before=sqlite3.connect(before_path)
        try: result['journal']=write_delta(before,conn,directory,'Alcohol',rid)
        finally: before.close()
    conn.close()
    (directory/'alcohol-last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=Path('.runtime'));parser.add_argument('--force',action='store_true')
    args=parser.parse_args();result=run(args.data_dir,args.force)
    summary={key:result[key] for key in ('run_id','status','finished_at_utc','attempt_finished_at_utc','collection_performed','skipped_same_week','source_requests_this_attempt','target_stores','successful_stores','stores_with_products','products','priced_products','dated_products','unconfirmed_stores','diffs','reason','journal') if key in result}
    print(json.dumps(summary,ensure_ascii=False))
    raise SystemExit(1 if result['status']=='failed' else 0)
