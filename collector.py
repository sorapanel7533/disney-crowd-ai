"""One bounded collection run for both parks. Scheduled independently of Streamlit."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import sqlite3
import tempfile
import time
import pandas as pd

import reliability as r
import utils
from persistence import snapshot
import operations
import continuous_evaluation as evaluation
import durable_admin
from data_quality import coverage
from data_journal import write_delta


def persist_forecasts(conn, history, settings, park, weather, now):
    """Durable first-issued weekly forecasts, plus a separate previous-evening issue."""
    prediction_history=utils.load_prediction_history(conn)
    daily_history=utils.load_daily_crowd_predictions(conn)
    hours=utils.load_park_hours(conn)
    events=utils.load_event_signals(conn)
    targets=[(now.date()+timedelta(days=i),'fixed') for i in range(7)
             if r.locked_daily(conn,now.date()+timedelta(days=i)) is None]
    tomorrow=now.date()+timedelta(days=1)
    if now.hour==21 and not conn.execute(
        "SELECT 1 FROM daily_crowd_predictions WHERE target_date=? AND forecast_kind='21:00' AND model_version=?",
        (str(tomorrow),r.MODEL_VERSION)).fetchone():
        targets.append((tomorrow,'21:00'))
    for target,kind in targets:
        temp,rain,source=utils.get_forecast_weather_for_date(weather[4],target,weather[0],weather[1])
        index,pred,reasons=utils.predict_crowd_index_for_date(
            history,settings,target,temp,rain,prediction_history,daily_history,
            event_signals=events,park_hours_df=hours,park=park)
        features={**pred.attrs.get('features',{}),'weather_source':source,'reasons':reasons,
                  'feature_cutoff_utc':pred.attrs.get('feature_cutoff_utc'),'issuer':'scheduled-collector'}
        issued=max(r.utc(now),r.utc())
        if r.save_daily(conn,target,index,features,kind=kind,generated_at=issued):
            major=pred[pred.Attraction.isin(settings['rides'])].copy()
            major.attrs=pred.attrs.copy()
            r.save_predictions(conn,major,'__ALL__',target_date=target,park=park,generated_at=issued)


def collect(destination, force=False, context=False):
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    os.environ['DISNEY_DB_DIR']=str(destination)
    os.environ['DISNEY_DISABLE_REMOTE_SYNC']='1'
    now=datetime.now(r.JST)
    if not force and not 8<=now.hour<22:
        return [{'status':'skipped','reason':'営業時間外（08:00〜22:00 JSTの収集窓）'}]
    weather=utils.get_weather(); results=[]; reports={}
    run_id=(os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')) if os.environ.get('GITHUB_RUN_ID') else r.utc().strftime('%Y%m%dT%H%M%S%f')
    if os.environ.get('COLLECTOR_SAMPLE_ID'): run_id+='-s'+os.environ['COLLECTOR_SAMPLE_ID']
    event=os.environ.get('COLLECTOR_EVENT',os.environ.get('GITHUB_EVENT_NAME','local'))
    admin_payload=json.loads(os.environ['ADMIN_EVENT_JSON']) if os.environ.get('ADMIN_EVENT_JSON','').strip() else None
    if admin_payload: durable_admin.validate(admin_payload)
    for park,base in utils.PARK_SETTINGS.items():
        settings=dict(base,park=park); start=r.iso(); started=time.monotonic()
        result={'park':park,'status':'failed'}
        try: conn,cursor=utils.connect_db(base['db'])
        except (sqlite3.Error,OSError) as exc:
            results.append({**result,'failure_class':'db_open','message':type(exc).__name__})
            continue
        latest=conn.execute('SELECT max(fetched_at_utc) FROM wait_times WHERE quality=?',('valid',)).fetchone()[0]
        if not force and latest and 0<=(r.utc()-r.utc(latest)).total_seconds()<300:
            results.append({'park':park,'status':'skipped','reason':'A current collection was persisted less than five minutes ago; no duplicate API request'})
            conn.close();continue
        with tempfile.TemporaryDirectory(dir=destination) as temp:
            before_path=Path(temp)/'before.db'; snapshot(conn,before_path)
            try:
                try:
                    frame,_=utils.fetch_wait_times(settings)
                    count=utils.save_wait_times(cursor,conn,frame,weather[0],weather[1],park)
                    usable=r.fresh_open(frame)
                    r.record_collection(conn,park,'success',f'{len(frame)}施設 / 有効営業中{len(usable)}施設',count)
                    result.update(status='success',received=len(frame),inserted=count,observed_at=frame.UpdatedUTC.max(),
                        open_positive=int(usable.Wait.gt(0).sum()),open_zero=int(usable.Wait.eq(0).sum()),
                        closed=int(frame.Open.eq(False).sum()),source_stale=int((pd.to_datetime(frame.UpdatedUTC,utc=True)<r.utc()-pd.Timedelta(minutes=20)).sum()))
                except Exception as exc:
                    r.record_collection(conn,park,'failed',str(exc)); result['message']=str(exc)
                    result['failure_class']='db_save' if isinstance(exc,sqlite3.Error) else 'api_fetch'
                if admin_payload and admin_payload['park']==park:
                    result['admin_acknowledged']=durable_admin.apply(conn,admin_payload)
                operations.source_check(conn,park,'weather','success' if r.finite(weather[0]) is not None else 'failed',weather[2],now.date(),'https://api.open-meteo.com/v1/forecast',observed_at=weather[4].attrs.get('observed_at_utc'))
                history=utils.load_history(conn)
                result['evaluated_predictions']=r.evaluate_predictions(conn,base['rides'])
                utils.update_daily_crowd_feedback(cursor,conn,history,settings,park)
                if r.finite(weather[0]) is not None: utils.save_weather_snapshot(cursor,conn,park,weather[0],weather[1],weather[2])
                if context:
                    utils.auto_collect_prediction_context(cursor,conn,park)
                    utils.auto_fetch_dpa_if_needed(cursor,conn,settings,park)
                    latest=conn.execute("SELECT max(checked_at_utc) FROM source_checks WHERE park=? AND source='ticket_price'",(park,)).fetchone()[0]
                    if not latest or r.utc()-r.utc(latest)>pd.Timedelta(hours=6):
                        prices,source=utils.fetch_ticket_prices()
                        operations.source_check(conn,park,'ticket_price','success' if prices else 'empty',source,now.date(),utils.CASTEL_TICKET_URL if 'Castel' in source else utils.OFFICIAL_TICKET_URL)
                context_checks(conn,park)
                persist_forecasts(conn,history,settings,park,weather,now)
                result['shadow_evaluated']=evaluation.evaluate(conn,history)
                result['shadow_issued']=evaluation.issue(conn,history,settings,park)
                reports[park]={'quality':coverage(history,park,settings['rides']),'evaluation':evaluation.report(conn),'sources':operations.freshness(conn)}
            except Exception as exc:
                result.update(status='failed',processing_error=str(exc),failure_class='db_save' if isinstance(exc,sqlite3.Error) else 'forecast_or_context')
            finally:
                result['duration_seconds']=round(time.monotonic()-started,3)
                with conn:
                    conn.execute('INSERT OR REPLACE INTO operation_runs VALUES (?,?,?,?,?,?,?)',(f'{park}|{run_id}',start,r.iso(),event,str(run_id),result['status'],json.dumps(result,ensure_ascii=False)))
                before=sqlite3.connect(before_path)
                try: result['journal']=write_delta(before,conn,destination,park,run_id)
                finally: before.close()
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)'); conn.close()
        results.append(result)
    (destination/'operational-report.json').write_text(json.dumps({'generated_at_utc':r.iso(),'parks':reports},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return results


def context_checks(conn,park):
    known={x[0] for x in conn.execute('SELECT run_key FROM source_checks')}
    for row in conn.execute('SELECT fetched_at,target_date,source,status,message FROM data_fetch_logs WHERE park=?',(park,)).fetchall():
        key=f'{park}|{row[2]}|{r.iso(row[0])}'
        if key not in known:
            operations.source_check(conn,park,row[2],row[3],row[4],row[1],utils.OFFICIAL_CALENDAR_URL if row[2]=='park_hours' else '',row[0])
    for row in conn.execute('SELECT fetched_at,target_date,source,status,message FROM dpa_fetch_logs WHERE park=?',(park,)).fetchall():
        key=f'{park}|dpa|{r.iso(row[0])}'
        if key not in known: operations.source_check(conn,park,'dpa',row[3],row[4],row[1],utils.PARK_SETTINGS[park].get('urtrip_url',row[2]),row[0])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,default=Path('.runtime'))
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--context',action='store_true')
    args=parser.parse_args()
    try: result=collect(args.data_dir,args.force,args.context)
    except Exception as exc: result=[{'status':'failed','message':str(exc)}]
    args.data_dir.mkdir(parents=True,exist_ok=True)
    (args.data_dir/'last-run.json').write_text(json.dumps({'generated_at_utc':r.iso(),'results':result},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(1 if any(x['status']=='failed' for x in result) else 0)
