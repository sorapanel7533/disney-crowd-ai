"""Expanding-window comparisons. Never train on/after the forecast origin."""
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import importlib.util
import json
import sqlite3
import subprocess

import numpy as np
import pandas as pd
import utils


def metrics(rows):
    error=rows.predicted-rows.actual
    return {'n':len(rows),'MAE':float(error.abs().mean()),'RMSE':float(np.sqrt((error**2).mean())),
            'Bias_predicted_minus_actual':float(error.mean()),'days':int(rows.target_day.nunique())}


def weighted_median(values,weights):
    order=np.argsort(values)
    values,weights=np.asarray(values)[order],np.asarray(weights)[order]
    return float(values[np.searchsorted(np.cumsum(weights),weights.sum()/2)])


def baseline(train,ride,target,weighted=False):
    rows=train[train.attraction.eq(ride)].copy()
    if rows.empty: return np.nan
    same=rows[(rows.datetime.dt.day_of_week==target.day_of_week)&(rows.datetime.dt.hour==target.hour)]
    if not weighted:
        if same.empty: same=rows[rows.datetime.dt.hour==target.hour]
        if same.empty: same=rows
        return float(same.wait_time.median())
    # Each ride/day/hour is one sample. Recency, weekday and time proximity are not additive bonuses.
    age=(target-rows.datetime).dt.total_seconds()/86400
    hour_distance=(rows.datetime.dt.hour-target.hour).abs()
    weights=np.exp(-age/28)*np.exp(-hour_distance/2)*np.where(rows.datetime.dt.day_of_week==target.day_of_week,2.,1.)
    return weighted_median(rows.wait_time,weights)


def load_legacy(path):
    spec=importlib.util.spec_from_file_location('legacy_reference',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def backtest(raw,settings,legacy,diagnostic=False):
    data=raw.copy()
    data['datetime']=pd.to_datetime(data.datetime)
    data['wait_time']=pd.to_numeric(data.wait_time,errors='coerce')
    data=data[data.wait_time.between(1,300)] # legacy zero has unknown operational status
    if 'is_open' in data: data=data[data.is_open.eq(1)]
    data=data.drop_duplicates(['datetime','attraction'])
    data['datetime']=data.datetime.dt.floor('h')
    data=data.groupby(['attraction','datetime'],as_index=False).wait_time.mean()
    data['date']=data.datetime.dt.date; data['hour']=data.datetime.dt.hour
    days=sorted(data.date.unique())
    origins=[]
    if diagnostic:
        for d in days:
            for hour in [12,14,16,18]: origins.append((pd.Timestamp(d)+pd.Timedelta(hours=hour),0))
    else:
        for target_day in days[14:]:
            for horizon in [1,7,30]:
                origin=pd.Timestamp(target_day)-pd.Timedelta(days=horizon)+pd.Timedelta(hours=21)
                origins.append((origin,horizon))
    results=[]
    for origin,horizon in origins:
        train=data[data.datetime<origin].copy()
        if train.empty or (not diagnostic and train.date.nunique()<14): continue
        if diagnostic:
            test=data[(data.date==origin.date()) & data.datetime.between(origin,origin+pd.Timedelta(hours=2),inclusive='left')]
        else:
            target_day=(origin+pd.Timedelta(days=horizon)).date()
            test=data[data.date==target_day]
        if test.empty: continue
        target_day=test.date.iloc[0]
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                return origin.to_pydatetime().replace(tzinfo=utils.JST) if tz else origin.to_pydatetime()
        old_dt,old_new_dt=legacy.datetime,utils.datetime
        legacy.datetime=utils.datetime=FrozenDateTime
        try:
            # No future actual weather/tickets/current waits supplied to any model.
            old=legacy.predict_wait_times_for_date(train,settings,target_day,np.nan,np.nan,pd.DataFrame())
            corrected=utils.predict_wait_times_for_date(train,settings,target_day,np.nan,np.nan,pd.DataFrame())
        finally:
            legacy.datetime,utils.datetime=old_dt,old_new_dt
        mappings={name:frame[frame.Minute.eq(0)].set_index(['Attraction','Hour'])['Predicted Wait']
                  for name,frame in [('legacy',old),('legacy_corrected',corrected)]}
        for row in test.itertuples():
            values={'weekday_hour_median':baseline(train,row.attraction,row.datetime),
                    'weighted_history_candidate':baseline(train,row.attraction,row.datetime,True)}
            for name,mapping in mappings.items(): values[name]=mapping.get((row.attraction,row.hour),np.nan)
            for model,value in values.items():
                if np.isfinite(value): results.append({'model':model,'origin':str(origin),'target_day':str(row.date),
                    'target_hour':int(row.hour),'attraction':row.attraction,'horizon_days':horizon,
                    'predicted':float(value),'actual':float(row.wait_time)})
    return pd.DataFrame(results), {'available_days':len(days),'first_day':str(days[0]) if days else None,
                                  'last_day':str(days[-1]) if days else None,'observations':len(raw),'hourly_samples':len(data)}


def report(data):
    if data.empty: return {'metrics':{},'breakdowns':{}}
    result={'metrics':{name:metrics(rows) for name,rows in data.groupby('model')},'breakdowns':{}}
    for dimension in ['attraction','target_hour','target_day','horizon_days','origin']:
        result['breakdowns'][dimension]=[{'model':name,dimension:str(key),**metrics(rows)} for (name,key),rows in data.groupby(['model',dimension])]
    base=result['metrics'].get('legacy',{}).get('MAE')
    for item in result['metrics'].values():
        item['improvement_vs_legacy_percent']=(base-item['MAE'])/base*100 if base else None
    return result


def run(legacy_path,output):
    legacy=load_legacy(legacy_path)
    result={'protocol':'Expanding origin; target observations strictly after origin; daily/hourly aggregation; no future weather, tickets or errors',
            'adopted':'legacy-corrected-v1 (correctness repairs; accuracy improvement not established)', 'parks':{}}
    for park,settings in utils.PARK_SETTINGS.items():
        with sqlite3.connect(settings['db']) as conn: raw=pd.read_sql_query('SELECT * FROM wait_times',conn)
        formal,info=backtest(raw,settings,legacy)
        intra,_=backtest(raw,settings,legacy,diagnostic=True)
        result['parks'][park]={'data':info,'formal_daily_validation':report(formal),
                              'intraday_diagnostic_only':report(intra),
                              'adoption_allowed':False if formal.empty else None,
                              'limitation':'Only one observed day. Intraday diagnostics do not establish next-day, weekday, seasonal or 7/30-day accuracy.',
                              'machine_learning':'Deferred until >=56 distinct observed days and independent holdout dates.'}
        if not intra.empty: intra.to_csv(output.with_name(park+'-intraday-diagnostic.csv'),index=False)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({p:{'data':v['data'],'diagnostic':v['intraday_diagnostic_only']['metrics']} for p,v in result['parks'].items()},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--legacy',type=Path,required=True); parser.add_argument('--output',type=Path,default=Path('docs/model-comparison.json'))
    args=parser.parse_args(); run(args.legacy,args.output)
