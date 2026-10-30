"""Bounded, weekly official factual menu survey. Never runs from a page visit."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser
import requests
import alcohol as a
import reliability as r
from persistence import snapshot
from data_journal import write_delta


class OfficialClient:
    def __init__(self,delay=2):
        self.session=requests.Session();self.delay=delay;self.previous=0;self.calls=0;self.failed=0
        self.session.headers['User-Agent']='DisneyCrowdAI/3.0 (+https://github.com/sorapanel7533/disney-crowd-ai; weekly factual menu survey)'
        self.robot=None

    def get(self,url):
        if not url.startswith(a.BASE+'/') or '/iframe/' in url or '/app/' in url: raise ValueError('Disallowed source')
        if self.calls>=100: raise ValueError('Weekly request limit reached')
        if self.failed>=3: raise RuntimeError('Source circuit open after three failures')
        if self.robot and not self.robot.can_fetch('DisneyCrowdAI',url): raise PermissionError('robots disallows source')
        time.sleep(max(0,self.delay-(time.monotonic()-self.previous)))
        self.previous=time.monotonic();self.calls+=1
        try:
            response=self.session.get(url,timeout=(5,15),allow_redirects=False)
            if response.status_code in (301,302,307,308):
                target=urljoin(url,response.headers.get('Location',''))
                if not target.startswith(a.BASE+'/') or not self.robot or not self.robot.can_fetch('DisneyCrowdAI',target):
                    raise ValueError('Unapproved official source redirect')
                self.calls+=1
                response=self.session.get(target,timeout=(5,15),allow_redirects=False)
            response.raise_for_status()
            if response.status_code!=200 or len(response.content)>2*1024*1024: raise ValueError('Unexpected response status/size')
            response.encoding='utf-8';return response.text
        except (requests.RequestException,ValueError):
            self.failed+=1
            raise RuntimeError('Official source HTTP/timeout/response failure') from None

    def check_robots(self):
        contents=self.get(a.BASE+'/robots.txt')
        self.robot=RobotFileParser();self.robot.parse(contents.splitlines())
        if not self.robot.can_fetch('DisneyCrowdAI',a.BASE+'/tdl/restaurant/list/'):
            raise PermissionError('robots disallows menu survey')


def survey(conn,client=None,force=False,now=None,run_id=None):
    now=r.iso(now);week=r.utc(now).tz_convert(r.JST).strftime('%G-W%V')
    history=a.records(conn,'alcohol_runs')
    # Initial browser verification does not suppress the first automated survey.
    existing=next((x for k,x in history.items() if k.startswith('weekly:') and x.get('week')==week and x['status']=='success'),None)
    if existing and not force: return existing
    run_id=run_id or 'weekly:'+week+':'+r.utc().strftime('%Y%m%dT%H%M%S%f')
    client=client or OfficialClient()
    try:
        client.check_robots()
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
        return a.apply_survey(conn,stores,menus,failures,now,run_id,force)
    except (ValueError,RuntimeError,PermissionError) as exc: return a.failure(conn,str(exc),now,run_id)


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
        before=sqlite3.connect(before_path)
        try: result['journal']=write_delta(before,conn,directory,'Alcohol',rid)
        finally: before.close()
    conn.close()
    (directory/'alcohol-last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=Path('.runtime'));parser.add_argument('--force',action='store_true')
    args=parser.parse_args();result=run(args.data_dir,args.force);print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(1 if result['status']=='failed' else 0)
