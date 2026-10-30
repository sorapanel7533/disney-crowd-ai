"""Prepare a reviewed local survey for an atomic, lease-checked data-branch update.

This does not install a scheduler, hold credentials, or push to GitHub.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def prepare(source_directory,output_directory,base_sha):
    from alcohol_collector import run
    from persistence import snapshot
    import alcohol as a
    if not re.fullmatch('[0-9a-f]{40}',base_sha):raise ValueError('A verified data-branch commit is required')
    source=Path(source_directory).resolve();target=Path(output_directory).resolve()
    if not (source/'alcohol.db').is_file():raise ValueError('Existing durable alcohol database is required')
    if target.exists():raise ValueError('Use a new output directory; existing files are never replaced')
    conn=sqlite3.connect((source/'alcohol.db').as_uri()+'?mode=ro',uri=True)
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Source database integrity failed')
        previous={table:{row[0] for row in conn.execute(f'SELECT record_key FROM "{table}"')} for table in a.TABLES}
        target.mkdir(parents=True)
        snapshot(conn,target/'alcohol.db')
    finally:conn.close()
    checkpoint=source/'alcohol-checkpoint.json'
    if checkpoint.is_file():(target/checkpoint.name).write_bytes(checkpoint.read_bytes())
    result=run(target)
    conn=a.connect(target/'alcohol.db')
    try:
        missing={table:len(keys-a.records(conn,table).keys()) for table,keys in previous.items()}
        if any(missing.values()):raise ValueError('Previous catalog/history keys were lost; publication stopped')
        snapshot(conn,target/'published-alcohol.db')
    finally:conn.close()
    files=[('alcohol.db','published-alcohol.db'),('alcohol-last-run.json','alcohol-last-run.json')]
    for name in ('journal','checkpoint'):
        if result.get(name):
            path=Path(result[name]['path']).as_posix()
            if not re.fullmatch(r'journal/\d{4}-\d{2}/alcohol-[A-Za-z0-9_-]+-Alcohol\.jsonl\.gz',path):
                raise ValueError('Unexpected publication journal path')
            result[name]['path']=path
            files.append((path,path))
    if result.get('checkpoint'):
        (target/'alcohol-checkpoint.json').write_text(json.dumps(result['checkpoint'],ensure_ascii=False,indent=2),encoding='utf-8')
        files.append(('alcohol-checkpoint.json','alcohol-checkpoint.json'))
    result['publication']={'execution_environment':'local','base_sha':base_sha}
    (target/'alcohol-last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest=[]
    for remote,local in files:
        body=(target/local).read_bytes()
        manifest.append({'path':remote,'local_path':local,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),
            'git_blob_sha':hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()})
    value={'format':'alcohol-local-publication-v1','base_sha':base_sha,'execution_environment':'local',
           'status':result['status'],'previous_keys_missing':missing,'files':manifest,'result':result}
    (target/'publication.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-directory',type=Path,required=True)
    parser.add_argument('--output-directory',type=Path,required=True)
    parser.add_argument('--base-sha',required=True)
    args=parser.parse_args();result=prepare(args.source_directory,args.output_directory,args.base_sha)
    print(json.dumps({key:result[key] for key in ('format','base_sha','status','previous_keys_missing','files')},ensure_ascii=False))
    raise SystemExit(1 if result['status']=='failed' else 0)
