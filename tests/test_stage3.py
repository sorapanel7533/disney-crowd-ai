import json
from pathlib import Path
import sqlite3
import pandas as pd
import pytest
import alcohol as a
import alcohol_admin
from alcohol_collector import survey
import collection_session as session
import reliability as r
import model_registry as models
from operations import observation_audit

FIXTURE=Path(__file__).parent/'fixtures'/'alcohol-menu.html'
NOW='2026-10-31T01:00:00Z'


@pytest.fixture
def store():
    return {'store_id':'tdl:999','park':'Disneyland','name':'検証用レストラン','area':'検証用エリア',
            'url':a.BASE+'/tdl/restaurant/detail/999/','menu_url':a.BASE+'/tdl/restaurant/food/999/'}


@pytest.fixture
def con(tmp_path):
    conn=a.connect(tmp_path/'alcohol.db');yield conn;conn.close()


def data(store):
    return a.parse_menu(FIXTURE.read_text(encoding='utf-8'),store)


def save(con,store,rows=None,now=NOW,run='first'):
    return a.apply_survey(con,[store],{store['store_id']:data(store) if rows is None else rows},{},now,run)


def test_official_normal_table_and_product_badge_prices_and_non_alcohol_exclusion(store):
    rows=data(store)
    assert len(rows)==3
    food=next(x for x in rows if x['product_id']=='food:90001')
    assert (food['price'],food['start'],food['end'],food['tax_included'])==(1300,'2026-11-01','2026-12-25',True)
    multiple=next(x for x in rows if 'ボトル' in x['name'])
    assert multiple['price'] is None and '4,500' in multiple['price_text']
    assert not any('ノンアルコール' in x['name'] or 'カクテル風' in x['name'] for x in rows)


@pytest.mark.parametrize('source',['<h1>別店舗</h1>','<h1>検証用レストラン</h1><p>temporary error</p>'])
def test_structure_change_or_wrong_store_is_not_empty_success(store,source):
    with pytest.raises(ValueError): a.parse_menu(source,store)


def test_official_store_list_tag_and_hotel_exclusion():
    source='<label for="searchTag6006">アルコール提供店舗</label>'
    source+=''.join(f'<li data-tags=\'["6006"]\'><a href="/{park}/restaurant/detail/{i}/"><p class="area">{area}</p><h3>店{i}</h3></a><a href="/{park}/restaurant/food/{i}/">メニュー</a></li>' for i,park,area in [(1,'tdl','ワールドバザール'),(2,'tdl','パーク外'),(3,'tds','港')])
    rows=a.parse_stores(source,'tdl')
    assert len(rows)==1 and rows[0]['store_id']=='tdl:1'
    with pytest.raises(ValueError): a.parse_stores('<h1>レストラン一覧</h1>','tdl')


@pytest.mark.parametrize('date,state',[('2026-10-31','販売開始前'),('2026-11-01','公式掲載上の販売期間内'),('2026-12-25','公式掲載上の販売期間内'),('2026-12-26','期間終了')])
def test_published_period_states(date,state):
    assert a.period_state('2026-11-01','2026-12-25',date)==state
    assert a.period_state(None,None,date)=='販売期間未確認'


def test_suspension_inside_published_period_is_not_available(store):
    source=FIXTURE.read_text(encoding='utf-8').replace('2026年11月1日 ～ 2026年12月25日','2026年11月1日 ～ 2026年12月25日 当メニューは、2026年11月10日から2026年11月20日まで販売休止いたします。')
    row=a.parse_menu(source,store)[0]
    assert a.period_state(row['start'],row['end'],'2026-11-15',row['suspensions'])=='公式掲載上の販売休止期間'


def test_beer_and_wine_sections_do_not_require_recommended_menu_badges(store):
    source=FIXTURE.read_text(encoding='utf-8').replace('<h2>アルコールドリンク</h2>','<h2>ビール</h2>')
    assert any(x['name']=='検証用生ビール' for x in a.parse_menu(source,store))


def test_shared_official_product_and_weekly_idempotence(con,store):
    second={**store,'store_id':'tds:998','park':'DisneySea'}
    rows=data(store);others=[{**x,'store_id':second['store_id']} for x in rows]
    result=a.apply_survey(con,[store,second],{store['store_id']:rows,second['store_id']:others},{},NOW,'weekly:2026-W44')
    assert result['status']=='success'
    assert len(a.records(con,'alcohol_products'))==3 and len(a.records(con,'alcohol_sales'))==6
    before=len(a.records(con,'alcohol_diffs'))
    assert a.apply_survey(con,[],{},{},NOW,'weekly:2026-W44')==result
    assert len(a.records(con,'alcohol_diffs'))==before


def test_failure_and_empty_parse_do_not_end_previous_products(con,store):
    save(con,store);before=a.records(con,'alcohol_sales')
    result=a.apply_survey(con,[store],{}, {store['store_id']:'HTTP timeout'},'2026-11-02T00:00:00Z','failed')
    assert result['status']=='failed' and a.records(con,'alcohol_sales')==before
    result=save(con,store,[],now='2026-11-09T00:00:00Z',run='empty')
    assert result['quarantined_stores']==[store['store_id']] and a.records(con,'alcohol_sales')==before
    assert not any(x['kind']=='store_removed' for x in a.records(con,'alcohol_diffs').values())


def test_price_period_and_small_listing_change_are_audited(con,store):
    rows=data(store)
    # Four prior relations allow one listing removal without triggering 30% guard.
    rows.append({**rows[0],'product_id':'food:90004','name':'検証用別商品'})
    save(con,store,rows)
    changed=[dict(x) for x in rows[:3]];changed[0].update(price=1400,price_text='¥1,400',end='2026-12-30')
    save(con,store,changed,'2026-11-02T00:00:00Z','second')
    kinds={x['kind'] for x in a.records(con,'alcohol_diffs').values()}
    assert {'price_changed','period_changed','store_removed'}<=kinds
    assert len(a.records(con,'alcohol_sales'))==4
    assert len(a.records(con,'alcohol_prices'))==5


def test_source_failure_circuit_keeps_last_good(con,store):
    save(con,store)
    class Client:
        def check_robots(self): raise RuntimeError('source unavailable')
    result=survey(con,Client(),now='2026-11-02T00:00:00Z',run_id='weekly:2026-W45')
    assert result['status']=='failed' and len(a.catalog(con,NOW))==3
    assert a.freshness(con,'2026-11-02T01:00:00Z')['latest_run']['status']=='failed'


@pytest.mark.parametrize('days,level',[(7,'fresh'),(8,'attention'),(14,'warning')])
def test_freshness_thresholds_and_monday_schedule(con,store,days,level):
    save(con,store)
    value=a.freshness(con,r.utc(NOW)+pd.Timedelta(days=days))
    assert value['level']==level
    next_time=pd.Timestamp(value['next_scheduled_jst'])
    assert next_time.day_of_week==0 and next_time.hour==6


def test_filters_do_not_include_unknown_period_as_confirmed_current(con,store):
    save(con,store)
    rows=a.catalog(con,'2026-11-02T00:00:00Z')
    assert len(a.filter_catalog(rows,query='ビール',parks=['Disneyland'],max_price=1000))==1
    assert len(a.filter_catalog(rows,within=True,limited=True))==1
    assert not a.filter_catalog(rows,parks=['DisneySea'])


def test_admin_overlay_auth_ack_and_weekly_preservation(con,store,tmp_path):
    save(con,store);key=next(iter(a.records(con,'alcohol_sales')))
    with pytest.raises(PermissionError): alcohol_admin.export(key,'note')
    event=alcohol_admin.export(key,'管理者の確認メモ',authorized=True)
    assert alcohol_admin.apply(con,event)
    assert not alcohol_admin.apply(con,event)
    save(con,store,now='2026-11-02T00:00:00Z',run='second')
    assert a.records(con,'alcohol_overrides')[key]['note']=='管理者の確認メモ'
    from persistence import snapshot,merge_snapshot
    path=tmp_path/'backup.db';snapshot(con,path)
    restored=a.connect(tmp_path/'restored.db');merge_snapshot(restored,path)
    assert a.records(restored,'alcohol_overrides')[key]['event_id']==event['event_id']
    restored.close()


def test_alcohol_journal_replay_latest_catalog_and_no_duplicate_changes(con,store,tmp_path):
    from persistence import snapshot
    from data_journal import write_delta,replay
    path=tmp_path/'before.db';snapshot(con,path);save(con,store)
    before=sqlite3.connect(path);journal=write_delta(before,con,tmp_path,'Alcohol','test-1');before.close()
    target=a.connect(tmp_path/'new.db')
    assert replay(target,tmp_path/journal['path'])>0
    assert replay(target,tmp_path/journal['path'])==0
    for table in a.TABLES: assert a.records(target,table)==a.records(con,table)
    second_before=tmp_path/'before-update.db';snapshot(con,second_before)
    changed=data(store);changed[0].update(price=1500,price_text='¥1,500')
    save(con,store,changed,'2026-11-02T00:00:00Z','second')
    before=sqlite3.connect(second_before);delta=write_delta(before,con,tmp_path,'Alcohol','test-2');before.close()
    assert replay(target,tmp_path/delta['path'])>0
    for table in a.TABLES:assert a.records(target,table)==a.records(con,table)
    assert target.execute('pragma integrity_check').fetchone()[0]=='ok';target.close()


def test_collection_session_retries_future_real_slot_without_backfill(tmp_path,monkeypatch):
    now=[r.utc('2026-10-30T23:07:00Z')];calls=[]
    def collect(*args,**kwargs):
        calls.append(now[0]);return [{'status':'failed' if len(calls)==1 else 'success'}]
    monkeypatch.setattr(session.collector,'collect',collect)
    def sleep(seconds):now[0]+=pd.Timedelta(seconds=seconds)
    result=session.run(tmp_path,minutes=31,clock=lambda:now[0],sleeper=sleep)
    assert result['samples']==3 and result['failed_samples']==1
    assert [x.tz_convert(r.JST).minute for x in calls]==[7,22,37]
    rows=[json.loads(x) for x in (tmp_path/'collection-attempts.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len({x['started_at_utc'] for x in rows})==3


def test_db_open_failure_classified_and_other_park_continues(tmp_path,monkeypatch):
    import collector
    monkeypatch.setattr(collector.utils,'get_weather',lambda:(20,0,'test',pd.DataFrame(),pd.DataFrame()))
    monkeypatch.setattr(collector.utils,'connect_db',lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('test')))
    result=collector.collect(tmp_path,force=True)
    assert len(result)==2 and {x['failure_class'] for x in result}=={'db_open'}


def test_recent_persisted_observation_skips_duplicate_request(tmp_path,monkeypatch):
    import collector,utils
    from datetime import datetime
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path));monkeypatch.setenv('DISNEY_DISABLE_REMOTE_SYNC','1')
    class Daytime(datetime):
        @classmethod
        def now(cls,tz=None):return r.utc().tz_convert(r.JST).replace(hour=10).to_pydatetime()
    monkeypatch.setattr(collector,'datetime',Daytime)
    monkeypatch.setattr(utils,'get_weather',lambda:(20,0,'test',pd.DataFrame(),pd.DataFrame()))
    for park,s in utils.PARK_SETTINGS.items():
        c,_=utils.connect_db(s['db']);f=r.parse_queue({'rides':[{'id':123,'name':'test','wait_time':20,'is_open':True,'last_updated':r.iso()}]},park)
        r.save_observations(c,f,20,0,park);c.close()
    monkeypatch.setattr(utils,'fetch_wait_times',lambda *args:pytest.fail('Duplicate request should have been skipped'))
    result=collector.collect(tmp_path)
    assert len(result)==2 and all(x['status']=='skipped' for x in result)


def test_three_day_collection_target_needs_actual_independent_complete_days():
    frame=[]
    for stamp in pd.date_range('2026-10-27T08:00', '2026-10-29T21:45',freq='15min',tz=r.JST):
        if not 8<=stamp.hour<22:continue
        for ride in range(37):
            frame.append({'park':'Disneyland','attraction':str(ride),'datetime':stamp.tz_localize(None),
                'observed_at_utc':r.iso(stamp),'fetched_at_utc':r.iso(stamp+pd.Timedelta(minutes=2)),
                'wait_time':10,'is_open':1,'quality':'valid'})
    df=pd.DataFrame(frame)
    audit=observation_audit(df,'Disneyland','2026-10-29T13:01:00Z')
    assert audit['completed_days']==3 and audit['provisional_80_percent']
    assert not observation_audit(df,'Disneyland','2026-10-28T13:01:00Z')['provisional_80_percent']
    df=df[df.datetime.dt.minute.eq(0)]
    audit=observation_audit(df,'Disneyland','2026-10-29T13:01:00Z')
    assert audit['three_day_rate']==.25 and not audit['provisional_80_percent']


def test_new_candidate_frozen_and_late_future_rows_cannot_change_forecast():
    assert models.frozen('recent-trend-candidate-v1')
    training=pd.DataFrame({'attraction':['a']*3,'datetime':pd.to_datetime(['2026-10-30 09:00','2026-10-30 09:15','2026-10-30 13:00']),'wait_time':[10,20,300]})
    targets=[('a','2026-10-30T02:00:00Z')];origin='2026-10-30T01:00:00Z'
    first=models.predict('recent-trend-candidate-v1',training.iloc[:2],{},targets,origin)
    second=models.predict('recent-trend-candidate-v1',training,{},targets,origin)
    assert first==second and 0<=next(iter(first.values()))<=300
    with pytest.raises(ValueError):models.validate_active('recent-trend-candidate-v1',{})


def test_weekly_run_keeps_replayable_bootstrap_checkpoint(tmp_path,monkeypatch):
    import alcohol_collector as ac
    from data_journal import replay
    monkeypatch.setattr(ac,'survey',lambda conn,force=False:a.failure(conn,'official_source_timeout: /robots.txt'))
    result=ac.run(tmp_path)
    assert result['status']=='failed' and result['checkpoint']['changes']>1000
    target=a.connect(tmp_path/'restored-from-checkpoint.db')
    assert replay(target,tmp_path/result['checkpoint']['path'])>1000
    assert len(a.catalog(target))==165
    assert a.freshness(target)['latest_run']['status']=='failed'
    target.close()


def test_source_timeout_log_is_specific_and_contains_no_credentials(monkeypatch):
    import requests
    from alcohol_collector import OfficialClient
    client=OfficialClient(delay=0)
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:(_ for _ in ()).throw(requests.ReadTimeout('private diagnostic text must not be logged')))
    with pytest.raises(RuntimeError,match=r'official_source_timeout: /robots.txt') as err:client.check_robots()
    assert 'private diagnostic' not in str(err.value)


def test_preserved_context_revisions_do_not_multiply_event_adjustment():
    import utils
    day=pd.Timestamp('2026-10-31').date()
    frame=pd.DataFrame([
        {'observed_at':pd.Timestamp('2026-10-30'),'park':'Disneyland','target_date':day,'source':'rule_estimate','event_name':'same signal','bonus':1.},
        {'observed_at':pd.Timestamp('2026-10-31'),'park':'Disneyland','target_date':day,'source':'rule_estimate','event_name':'same signal','bonus':2.},
        {'observed_at':pd.Timestamp('2026-10-31'),'park':'Disneyland','target_date':day,'source':'rule_estimate','event_name':'another signal','bonus':3.},
    ])
    bonus,names=utils.get_event_bonus(frame,day,'Disneyland')
    assert bonus==5. and set(names)=={'same signal','another signal'}
