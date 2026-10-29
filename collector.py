"""One bounded collection run for both parks. Scheduled independently of Streamlit."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys

import reliability as r
import utils
from persistence import snapshot


def collect(destination, force=False, context=False):
    os.environ['DISNEY_DB_DIR']=str(destination)
    now=datetime.now(r.JST)
    # Wide opening window (08:00-22:00 JST); no routine overnight requests.
    if not force and not 8 <= now.hour < 22:
        return [{'status':'skipped','reason':'営業時間外（08:00〜22:00 JSTの収集窓）'}]
    weather=utils.get_weather()
    results=[]
    for park,base in utils.PARK_SETTINGS.items():
        settings=dict(base,park=park)
        conn,cursor=utils.connect_db(base['db'])
        try:
            try:
                frame,_=utils.fetch_wait_times(settings)
                count=utils.save_wait_times(cursor,conn,frame,weather[0],weather[1],park)
                r.record_collection(conn,park,'success',f'{len(frame)}施設',count)
                results.append({'park':park,'status':'success','received':len(frame),'inserted':count,'observed_at':frame.UpdatedUTC.max()})
            except Exception as exc:
                r.record_collection(conn,park,'failed',str(exc))
                results.append({'park':park,'status':'failed','message':str(exc)})
            history=utils.load_history(conn)
            r.evaluate_predictions(conn,base['rides'])
            utils.update_daily_crowd_feedback(cursor,conn,history,settings,park)
            utils.save_weather_snapshot(cursor,conn,park,weather[0],weather[1],weather[2]) if r.finite(weather[0]) is not None else None
            if context:
                utils.auto_collect_prediction_context(cursor,conn,park)
                utils.auto_fetch_dpa_if_needed(cursor,conn,settings,park)
            # First successful job after 21:00 takes an immutable next-day snapshot.
            if now.hour==21:
                target=now.date()+timedelta(days=1)
                temp,rain,weather_source=utils.get_forecast_weather_for_date(weather[4],target,weather[0],weather[1])
                pred_history=utils.load_prediction_history(conn)
                index,pred,reasons=utils.predict_crowd_index_for_date(history,settings,target,temp,rain,pred_history,utils.load_daily_crowd_predictions(conn),park=park)
                r.save_predictions(conn,pred,'__ALL__',target_date=target,park=park)
                r.save_daily(conn,target,index,{'weather_source':weather_source,'reasons':reasons},kind='21:00')
                r.save_daily(conn,target,index,{'weather_source':weather_source,'reasons':reasons})
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            conn.close()
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,default=Path('.runtime'))
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--context',action='store_true')
    args=parser.parse_args()
    result=collect(args.data_dir,args.force,args.context)
    args.data_dir.mkdir(parents=True,exist_ok=True)
    (args.data_dir/'last-run.json').write_text(json.dumps({'generated_at_utc':r.iso(),'results':result},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    sys.exit(1 if any(x['status']=='failed' for x in result) else 0)
