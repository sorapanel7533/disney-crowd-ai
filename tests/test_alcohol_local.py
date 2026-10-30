import hashlib
import json
from pathlib import Path
import sqlite3
import pytest
import alcohol as a
import alcohol_collector
from alcohol_local import prepare


@pytest.mark.parametrize('status',['success','failed'])
def test_local_publication_preserves_source_history_admin_notes_and_replay_files(tmp_path,monkeypatch,status):
    source=tmp_path/'durable';source.mkdir()
    conn=a.connect(source/'alcohol.db');a.bootstrap(conn)
    key=next(iter(a.records(conn,'alcohol_sales')))
    a.annotate(conn,key,'検証用の管理者補足',authorized=True)
    original={table:a.records(conn,table) for table in a.TABLES};conn.close()
    checksum=hashlib.sha256((source/'alcohol.db').read_bytes()).hexdigest()
    store={'store_id':'tdl:999','park':'Disneyland','name':'検証用レストラン','area':'検証用エリア',
        'url':a.BASE+'/tdl/restaurant/detail/999/','menu_url':a.BASE+'/tdl/restaurant/food/999/'}
    menu=(Path(__file__).parent/'fixtures'/'alcohol-menu.html').read_text(encoding='utf-8')
    def survey(conn,force=False):
        if status=='failed':return a.failure(conn,'test source timeout',run_id='weekly:2026-W44:test')
        return a.apply_survey(conn,[store],{store['store_id']:a.parse_menu(menu,store)},{},run_id='weekly:2026-W44:test')
    monkeypatch.setattr(alcohol_collector,'survey',survey)
    target=tmp_path/'prepared'
    manifest=prepare(source,target,'a'*40)
    assert manifest['status']==status and not any(manifest['previous_keys_missing'].values())
    assert hashlib.sha256((source/'alcohol.db').read_bytes()).hexdigest()==checksum
    allowed={'alcohol.db','alcohol-last-run.json','alcohol-checkpoint.json'}
    assert all(x['path'] in allowed or x['path'].startswith('journal/') and x['path'].endswith('-Alcohol.jsonl.gz') for x in manifest['files'])
    assert not any('before' in x['path'] or 'disneyland.db' in x['path'] for x in manifest['files'])
    published=a.connect(target/'published-alcohol.db')
    assert published.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for table,records in original.items():assert records.keys()<=a.records(published,table).keys()
    assert a.records(published,'alcohol_overrides')==original['alcohol_overrides']
    if status=='failed':assert a.records(published,'alcohol_sales')==original['alcohol_sales']
    last=json.loads((target/'alcohol-last-run.json').read_text(encoding='utf-8'))
    assert last['publication']=={'execution_environment':'local','base_sha':'a'*40}
    assert '\\' not in last['journal']['path']
    for item in manifest['files']:
        raw=(target/item['local_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==item['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==item['git_blob_sha']
    published.close()
