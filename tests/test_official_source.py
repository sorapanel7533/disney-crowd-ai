import json
from pathlib import Path
import requests
import pytest
import alcohol as a
from alcohol_collector import survey
from official_source import BASE,OfficialClient,legal_fingerprint

ROBOTS='User-agent: *\nDisallow: /iframe/\nDisallow: /app/\n'
LEGAL='<div id="area0"><h1>法的制約と使用条件</h1><h2>コンテンツの使用制限</h2><p>検証用の使用条件</p></div>'


def response(body,status=200,location=None):
    r=requests.Response();r.status_code=status;r._content=body.encode();r.encoding='utf-8';r.url=BASE+'/robots.txt'
    if location:r.headers['Location']=location
    return r


@pytest.fixture
def client(tmp_path):
    p=tmp_path/'policy.json';p.write_text(json.dumps({'legal_main_sha256':legal_fingerprint(LEGAL),'scope':'test facts only'}),encoding='utf-8')
    c=OfficialClient(delay=0,policy_file=p);yield c;c.close()


def test_timeout_retry_retains_crawler_identity_without_leaking_exception(client,monkeypatch):
    agents=[]
    def get(*args,**kwargs):
        agents.append(client.session.headers['User-Agent'])
        raise requests.ReadTimeout('secret diagnostic content')
    monkeypatch.setattr(client.session,'get',get)
    with pytest.raises(RuntimeError,match='official_source_timeout: /robots.txt') as exc:client.check_robots()
    assert 'secret' not in str(exc.value) and agents==['DisneyCrowdAI/3.1']*2
    assert client.summary()['failed_requests']==2


def test_transient_server_error_recovers_with_one_retry_and_audit(client,monkeypatch):
    replies=iter([response('temporary',503),response(ROBOTS)])
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:next(replies))
    client.check_robots()
    assert client.calls==2 and client.failed==1
    assert [x['http_status'] for x in client.audit]==[503,200]
    assert client.policy['robots']['sha256']


@pytest.mark.parametrize('status',[401,403,429])
def test_denied_or_rate_limited_source_is_never_retried_or_bypassed(client,monkeypatch,status):
    calls=[]
    def get(*args,**kwargs):calls.append(args[0]);return response('denied',status)
    monkeypatch.setattr(client.session,'get',get)
    with pytest.raises(PermissionError):client.check_robots()
    with pytest.raises(PermissionError):client.get(BASE+'/robots.txt')
    assert len(calls)==1 and client.failed==1


@pytest.mark.parametrize('source',['<html>User-agent: * fake challenge</html>','User-agent: *\nDisallow: /\n','User-agent: *\nDisallow: /private/*\n'])
def test_html_denial_and_unsupported_robots_stop_before_menu(client,monkeypatch,source):
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:response(source))
    with pytest.raises((PermissionError,ValueError)):client.check_robots()
    assert client.calls==1


def test_robots_path_and_delay_are_enforced_before_requests(client,monkeypatch):
    source=ROBOTS+'Disallow: /tdl/restaurant/food/\nCrawl-delay: 2\n'
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:response(source))
    client.check_robots()
    assert client.delay==2
    with pytest.raises(PermissionError):client.get(BASE+'/tdl/restaurant/food/300/')
    assert client.calls==1


def test_redirect_must_stay_on_verified_official_public_sources(client,monkeypatch):
    replies=iter([response(ROBOTS),response('',302,'/tdl/restaurant/list.html'),response('<h1>list</h1>'),response('',302,'https://other.example/robots.txt')])
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:next(replies))
    client.check_robots()
    assert 'list' in client.get(BASE+'/tdl/restaurant/list/')
    with pytest.raises(ValueError,match='redirect'):client.get(BASE+'/tds/restaurant/list/')
    assert client.calls==4


def menu_list(park):
    return '<label for="searchTag6006">アルコール提供店舗</label>'+f'<li data-tags=\'["6006"]\'><a href="/{park}/restaurant/detail/999/"><p class="area">検証エリア</p><h3>検証用レストラン</h3></a><a href="/{park}/restaurant/food/999/">メニュー</a></li>'


def wire_survey(client,monkeypatch,legal=LEGAL):
    menu=(Path(__file__).parent/'fixtures'/'alcohol-menu.html').read_text(encoding='utf-8')
    def get(url,**kwargs):
        if url.endswith('/robots.txt'):return response(ROBOTS)
        if url.endswith('/legal.html'):return response(legal)
        if url.endswith('/list/'):return response(menu_list('tdl' if '/tdl/' in url else 'tds'))
        return response(menu)
    monkeypatch.setattr(client.session,'get',get)


def test_real_survey_protocol_success_duplicate_week_and_retry_after_failure(client,monkeypatch,tmp_path):
    wire_survey(client,monkeypatch);conn=a.connect(tmp_path/'catalog.db')
    first=survey(conn,client,now='2026-10-30T16:00:00Z',run_id='weekly:2026-W44:first')
    assert first['status']=='success' and first['successful_stores']==2
    assert first['source']['requests']==6 and first['source']['failed_requests']==0
    assert set(first['source']['policy'])=={'robots','terms'}
    calls=client.calls
    skipped=survey(conn,client,now='2026-10-30T16:10:00Z')
    assert skipped['skipped_same_week'] and skipped['source_requests_this_attempt']==0 and client.calls==calls
    assert a.last_success(conn)['finished_at_utc']==first['finished_at_utc']
    a.failure(conn,'test later forced source failure','2026-10-30T16:20:00Z','weekly:2026-W44:failed')
    retried=survey(conn,client,now='2026-10-30T16:30:00Z',run_id='weekly:2026-W44:retry')
    assert retried['status']=='success' and client.calls>calls
    assert len(a.records(conn,'alcohol_products'))==5
    assert a.freshness(conn,'2026-10-30T17:00:00Z')['catch_up_scheduled_jst'] is None
    conn.close()


def test_changed_terms_preserve_all_previous_catalog_rows(client,monkeypatch,tmp_path):
    wire_survey(client,monkeypatch);conn=a.connect(tmp_path/'catalog.db')
    first=survey(conn,client,now='2026-10-30T16:00:00Z',run_id='weekly:2026-W44:first')
    original=a.records(conn,'alcohol_sales')
    wire_survey(client,monkeypatch,LEGAL.replace('検証用の使用条件','変更された条件'))
    failed=survey(conn,client,force=True,now='2026-10-30T16:30:00Z',run_id='weekly:2026-W44:terms-changed')
    assert failed['status']=='failed' and 'terms changed' in failed['reason']
    assert a.records(conn,'alcohol_sales')==original
    assert a.last_success(conn)['run_id']==first['run_id']
    conn.close()
