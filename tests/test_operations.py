import gzip
import json
import sqlite3
from pathlib import Path
import pandas as pd
import pytest
import reliability as r
import utils
import data_quality as q
import model_registry as models
import continuous_evaluation as ce
import operations
import ocr_review
import durable_admin
from persistence import snapshot,merge_snapshot
from data_journal import write_delta,replay


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path))
    con,_=utils.connect_db('test.db')
    yield con
    con.close()


def observations(days=1,ride='a',park='Disneyland'):
    rows=[]
    for day in pd.date_range('2026-10-01',periods=days):
        for hour in range(9,21):
            for minute in (0,15,30,45):
                t=day.replace(hour=hour,minute=minute).tz_localize(r.JST)
                rows.append({'datetime':t.tz_localize(None),'attraction':ride,'wait_time':hour*2.,'is_open':1,'park':park,
                    'quality':'valid','source':'queue-times','observed_at_utc':r.iso(t),'fetched_at_utc':r.iso(t+pd.Timedelta(minutes=1)),'observation_key':f'{park}|{ride}|{t}'})
    return pd.DataFrame(rows)


def test_quality_separates_legacy_stale_closed_and_open_zero():
    f=observations().iloc[:6].copy(); f.loc[0,'wait_time']=0
    f.loc[1,'is_open']=0; f.loc[2,'quality']='quarantined'
    f.loc[3,'fetched_at_utc']=r.iso(r.utc(f.loc[3,'observed_at_utc'])+pd.Timedelta(hours=1))
    f.loc[4,'quality']='legacy_unverified'; f.loc[5,'observed_at_utc']='2099-01-01T00:00:00Z'
    c=q.classify(f,'2026-10-02T00:00:00Z')
    assert c.eligible.tolist()==[True,False,False,False,False,False]
    assert set(c.exclusion)=={'','closed_or_suspended','unverified_source','stale_at_fetch','not_available_at_origin'}
    assert q.training_rows(f,'2026-10-02T00:00:00Z').wait_time.tolist()==[0]


def test_coverage_completed_days_and_partial_day_denominator():
    f=observations(days=15)
    report=q.coverage(f,'Disneyland',['a'],'2026-10-15T03:00:00Z')
    assert report['summary']['eligible_evaluation_days']==14
    current=report['daily'][-1]
    assert current['expected_slots']==12 and not current['evaluation_ready']
    # Closed is a valid state report, but not a ground-truth wait.
    f.is_open=0
    closed=q.coverage(f,'Disneyland',['a'],'2026-10-16T00:00:00Z')
    assert closed['summary']['eligible_evaluation_days']==0
    assert closed['daily'][0]['state_slots']==48 and closed['daily'][0]['valid_open_slots']==0


def test_no_future_observation_or_late_import_can_change_training():
    f=observations(); origin='2026-10-01T02:00:00Z'
    a=models.prepare(f,origin)
    changed=f.copy(); changed.loc[r.utc(origin)<pd.to_datetime(changed.fetched_at_utc,utc=True),'wait_time']=300
    pd.testing.assert_frame_equal(a,models.prepare(changed,origin))
    late=f.copy();late.fetched_at_utc='2026-10-03T00:00:00Z'
    assert models.prepare(late,origin).empty


def test_utc_jst_mismatch_is_not_training_truth():
    frame=observations().iloc[:1].copy()
    frame['datetime']=frame.datetime+pd.Timedelta(days=1)
    result=q.classify(frame,'2026-10-03T00:00:00Z')
    assert result.exclusion.iloc[0]=='jst_utc_mismatch' and not result.eligible.iloc[0]


def test_unverified_or_external_duplicate_cannot_hide_direct_api_truth():
    frame=observations().iloc[:1].copy()
    external=frame.copy();external.quality='verified_external';external.wait_time=90
    unverified=frame.copy();unverified.quality='quarantined';unverified.wait_time=300
    all_rows=pd.concat([unverified,external,frame],ignore_index=True)
    valid=q.classify(all_rows,'2026-10-02T00:00:00Z')
    assert valid.loc[valid.eligible,'wait_time'].tolist()==[18.]


def test_offline_formal_validation_excludes_legacy_and_incomplete_days(monkeypatch):
    import evaluation
    frame=observations(days=16)
    settings=dict(utils.PARK_SETTINGS['Disneyland'],rides=['a'],park='Disneyland')
    class Reference:
        datetime=__import__('datetime').datetime
        @staticmethod
        def predict_wait_times_for_date(*args,**kwargs):
            return pd.DataFrame([{'Attraction':'a','Hour':h,'Minute':0,'Predicted Wait':30} for h in range(9,21)])
    monkeypatch.setattr(utils,'predict_wait_times_for_date',Reference.predict_wait_times_for_date)
    legacy=frame.copy();legacy['quality']='legacy_unverified'
    assert evaluation.backtest(legacy,settings,Reference)[0].empty
    result,info=evaluation.backtest(frame,settings,Reference)
    assert info['available_days']==16 and not result.empty
    assert set(result.horizon_days)=={1} # 7/30-day origins lack 14 prior training dates
    # These 2 test dates do not satisfy the separate 14-independent-evaluation-day gate.
    assert result.target_day.nunique()==2
    frame['wait_time']=0
    zero_result,_=evaluation.backtest(frame,settings,Reference)
    assert zero_result.actual.eq(0).all() and len(zero_result)>0


def test_frozen_models_and_promotion_requires_more_than_fourteen_days():
    for version in ('legacy-original-v0','legacy-corrected-v1'): assert models.frozen(version)
    with pytest.raises(ValueError): models.validate_active('weighted-history-candidate-v1',{})
    report={p:{c:{'independent_days':30} for c in ('major','nonmajor')} for p in r.PARK_IDS}
    assert not models.adoption_gate(report)['allowed']
    assert models.validate_active('legacy-corrected-v1')=='legacy-corrected-v1'


def test_multiple_day_paired_metrics_and_lead_groups(db):
    ce.migrate(db)
    for day in range(1,16):
        issued=f'2026-10-{day:02d}T00:00:00Z'; target=f'2026-10-{day:02d}T02:00:00Z'; key=f'issue{day}'
        db.execute('INSERT INTO model_issues VALUES (?,?,?,?,?,?,?)',(key,'Disneyland',issued,target[:10],'intraday',issued,'{}'))
        for version in models.MODELS:
            value=50 if version=='weighted-history-candidate-v1' else 60
            db.execute('INSERT INTO model_predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(f'{key}{version}',key,'Disneyland','a',target,version,value,50,value-50,issued,'evaluated','major'))
    db.commit(); report=ce.report(db)
    assert report['paired_points']==15
    assert {x['independent_days'] for x in report['paired_metrics']}=={15}
    assert next(x for x in report['paired_metrics'] if x['model_version']=='weighted-history-candidate-v1')['MAE']==0
    assert {x['lead_bucket'] for x in report['by_lead']}=={'0-3h'}
    assert not report['adoption']['allowed']


def test_evening_shadow_targets_next_day_is_immutable(db,monkeypatch):
    f=observations();settings=dict(utils.PARK_SETTINGS['Disneyland'],rides=['a'])
    monkeypatch.setattr(models,'predict',lambda version,training,settings,targets,origin:{x:10. for x in targets})
    first=ce.issue(db,f,settings,'Disneyland','2026-10-01T12:07:00Z')
    assert first==12*len(models.MODELS) # All frozen versions, intraday outside opening hours
    assert ce.issue(db,f,settings,'Disneyland','2026-10-01T12:22:00Z')==0
    assert db.execute('SELECT distinct target_date FROM model_issues').fetchone()[0]=='2026-10-02'
    assert db.execute('SELECT min(target_at_utc) FROM model_predictions').fetchone()[0].startswith('2026-10-02T00:00')


def test_shadow_scoring_requires_three_distinct_slots_and_target_day(db):
    ce.migrate(db)
    db.execute('INSERT INTO model_issues VALUES (?,?,?,?,?,?,?)',('i','Disneyland','2026-10-01T00:00:00Z','2026-10-01','intraday','2026-10-01T00:00:00Z','{}'))
    db.execute('INSERT INTO model_predictions VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?,?)',('p','i','Disneyland','a','2026-10-01T01:00:00Z','legacy-corrected-v1',30,'pending','major'));db.commit()
    f=observations();wrong=f.copy();wrong.attraction='b'
    assert ce.evaluate(db,wrong,'2026-10-02T00:00:00Z')==0
    assert ce.evaluate(db,f,'2026-10-01T01:59:00Z')==0
    assert ce.evaluate(db,f,'2026-10-02T00:00:00Z')==1
    assert db.execute('SELECT actual,error FROM model_predictions').fetchone()==(20.,10.)
    assert ce.evaluate(db,f,'2026-10-03T00:00:00Z')==0


def test_missing_schedule_is_not_reported_as_failed_job():
    runs=[{'id':1,'event':'schedule','created_at':'2026-10-30T00:09:00Z','status':'completed','conclusion':'success'}]
    a=operations.schedule_audit(runs,'2026-10-30T00:07:00Z','2026-10-30T01:00:00Z')
    assert a['expected_slots']==4 and a['not_observed_slots']==1
    assert a['failure_rate_of_executed_jobs']==0
    assert a['slots'][0]['inferred_delay_minutes']==2


def test_external_freshness_distinguishes_attempts_estimates_and_success(db):
    operations.source_check(db,'Disneyland','park_hours','estimated',now='2026-10-01T00:00:00Z')
    operations.source_check(db,'Disneyland','weather','failed',now='2026-10-01T00:00:00Z')
    operations.source_check(db,'Disneyland','weather','success',now='2026-10-01T00:30:00Z')
    out={x['source']:x for x in operations.freshness(db,'2026-10-01T02:00:00Z')}
    assert out['weather']['failure_rate']==.5 and out['weather']['stale']
    assert out['park_hours']['last_success_utc'] is None


def test_fresh_http_fetch_does_not_make_old_weather_current(db):
    operations.source_check(db,'Disneyland','weather','success',now='2026-10-01T02:00:00Z',observed_at='2026-10-01T00:00:00Z')
    row=operations.freshness(db,'2026-10-01T02:05:00Z')[0]
    assert row['age_minutes']==5 and row['source_age_minutes']==125 and row['stale']


@pytest.mark.parametrize('bad',['8O','O','-','301','5.0'])
def test_ocr_ambiguous_values_never_promoted(bad):
    with pytest.raises(ValueError): ocr_review.validate_cell({'datetime':'2026-10-01 10:00:00','attraction':'a','text':bad,'is_open':1},'2026-10-01',{'a'})


def test_ocr_review_is_independent_authorized_and_idempotent(db):
    name=utils.PARK_SETTINGS['Disneyland']['rides'][0]
    row={'datetime':'2026-10-01 10:00:00','attraction':name,'wait_time':50,'is_open':1}
    assert ocr_review.quarantine(db,[row],'Disneyland','2026-10-01','https://example.com/source',b'image')==1
    assert ocr_review.quarantine(db,[row],'Disneyland','2026-10-01','https://example.com/source',b'image')==0
    key=db.execute('SELECT candidate_key FROM history_candidates').fetchone()[0]
    with pytest.raises(PermissionError): ocr_review.approve(db,key,{},'reviewer')
    truth={**row,'source_rights_checked':True,'date_and_name_checked':True,'image_sha256':__import__('hashlib').sha256(b'image').hexdigest()}
    with pytest.raises(ValueError): ocr_review.approve(db,key,{**truth,'wait_time':80},'reviewer',True)
    assert ocr_review.approve(db,key,truth,'reviewer',True)==1
    assert ocr_review.approve(db,key,truth,'reviewer',True)==0
    assert db.execute("SELECT quality FROM wait_times WHERE observation_key LIKE 'verified-external%'").fetchone()[0]=='verified_external'


def test_admin_event_dispatch_ack_restore_and_failed_auth(db,tmp_path):
    name=utils.PARK_SETTINGS['Disneyland']['rides'][0]
    with pytest.raises(PermissionError): durable_admin.enqueue(db,'Disneyland',name,'2026-10-30',10)
    payload=durable_admin.enqueue(db,'Disneyland',name,pd.Timestamp.now(tz=r.JST).date(),10,True)
    assert durable_admin.dispatch(payload,'',True)=='not_configured'
    assert durable_admin.apply(db,payload)==1 and durable_admin.apply(db,payload)==0
    path=tmp_path/'ack.db';snapshot(db,path)
    other=sqlite3.connect(':memory:')
    # Use the real additive initialization via a separate runtime file.
    con,_=utils.connect_db('restored.db');merge_snapshot(con,path)
    assert con.execute('SELECT status FROM admin_events WHERE event_id=?',(payload['event_id'],)).fetchone()[0]=='acknowledged'
    assert con.execute("SELECT count(*) FROM dpa_sellouts WHERE source='manual'").fetchone()[0]==1
    con.close();other.close()


def test_journal_replay_preserves_rows_and_evaluation(db,tmp_path):
    path=tmp_path/'before.db';snapshot(db,path);before=sqlite3.connect(path)
    payload={'rides':[{'id':1,'name':'a','wait_time':0,'is_open':True,'last_updated':'2026-10-01T00:00:00Z'}]}
    r.save_observations(db,r.parse_queue(payload,'Disneyland','2026-10-01T00:01:00Z'),20,0,'Disneyland')
    entry=write_delta(before,db,tmp_path,'Disneyland','run-1');before.close()
    con,_=utils.connect_db('replay.db')
    assert replay(con,tmp_path/entry['path'])==1
    assert replay(con,tmp_path/entry['path'])==0
    assert con.execute('SELECT wait_time FROM wait_times').fetchone()[0]==0
    con.close()


def test_collector_failure_then_recovery_both_parks_and_no_duplicates(tmp_path,monkeypatch):
    import collector
    monkeypatch.setattr(utils,'PARK_SETTINGS',{p:dict(s,db=p+'.db') for p,s in utils.PARK_SETTINGS.items()})
    monkeypatch.setattr(utils,'get_weather',lambda:(20,0,'test',pd.DataFrame(),pd.DataFrame()))
    monkeypatch.setattr(collector,'persist_forecasts',lambda *args:None)
    monkeypatch.setattr(ce,'issue',lambda *args:0)
    state={'fail':True}
    def fetch(settings):
        if state['fail']: raise RuntimeError('simulated timeout')
        raw={'rides':[{'id':1,'name':'a','wait_time':40,'is_open':True,'last_updated':r.iso()}]}
        f=r.parse_queue(raw,settings['park']);return f,f
    monkeypatch.setattr(utils,'fetch_wait_times',fetch)
    monkeypatch.setenv('GITHUB_RUN_ID','test-failure')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    first=collector.collect(tmp_path,force=True)
    assert len(first)==2 and all(x['status']=='failed' for x in first)
    state['fail']=False;monkeypatch.setenv('GITHUB_RUN_ID','test-recovery')
    second=collector.collect(tmp_path,force=True)
    assert all(x['status']=='success' and x['inserted']==1 for x in second)
    for park in utils.PARK_SETTINGS:
        con=sqlite3.connect(tmp_path/(park+'.db'))
        assert con.execute("SELECT count(*) FROM collection_runs WHERE status='failed'").fetchone()[0]==1
        con.close()


def test_corrupt_db_recovery_preserves_original_bytes(tmp_path):
    import subprocess
    from backup_recovery import recover,valid
    folder=tmp_path/'repo';folder.mkdir();path=folder/'disneyland.db'
    con=sqlite3.connect(path);con.execute('CREATE TABLE evidence (n INTEGER)');con.execute('INSERT INTO evidence VALUES (7)');con.commit();con.close()
    subprocess.run(['git','init'],cwd=folder,capture_output=True,check=True)
    subprocess.run(['git','add','disneyland.db'],cwd=folder,check=True)
    subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Intact snapshot'],cwd=folder,capture_output=True,check=True)
    path.write_bytes(b'corrupt test data')
    result=recover(path,folder)
    assert result['status']=='recovered' and valid(path)
    assert gzip.decompress((folder/result['preserved_corrupt']).read_bytes())==b'corrupt test data'
    assert sqlite3.connect(path).execute('SELECT n FROM evidence').fetchone()[0]==7


def test_recovery_stops_without_intact_backup(tmp_path):
    import subprocess
    from backup_recovery import recover
    subprocess.run(['git','init'],cwd=tmp_path,capture_output=True,check=True)
    path=tmp_path/'disneyland.db';path.write_bytes(b'preserve me')
    with pytest.raises((RuntimeError,subprocess.CalledProcessError)): recover(path,tmp_path)
    assert path.read_bytes()==b'preserve me'


def test_failed_admin_auth_never_dispatches_token_or_payload(monkeypatch):
    called=[];monkeypatch.setattr(durable_admin.requests,'post',lambda *a,**k:called.append(k))
    with pytest.raises(PermissionError): durable_admin.dispatch({},'test-secret',False)
    assert not called


@pytest.mark.parametrize('status',[200,204,403])
def test_admin_dispatch_accepts_documented_success_variants(monkeypatch,status):
    from types import SimpleNamespace
    import uuid
    payload={'event_id':str(uuid.uuid4()),'park':'Disneyland','attraction':utils.PARK_SETTINGS['Disneyland']['rides'][0],
        'target_date':str(pd.Timestamp.now(tz=r.JST).date()),'sellout_hour':12.,'created_at_utc':r.iso()}
    monkeypatch.setattr(durable_admin.requests,'post',lambda *a,**k:SimpleNamespace(status_code=status))
    if status==403:
        with pytest.raises(RuntimeError,match='HTTP 403'):durable_admin.dispatch(payload,'isolated-test-token',True)
    else:
        assert durable_admin.dispatch(payload,'isolated-test-token',True)=='submitted'


def test_admin_ui_authorizes_save_and_restart_retains_outbox(tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    import hashlib
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path))
    monkeypatch.setenv('DISNEY_DISABLE_REMOTE_SYNC','1')
    monkeypatch.setenv('DISNEY_ADMIN_PASSWORD_SHA256',hashlib.sha256(b'isolated-test-password').hexdigest())
    monkeypatch.delenv('DISNEY_GITHUB_ACTIONS_TOKEN',raising=False)
    monkeypatch.setattr(utils,'get_weather',lambda:(20,0,'test',pd.DataFrame(),pd.DataFrame()))
    monkeypatch.setattr(utils,'fetch_ticket_prices',lambda:({},'test unavailable'))
    monkeypatch.setattr(utils,'fetch_wait_times',lambda settings:(_ for _ in ()).throw(RuntimeError('isolated outage')))
    st.cache_data.clear()
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=45).run()
    app.button_group(key='display_mode_segmented').set_value('DPA/PP予測').run()
    app.text_input(key='admin_password').set_value('wrong').run()
    assert [b for b in app.button if b.label=='保存'][0].disabled
    app.text_input(key='admin_password').set_value('isolated-test-password').run()
    assert not [b for b in app.button if b.label=='保存'][0].disabled
    [b for b in app.button if b.label=='保存'][0].click().run()
    assert not app.exception
    con=sqlite3.connect(tmp_path/'disneysea.db')
    assert con.execute("SELECT count(*) FROM dpa_sellouts WHERE source='manual'").fetchone()[0]==1
    assert con.execute("SELECT count(*) FROM admin_events WHERE status='pending'").fetchone()[0]==1
    con.close()
    restarted=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=45).run()
    restarted.button_group(key='display_mode_segmented').set_value('データ管理').run()
    assert not restarted.exception
