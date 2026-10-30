"""Integrity-checked recovery from retained Git snapshots, preserving corrupt bytes."""
import gzip
from pathlib import Path
import sqlite3
import subprocess
import reliability as r


def valid(path):
    try:
        con=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)
        try: return con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        finally: con.close()
    except (sqlite3.Error,OSError): return False


def recover(path,repository,limit=5):
    path=Path(path); repository=Path(repository)
    if not path.exists() or valid(path): return {'status':'unchanged'}
    commits=subprocess.run(['git','log',f'-{limit+1}','--format=%H','--',path.name],cwd=repository,capture_output=True,text=True,check=True).stdout.splitlines()
    for commit in commits:
        result=subprocess.run(['git','show',f'{commit}:{path.name}'],cwd=repository,capture_output=True)
        if result.returncode: continue
        candidate=repository/(path.name+'.recovery-candidate'); candidate.write_bytes(result.stdout)
        if valid(candidate):
            archive=repository/'recovery'/f'{r.utc().strftime("%Y%m%dT%H%M%S")}-{path.name}.corrupt.gz'
            archive.parent.mkdir(exist_ok=True); archive.write_bytes(gzip.compress(path.read_bytes(),mtime=0))
            candidate.replace(path)
            return {'status':'recovered','commit':commit,'preserved_corrupt':str(archive.relative_to(repository))}
        candidate.unlink()
    raise RuntimeError(f'No intact recent backup for {path.name}; collection stopped without overwriting original')


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path)
    p.add_argument('--only',action='append',choices=['disneyland.db','disneysea.db','alcohol.db']);args=p.parse_args()
    print(json.dumps({name:recover(args.directory/name,args.directory) for name in (args.only or ['disneyland.db','disneysea.db','alcohol.db'])},ensure_ascii=False))
