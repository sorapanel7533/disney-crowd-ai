"""Read-through durable snapshots from the public data branch, merge without loss."""
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import time
import requests

BASE = 'https://raw.githubusercontent.com/sorapanel7533/disney-crowd-ai/crowd-data/'


def snapshot(conn, destination):
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(destination) as target:
        conn.backup(target)
        result=target.execute('PRAGMA integrity_check').fetchone()[0]
        if result!='ok': raise RuntimeError(result)


def merge_snapshot(conn, path):
    """Insert rows by semantic keys; preserve local observations and manual history."""
    source=sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro',uri=True)
    if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
        source.close(); raise ValueError('Snapshot integrity check failed')
    source.row_factory=sqlite3.Row
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    incoming={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    saved=0
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            for table in sorted(tables & incoming):
                cols={row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
                for row in source.execute(f'SELECT * FROM "{table}"'):
                    record={key:row[key] for key in row.keys() if key in cols}
                    key=next((k for k in ('observation_key','prediction_key','forecast_key','run_key') if record.get(k)),None)
                    if key:
                        existing=conn.execute(f'SELECT rowid FROM "{table}" WHERE "{key}"=?',(record[key],)).fetchone()
                        if existing:
                            # Only evaluation fields may be filled; immutable forecast stays untouched.
                            updates={k:record[k] for k in ('actual_wait','actual_crowd_index','error','evaluated_at_utc','evaluation_status') if record.get(k) is not None}
                            if updates and (record.get('evaluation_status')=='evaluated' or record.get('actual_crowd_index') is not None):
                                assignments=','.join(f'"{k}"=?' for k in updates)
                                conn.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid=?',(*updates.values(),existing[0]))
                            continue
                    else:
                        where=' AND '.join(f'"{k}" IS ?' for k in record)
                        if conn.execute(f'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1',tuple(record.values())).fetchone():
                            continue
                    names=','.join(f'"{c}"' for c in record)
                    values=','.join('?' for _ in record)
                    saved+=conn.execute(f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({values})',tuple(record.values())).rowcount
    finally:
        source.close()
    return saved


def sync_public_snapshot(conn, db_name, directory, ttl=900):
    if os.environ.get('DISNEY_DISABLE_REMOTE_SYNC')=='1':
        return '同期無効（テスト/収集環境）'
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    marker=directory/(Path(db_name).stem+'-sync.json')
    if marker.exists():
        try:
            state=json.loads(marker.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            state={}
        retry_ttl=ttl if state.get('status')=='success' else min(ttl,60)
        if time.time()-state.get('checked',0)<retry_ttl:
            return state['message']
    try:
        # Data branch contains public park observations only, never secrets/config.
        response=requests.get(BASE+Path(db_name).name,timeout=(3,12))
        response.raise_for_status()
        if len(response.content)>100*1024*1024:
            raise ValueError('Snapshot exceeds 100 MiB; partition storage before continuing')
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            path=Path(tmp)/'incoming.db'; path.write_bytes(response.content)
            count=merge_snapshot(conn,path)
        message=f'永続スナップショット同期成功: {count}行を追加'
        status='success'
    except (requests.RequestException,ValueError,sqlite3.Error,OSError) as exc:
        message=f'永続保存先の同期失敗。ローカル履歴を保持: {exc}'
        status='failed'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=directory,delete=False) as status_file:
        json.dump({'checked':time.time(),'message':message,'status':status},status_file,ensure_ascii=False)
        status_path=status_file.name
    os.replace(status_path,marker)
    return message
