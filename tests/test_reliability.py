import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest
import reliability as r
import utils
from routing import optimize, wait_at
from admin import authorized


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path))
    conn,cursor=utils.connect_db('empty.db')
    yield conn
    conn.close()


def fixture(park=274):
    return json.loads((Path(__file__).parent/'fixtures'/f'queue-{park}.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('pid,park,count',[(274,'Disneyland',37),(275,'DisneySea',36)])
def test_real_response(pid,park,count):
    frame=r.parse_queue(fixture(pid),park)
    assert len(frame)==count
    assert frame.RideID.nunique()==count
    assert frame.Area.ne('').all()
    assert frame.UpdatedUTC.notna().all()


def test_shapes_duplicates_missing_and_open_zero():
    raw={'id':1,'name':'a','is_open':True,'wait_time':0,'last_updated':'2026-10-30T00:00:00Z'}
    data={'rides':[raw],'lands':[{'id':10,'name':'area','rides':[raw]}]}
    frame=r.parse_queue(data,'Disneyland','2026-10-30T00:01:00Z')
    assert len(frame)==1 and frame.iloc[0].Area=='area'
    assert len(r.fresh_open(frame,'2026-10-30T00:02:00Z'))==1
    assert r.fresh_open(frame,'2026-10-30T01:00:00Z').empty
    raw['wait_time']=None
    frame=r.parse_queue({'rides':[raw]},'Disneyland','2026-10-30T00:01:00Z')
    assert frame.iloc[0].Wait is None and frame.iloc[0].Quality=='incomplete'
    assert r.parse_queue({'lands':[],'rides':[]},'Disneyland').empty
    for malformed in [[],{}, {'rides':{}}]:
        with pytest.raises(ValueError): r.parse_queue(malformed,'Disneyland')


def test_idempotent_ingest_and_jst(db):
    frame=r.parse_queue(fixture(),'Disneyland')
    first=r.save_observations(db,frame,np.nan,np.nan,'Disneyland')
    assert first==37 and r.save_observations(db,frame,20,0,'Disneyland')==0
    row=db.execute('SELECT datetime,observed_at_utc,temperature FROM wait_times LIMIT 1').fetchone()
    assert pd.Timestamp(row[0])==pd.Timestamp(row[1]).tz_convert(r.JST).tz_localize(None)
    assert row[2] is None
    assert len(r.load_history(db))==37 # closures are retained


def prediction(day='2026-10-31',value=80):
    f=pd.DataFrame([{'Hour':14,'Minute':0,'Predicted Wait':value,'Attraction':'a'}])
    f.attrs.update(target_date=day,park='Disneyland')
    return f


def test_correct_target_day_and_immutable(db):
    f=prediction()
    assert r.save_predictions(db,f,'a',generated_at='2026-10-30T12:00:00Z')==1
    assert r.save_predictions(db,prediction(value=5),'a',generated_at='2026-10-30T12:01:00Z')==0
    assert db.execute('SELECT predicted_wait FROM predictions').fetchone()[0]==80
    for day,wait in [('2026-10-30',10),('2026-10-31',90)]:
        payload={'rides':[{'id':1,'name':'a','is_open':True,'wait_time':wait,'last_updated':day+'T05:05:00Z'}]}
        r.save_observations(db,r.parse_queue(payload,'Disneyland',day+'T05:06:00Z'),20,0,'Disneyland')
    assert r.evaluate_predictions(db,[],now='2026-10-31T05:10:00Z')==0
    assert r.evaluate_predictions(db,[],now='2026-10-31T05:16:00Z')==1
    assert db.execute('SELECT actual_wait,error FROM predictions').fetchone()==(90,10)
    assert r.evaluate_predictions(db,[],now='2026-11-01T00:00:00Z')==0


def test_retrospective_and_legacy_excluded(db):
    db.execute("INSERT INTO predictions(created_at,target_hour,predicted_wait,error) VALUES('2026-05-08 09:00:00',10,50,1)")
    db.commit()
    assert utils.load_prediction_history(db).empty
    assert r.save_predictions(db,prediction('2026-10-29'),'a',generated_at='2026-10-30T00:00:00Z')==0
    f=prediction(); f.attrs['feature_cutoff_utc']='2026-11-01T00:00:00Z'
    with pytest.raises(ValueError): r.save_predictions(db,f,'a',generated_at='2026-10-30T00:00:00Z')


def test_daily_locked_not_overwritten(db):
    assert r.save_daily(db,date(2026,10,31),5)
    assert not r.save_daily(db,date(2026,10,31),9)
    assert r.locked_daily(db,date(2026,10,31))==5


def test_empty_history_no_fabricated_forecast():
    pred=utils.predict_wait_times_for_date(pd.DataFrame(),utils.PARK_SETTINGS['Disneyland'],date.today(),20,0,pd.DataFrame())
    assert pred.empty and 'Attraction' in pred


def test_route_completion_missing_zero():
    rows=[]
    for a,wait in [('a',0),('b',60)]:
        for h in range(9,21): rows.append({'Attraction':a,'Hour':h,'Minute':0,'Predicted Wait':wait})
    f=pd.DataFrame(rows)
    assert wait_at(f,'missing',540) is None
    route,meta=optimize(f,['b','a','missing'],'09:00','09:30')
    assert route.Attraction.tolist()==['a']
    assert set(meta['skipped'])=={'b','missing'} and meta['end_time']<='09:30'
    assert meta['total_wait']==0


def test_unknown_weather_not_copied_to_future():
    t,rain,source=utils.get_forecast_weather_for_date(pd.DataFrame(),date(2099,1,1),20,0)
    assert np.isnan(t) and np.isnan(rain) and '予報なし' in source


def test_default_deny_admin():
    import hashlib
    assert not authorized('anything','')
    digest=hashlib.sha256(b'secret').hexdigest()
    assert authorized('secret',digest) and not authorized('bad',digest)


def test_actual_gaps_and_open_zero():
    today=pd.Timestamp.now(tz=r.JST).tz_localize(None).normalize()
    f=pd.DataFrame({'datetime':[today+pd.Timedelta(hours=9),today+pd.Timedelta(hours=9,minutes=30)],
                    'attraction':['a','a'],'wait_time':[0,30],'is_open':[1,1]})
    f['date']=f.datetime.dt.date; f['hour']=f.datetime.dt.hour
    actual=utils.get_actual_wait_series_for_today(f,'a').set_index('TimeLabel')['Actual Wait']
    assert actual['09:00']==0 and np.isnan(actual['09:15']) and actual['09:30']==30


def test_backup_restore_merge_does_not_lose_local(db,tmp_path,monkeypatch):
    from persistence import snapshot,merge_snapshot
    frame=r.parse_queue(fixture(),'Disneyland')
    r.save_observations(db,frame,20,0,'Disneyland')
    archive=tmp_path/'backup.db'
    snapshot(db,archive)
    original=db.execute('SELECT count(*) FROM wait_times').fetchone()[0]
    assert merge_snapshot(db,archive)==0
    assert db.execute('SELECT count(*) FROM wait_times').fetchone()[0]==original
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_concurrent_readers_ingest_one_copy(tmp_path,monkeypatch):
    monkeypatch.setenv('DISNEY_DB_DIR',str(tmp_path))
    frame=r.parse_queue(fixture(),'Disneyland')
    def insert(_):
        conn,_=utils.connect_db('concurrent.db')
        try: return r.save_observations(conn,frame,20,0,'Disneyland')
        finally: conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(insert,range(4)))==37


@pytest.mark.parametrize('days',[30,90,180,365])
def test_history_import_range_resumes_and_reports_source_failures(db,monkeypatch,days):
    from datetime import timedelta
    end=date(2026,10,29); start=end-timedelta(days=days-1)
    calendar=utils.fetch_disneyreal_history_calendar('Disneyland',start,end)
    assert len(calendar)==days
    monkeypatch.setattr(utils,'fetch_disneyreal_daily_waits',lambda *a:([], 'image-only fixture; no numeric table','skipped'))
    result=utils.import_disneyreal_history(db.cursor(),db,'Disneyland',start,end,max_days=7)
    assert result['saved_count']==0 and result['processed_days']==1
    assert any(x['status']=='paused' for x in result['results'])


def test_manual_weather_changes_forecast_and_horizon_schema():
    with sqlite3.connect(Path(__file__).parents[1]/'disneyland.db') as conn:
        history=pd.read_sql_query('SELECT * FROM wait_times',conn)
    history['datetime']=pd.to_datetime(history.datetime)
    history['date']=history.datetime.dt.date; history['hour']=history.datetime.dt.hour
    for horizon in (0,1,7,30):
        day=pd.Timestamp.now(tz=r.JST).date()+pd.Timedelta(days=horizon)
        sunny=utils.predict_wait_times_for_date(history,utils.PARK_SETTINGS['Disneyland'],day,20,0,pd.DataFrame())
        rainy=utils.predict_wait_times_for_date(history,utils.PARK_SETTINGS['Disneyland'],day,35,5,pd.DataFrame())
        assert len(sunny)==5*48
        assert sunny['Predicted Wait'].mean()>rainy['Predicted Wait'].mean()
        assert sunny.attrs['target_date']==str(day)


def test_dpa_correction_preserves_previous_value(db):
    assert utils.save_dpa_sellout(db.cursor(),db,'a',12)==1
    assert utils.save_dpa_sellout(db.cursor(),db,'a',12)==0
    assert utils.save_dpa_sellout(db.cursor(),db,'a',13)==1
    assert db.execute('SELECT count(*) FROM dpa_sellouts').fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM dpa_revisions').fetchone()[0]==1


def test_dpa_maps_names_instead_of_old_column_order():
    from dpa import parse_report,eligible
    html='''<html><title>2026年10月29日 東京ディズニーランド</title><table>
    <tr><td>今日</td><td>スプラッシュ・マウンテン 10:00発行終了</td><td>美女と野獣 11:00発行終了</td></tr>
    <tr><td>10/28(水)</td><td>12:00</td><td>13:00</td></tr></table></html>'''
    frame,message=parse_report(html,'Disneyland','https://example.com',utils._normalize_disneyreal_attraction_name,pd.Timestamp('2026-10-30'))
    assert len(frame)==4
    dated=html.replace('2026年10月29日 東京ディズニーランド','東京ディズニーランド 今日')+'<time datetime="2026-10-29T20:57:42+0900"></time>'
    timed,_=parse_report(dated,'Disneyland','https://example.com',utils._normalize_disneyreal_attraction_name,pd.Timestamp('2026-10-30'))
    assert set(timed.target_date)=={date(2026,10,28),date(2026,10,29)}
    old=frame[frame.target_date.eq(date(2026,10,28))].set_index('attraction')
    assert old.loc['Splash Mountain','sellout_hour']==12
    assert old.loc['Enchanted Tale of Beauty and the Beast','sellout_hour']==13
    assert len(eligible('Disneyland',date(2026,10,30)))==7
    assert 'Indiana Jones Adventure: Temple of the Crystal Skull' not in eligible('DisneySea',date(2026,10,30))
    unknown,_=parse_report('<table></table>','Disneyland','url',str,pd.Timestamp('2026-10-30'))
    assert unknown.empty


def test_unavailable_values_are_not_zero_or_official():
    f=pd.DataFrame(columns=r.WAIT_COLUMNS)
    assert np.isnan(utils.get_all_attraction_crowd_stats(f)['avg_wait'])
    assert utils.get_ticket_price_from_castel(date(2099,1,1),{})[0] is None
    hours=pd.DataFrame([{'target_date':date(2026,10,30),'park':'Disneyland','open_hour':9,'close_hour':21,'source':'rule_estimate'}])
    assert '推定' in utils.get_today_park_hours(hours,'Disneyland',date(2026,10,30))[2]


def test_scheduled_week_forecasts_survive_rerun_and_evening_issue(db,monkeypatch):
    from collector import persist_forecasts
    with sqlite3.connect(Path(__file__).parents[1]/'disneyland.db') as source:
        history=pd.read_sql_query('SELECT * FROM wait_times',source)
    history['datetime']=pd.to_datetime(history.datetime)
    history['date']=history.datetime.dt.date;history['hour']=history.datetime.dt.hour
    weather=(20.,0.,'',pd.DataFrame(),pd.DataFrame())
    now=pd.Timestamp.now(tz=r.JST).normalize()+pd.Timedelta(hours=8)
    persist_forecasts(db,history,utils.PARK_SETTINGS['Disneyland'],'Disneyland',weather,now)
    before=db.execute('SELECT * FROM daily_crowd_predictions').fetchall()
    assert len(before)==7
    assert db.execute('SELECT count(*) FROM predictions WHERE target_at_utc IS NOT NULL').fetchone()[0]>0
    persist_forecasts(db,history,utils.PARK_SETTINGS['Disneyland'],'Disneyland',weather,now+pd.Timedelta(minutes=15))
    assert db.execute('SELECT * FROM daily_crowd_predictions').fetchall()==before
    persist_forecasts(db,history,utils.PARK_SETTINGS['Disneyland'],'Disneyland',weather,now+pd.Timedelta(hours=13))
    assert db.execute("SELECT count(*) FROM daily_crowd_predictions WHERE forecast_kind='21:00'").fetchone()[0]==1
    assert db.execute("SELECT * FROM daily_crowd_predictions WHERE forecast_kind='fixed'").fetchall()==before


def test_unobserved_forecast_expires_without_fabricated_actual(db):
    r.save_predictions(db,prediction(),'a',generated_at='2026-10-30T12:00:00Z')
    r.evaluate_predictions(db,[],now='2026-11-10T00:00:00Z')
    assert db.execute('SELECT predicted_wait,actual_wait,error,evaluation_status FROM predictions').fetchone()==(80,None,None,'insufficient_actual')


def test_status_diagnostics_use_saved_observations(db):
    frame=r.parse_queue(fixture(),'Disneyland')
    r.save_observations(db,frame,20,0,'Disneyland')
    loaded=utils.load_attraction_status_snapshots(db)
    assert len(loaded)==37
    assert loaded.attraction.nunique()==37
    assert loaded.is_open.eq(0).all()


def test_route_discloses_model_imputation():
    frame=pd.DataFrame([{'Attraction':'a','Hour':9,'Minute':0,'Predicted Wait':10,'理由':'履歴不足：パーク傾向で補完'}])
    route,meta=optimize(frame,['a'],'09:00','12:00')
    assert meta['completed_count']==1 and meta['filled_attractions']==['a']


def test_dpa_uses_history_when_closed_and_never_invents_missing_times():
    empty=utils.predict_dpa_sellout_time('a',np.nan,np.nan,None,0,pd.DataFrame())
    assert empty['予測売切れ時刻']=='未推定' and np.isnan(empty['Risk Score'])
    history=pd.DataFrame([{'attraction':'a','sellout_hour':19.,'observed_at':pd.Timestamp('2026-10-29'),'target_date':date(2026,10,29)}])
    prior=utils.predict_dpa_sellout_time('a',np.nan,np.nan,None,0,history)
    assert prior['予測売切れ時刻']=='19:00' and prior['履歴件数']==1
