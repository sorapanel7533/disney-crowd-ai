"""Recover an isolated new SQLite from a verified immutable compressed snapshot.

Never edits a production database or branch. Verify before owner publication.
"""
import argparse
import gzip
import json
from pathlib import Path
import measured as m

def restore(checkpoint,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('復旧先には新しいファイルを指定してください')
    raw=gzip.decompress(Path(checkpoint).read_bytes())
    if len(raw)>40*1024*1024:raise ValueError('Checkpoint too large')
    value=json.loads(raw)
    if value.get('format')!='measured-snapshot-v1':raise ValueError('Unknown checkpoint format')
    db=m.connect(destination)
    try:
        with db:
            for row in value['measurements']:db.execute('INSERT INTO measurements VALUES (?,?)',(row['id'],json.dumps(row,ensure_ascii=False)))
            for row in value['audit']:db.execute('INSERT INTO measurement_audit VALUES (?,?,?,?)',tuple(row[k] for k in ('id','record_id','at','payload')))
            for row in value['models']:db.execute('INSERT INTO measured_models VALUES (?,?)',(row['id'],row['payload']))
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        return len(m.records(db))
    finally:db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('checkpoint');parser.add_argument('new_database')
    args=parser.parse_args();print('Restored records:',restore(args.checkpoint,args.new_database))
