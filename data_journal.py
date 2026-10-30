"""Small immutable row deltas beside live SQLite; no history is pruned."""
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import reliability as r

TABLES={'wait_times','predictions','daily_crowd_predictions','dpa_sellouts','dpa_fetch_logs','data_fetch_logs',
    'weather_snapshots','ticket_price_snapshots','attraction_status_snapshots','park_hours','event_signals',
    'show_schedules','show_wait_context','historical_import_logs','collection_runs','dpa_revisions',
    'operation_runs','source_checks','model_issues','model_predictions','admin_events','history_candidates'}
# Storage transport must not import the optional menu parser: a menu failure
# must never prevent park collection. Only these explicit envelopes are replayed.
TABLES |= {'alcohol_products','alcohol_stores','alcohol_sales','alcohol_periods','alcohol_prices',
           'alcohol_runs','alcohol_sources','alcohol_diffs','alcohol_overrides'}


def rows(conn,table):
    conn.row_factory=sqlite3.Row
    return [dict(x) for x in conn.execute(f'SELECT * FROM "{table}"')]


def write_delta(before,after,destination,park,run_id):
    before_tables={x[0] for x in before.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    after_tables={x[0] for x in after.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    changes=[]
    for table in sorted(TABLES & after_tables):
        previous={json.dumps(x,sort_keys=True,ensure_ascii=False) for x in rows(before,table)} if table in before_tables else set()
        for row in rows(after,table):
            if json.dumps(row,sort_keys=True,ensure_ascii=False) not in previous:
                changes.append({'table':table,'row':row})
    # Includes changed evaluation/acknowledgement rows, not just new observations.
    content=('\n'.join(json.dumps(x,sort_keys=True,ensure_ascii=False) for x in
        [{'format':'disney-row-delta-v1','park':park,'run_id':str(run_id),'created_at_utc':r.iso()}]+changes)+'\n').encode()
    now=r.utc(); path=Path(destination)/'journal'/now.strftime('%Y-%m')/f'{run_id}-{park}.jsonl.gz'
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise ValueError('Journal is immutable; run ID already exists')
    path.write_bytes(gzip.compress(content,mtime=0))
    return {'path':str(path.relative_to(destination)),'changes':len(changes),'compressed_bytes':path.stat().st_size,
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def replay(conn,path):
    """Rebuild a snapshot without SQL supplied by the journal. No delete operation."""
    from persistence import merge_snapshot
    import tempfile
    items=[json.loads(x) for x in gzip.decompress(Path(path).read_bytes()).decode().splitlines()]
    if not items or items[0].get('format')!='disney-row-delta-v1': raise ValueError('Unknown journal format')
    with tempfile.TemporaryDirectory(dir=Path(path).parent) as folder:
        db=Path(folder)/'delta.db'; target=sqlite3.connect(db)
        for table in sorted({x['table'] for x in items[1:]}):
            if table not in TABLES: raise ValueError('Unknown journal table')
            sql=conn.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()
            if not sql: raise ValueError('Initialize current schema before replay')
            target.execute(sql[0])
        for item in items[1:]:
            table=item['table']; record=item['row']
            allowed={x[1] for x in target.execute(f'PRAGMA table_info("{table}")')}
            if not set(record)<=allowed: raise ValueError('Unknown journal column')
            cols=','.join(f'"{x}"' for x in record); marks=','.join('?' for _ in record)
            target.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})',tuple(record.values()))
        target.commit(); target.close()
        return merge_snapshot(conn,db)
