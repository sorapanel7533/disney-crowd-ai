"""Acceptance boundaries, not implementation-shaped count tests."""
import concurrent.futures
import json
import threading
import pandas as pd
import pytest
import measured as m
from measured_store import GitHubStore,Unavailable,Conflict

START='2026-11-01T10:20:00+09:00'
NOW='2026-11-02T12:00:00+09:00'
RECEIPT='a'*64

def row(**kw):
    values=dict(park='Disneyland',ride_id=7987,start=START,minutes=45,receipt=RECEIPT,now=NOW,
        display={'displayed_wait':70,'display_source':'manual-sign','display_observed_at':START})
    values.update(kw);return m.make(**values)

def test_two_methods_and_explicit_zero_minutes():
    a=row();b=row(minutes=None,end='2026-11-01T11:05:00+09:00')
    assert a['actual_wait']==b['actual_wait']==45
    assert a['end_is_calculated'] and not b['end_is_calculated']
    assert a['deviation']==-25 and a['ratio']==pytest.approx(45/70)
    assert a['deviation_pct']==pytest.approx(-35.7142857)
    zero=row(minutes=0,display={'displayed_wait':0,'display_source':'manual-sign','display_observed_at':START})
    assert zero['deviation']==0 and zero['ratio'] is None and zero['deviation_pct'] is None

@pytest.mark.parametrize('kw',[{'minutes':-1},{'minutes':float('nan')},{'minutes':1441},{'start':'2027-01-01'},
    {'minutes':None,'end':'2026-11-01T10:00:00+09:00'},
    {'minutes':None,'end':START},
    {'minutes':None,'end':'2026-11-02T00:10:00+09:00'},
    {'start':'2026-11-01T23:50:00+09:00','minutes':20,'display':None},
    {'minutes':None,'end':'2026-11-01T11:05:00+09:00','now':'2026-11-01T10:30:00+09:00'},
    {'minutes':361},{'park':'DisneySea'},{'ride_id':999999}])
def test_invalid_input(kw):
    with pytest.raises(ValueError):row(**kw)

def test_history_no_future_acquisition_no_stale_no_wrong_ride():
    base={'park':'Disneyland','ride_id':7987,'quality':'valid','is_open':1,'wait_time':70,
      'observed_at_utc':'2026-11-01T10:15:00+09:00','fetched_at_utc':'2026-11-01T10:16:00+09:00'}
    f=pd.DataFrame([base,{**base,'wait_time':90,'observed_at_utc':'2026-11-01T10:21:00+09:00'},
      {**base,'wait_time':80,'fetched_at_utc':'2026-11-01T11:00:00+09:00'},
      {**base,'wait_time':50,'observed_at_utc':'2026-11-01T09:59:00+09:00'},
      {**base,'wait_time':30,'ride_id':7986}])
    assert m.displayed(f,'Disneyland',7987,START)['displayed_wait']==70
    assert m.displayed(f.iloc[1:],'Disneyland',7987,START) is None
    assert m.displayed(pd.DataFrame([{**base,'is_open':0}]),'Disneyland',7987,START) is None

def test_idempotency_quarantine_moderation_audit_and_restore(tmp_path):
    db=m.connect(tmp_path/'measure.db');a=m.save(db,row(),now=NOW)
    assert m.save(db,row(record_id=a['id']),now=NOW)==a
    assert len(m.records(db))==1 and m.frame(db).empty
    second=m.save(db,row(),now=NOW);assert second['status']=='quarantined'
    with pytest.raises(PermissionError):m.moderate(db,a['id'],'approved',reason='test',now=NOW)
    m.moderate(db,a['id'],'approved',authorized=True,reason='fixture review',now=NOW)
    assert len(m.frame(db))==1
    assert len(m.frame(db,approved=False))==1  # duplicate remains excluded
    m.moderate(db,a['id'],'invalid',authorized=True,reason='fixture invalidate',now=NOW)
    assert m.frame(db).empty
    assert db.execute('SELECT COUNT(*) FROM measurement_audit').fetchone()[0]==4
    db.close();restored=m.connect(tmp_path/'measure.db');assert len(m.records(restored))==2
    assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok';restored.close()

def test_timer_resume_and_owned_completion(tmp_path):
    path=tmp_path/'draft.db';db=m.connect(path)
    draft=m.save(db,row(minutes=None),now=NOW);assert draft['status']=='draft';db.close()
    db=m.connect(path)
    with pytest.raises(PermissionError):m.finish(db,draft['id'],'b'*64,'2026-11-01T11:05:00+09:00',now=NOW)
    done=m.finish(db,draft['id'],RECEIPT,'2026-11-01T11:05:00+09:00',now=NOW)
    assert done['actual_wait']==45 and done['displayed_wait']==70
    assert m.finish(db,draft['id'],RECEIPT,NOW,now=NOW)==done
    assert len(m.records(db))==1


def test_storage_rejects_invalid_duration_and_preserves_draft():
    db=m.connect()
    draft=m.save(db,row(minutes=None),now=NOW)
    for end,now in [(START,NOW),('2026-11-02T00:10:00+09:00',NOW),
                    ('2026-11-01T16:21:00+09:00',NOW),('2026-11-01T11:05:00+09:00',START)]:
        with pytest.raises(ValueError):m.finish(db,draft['id'],RECEIPT,end,now=now)
    invalid={**row(),'boarded_at':'2026-11-02T00:10:00+09:00'}
    with pytest.raises(ValueError):m.save(db,invalid,now=NOW)
    assert m.records(db)==[draft]
    assert db.execute('SELECT COUNT(*) FROM measurement_audit').fetchone()[0]==1


def test_live_observation_is_fixed_to_queue_entry():
    data={'park':'Disneyland','ride_id':7987,'quality':'valid','is_open':1,'wait_time':70,
          'observed_at_utc':'2026-11-01T10:19:00+09:00','fetched_at_utc':'2026-11-01T10:20:10+09:00'}
    history=pd.DataFrame([data,{**data,'wait_time':90,'observed_at_utc':'2026-11-01T10:20:05+09:00'}])
    assert m.displayed(history,'Disneyland',7987,START) is None
    live=m.displayed(history,'Disneyland',7987,START,live=True)
    assert live['display_source']=='queue-times-live' and live['displayed_wait']==70
    saved=row(minutes=None,display=live,now='2026-11-01T10:20:11+09:00')
    assert saved['started_at']==m.stamp(START)
    assert saved['display_fetched_at']==m.stamp(data['fetched_at_utc'])
    too_late=pd.DataFrame([{**data,'fetched_at_utc':'2026-11-01T10:21:01+09:00'}])
    assert m.displayed(too_late,'Disneyland',7987,START,live=True) is None

def test_statistics_denominators_and_distribution():
    f=pd.DataFrame([{'deviation':-25,'deviation_pct':-35.7},{'deviation':0,'deviation_pct':None},{'deviation':15,'deviation_pct':30}])
    stats,bins=m.statistics(f,15)
    assert stats['n']==3 and stats['ratio_n']==2
    assert stats['median']==0 and stats['std']>0
    assert bins['相対度数'].sum()==pytest.approx(1)
    assert bins['累積相対度数'].iloc[-1]==pytest.approx(1)
    assert stats['within_displayed']==pytest.approx(2/3)

def training():
    db=m.connect()
    for d in range(10):
        for h in (10,11,12,13):
            start=pd.Timestamp('2026-10-01',tz='Asia/Tokyo')+pd.Timedelta(days=d,hours=h)
            a=m.save(db,row(start=start,display=None,minutes=45,now=start+pd.Timedelta(hours=2),receipt=(str(d)+str(h)).ljust(64,'x')),now=start+pd.Timedelta(hours=2))
            # Missing display cannot become a training pair by inference.
            m.moderate(db,a['id'],'approved',authorized=True,reason='fixture',now=start+pd.Timedelta(hours=3))
            raw=next(x for x in m.records(db) if x['id']==a['id']);raw=m.derive({**raw,'displayed_wait':70,'display_source':'manual-sign'})
            with db:db.execute('UPDATE measurements SET payload=? WHERE id=?',(json.dumps(raw),a['id']))
    return m.frame(db)

def test_models_data_gates_asof_and_independent_days():
    f=training()
    assert m.estimate(pd.DataFrame(),'Disneyland',7987,70,NOW)['estimate'] is None
    assert m.estimate(f.head(5),'Disneyland',7987,70,NOW,as_of=NOW)['estimate'] is None
    p=m.estimate(f,'Disneyland',7987,70,NOW,as_of=NOW)
    assert p['estimate']==45 and p['state']=='暫定推定・精度未検証'
    assert m.estimate(f,'Disneyland',7987,70,NOW,as_of='2026-09-01')['estimate'] is None
    assert m.estimate(f,'Disneyland',7987,70,NOW,as_of=NOW,opened=False)['estimate'] is None
    e=m.evaluate(f);assert e.date.nunique()==5 and set(e.method)==set(m.METHODS)
    assert m.metrics(e.error)['MAE']==0 and m.metrics(e.baseline_error)['MAE']==25
    f['updated_at']='2026-12-01T00:00:00Z'
    assert m.evaluate(f).empty

class SharedRemote:
    def __init__(self):self.raw=m.connect().serialize();self.head=0;self.lock=threading.Lock()

class RemoteClient(GitHubStore):
    def __init__(self,server):super().__init__('isolated-test-token');self.server=server
    def load(self):
        with self.server.lock:
            db=m.connect();db.deserialize(self.server.raw);return db,self.server.head,None
    def commit(self,db,head,tree):
        with self.server.lock:
            if self.server.head!=head:raise Conflict()
            self.server.raw=db.serialize();self.server.head+=1;return self.server.head

def test_external_boundary_two_users_concurrent_and_new_sessions():
    remote=SharedRemote()
    a=row(is_test=True);b=row(start='2026-11-01T10:21:00+09:00',display=None,receipt='b'*64,is_test=True)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda x:RemoteClient(remote).submit(x),[a,b]))
    third=RemoteClient(remote);db,_,_=third.load()
    assert len(m.records(db))==2 and len(m.frame(db,approved=False,include_test=True))==2
    assert m.frame(db).empty # fixture submissions never pollute production learning
    assert third.submit(a)[0]['id']==a['id']
    db,_,_=RemoteClient(remote).load();assert len(m.records(db))==2

def test_external_failure_never_reports_success():
    with pytest.raises(Unavailable):GitHubStore('').submit(row())
    class Broken(RemoteClient):
        def commit(self,*args):raise Unavailable('simulated offline')
    with pytest.raises(Unavailable):Broken(SharedRemote()).submit(row())

def test_daily_rebuild_no_retraining_unchanged_and_recover(tmp_path):
    import gzip
    from measured_daily import rebuild
    from measured_recovery import restore
    db=m.connect();a=m.save(db,row(),now=NOW)
    m.moderate(db,a['id'],'approved',authorized=True,reason='fixture',now=NOW)
    first=rebuild(db,now=NOW);assert not first['adopted'] and first['training_records']==1
    assert rebuild(db,now=NOW)==first
    assert db.execute('SELECT COUNT(*) FROM measured_models').fetchone()[0]==1
    value={'format':'measured-snapshot-v1','measurements':m.records(db),'audit':[
        dict(zip(('id','record_id','at','payload'),x)) for x in db.execute('SELECT * FROM measurement_audit')],
        'models':[dict(zip(('id','payload'),x)) for x in db.execute('SELECT * FROM measured_models')]}
    checkpoint=tmp_path/'checkpoint.gz';checkpoint.write_bytes(gzip.compress(json.dumps(value).encode()))
    dest=tmp_path/'recovered.db';assert restore(checkpoint,dest)==1
    restored=m.connect(dest);assert m.records(restored)==m.records(db)
    assert restored.execute('SELECT COUNT(*) FROM measurement_audit').fetchone()[0]==2
    with pytest.raises(ValueError):restore(checkpoint,dest)
