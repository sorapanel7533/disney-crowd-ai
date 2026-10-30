"""Frozen shadow-only candidate: weighted median plus bounded recent level/trend."""
import numpy as np
import pandas as pd


def predict(training,targets,origin,params):
    origin=pd.Timestamp(origin).tz_convert('Asia/Tokyo').tz_localize(None)
    output={}
    for name,target in targets:
        rows=training[training.attraction.eq(name)].copy()
        rows=rows[rows.datetime<origin]
        if rows.empty: continue
        target_local=pd.Timestamp(target).tz_convert('Asia/Tokyo').tz_localize(None)
        def median(stamp):
            hourly=rows.assign(datetime=rows.datetime.dt.floor('h')).groupby('datetime',as_index=False).wait_time.mean()
            age=(stamp-hourly.datetime).dt.total_seconds()/86400
            weights=np.exp(-age/params['half_life_scale_days'])*np.exp(-(hourly.datetime.dt.hour-stamp.hour).abs()/params['hour_decay_scale'])*np.where(hourly.datetime.dt.day_of_week==stamp.day_of_week,params['same_weekday_weight'],1.)
            order=np.argsort(hourly.wait_time.to_numpy());w=weights.to_numpy()[order];v=hourly.wait_time.to_numpy()[order]
            return float(v[np.searchsorted(np.cumsum(w),w.sum()/2)])
        baseline=median(target_local)
        recent=rows[(rows.datetime>=origin-pd.Timedelta(minutes=params['recent_minutes']))&(rows.datetime.dt.date==origin.date())].sort_values('datetime')
        adjustment=0.
        if len(recent)>=2:
            last=recent.iloc[-1];first=recent.iloc[-2]
            interval=(last.datetime-first.datetime).total_seconds()/3600
            slope=np.clip((last.wait_time-first.wait_time)/interval,-20,20) if interval>0 else 0
            lead=max(0,(target_local-origin).total_seconds()/3600)
            adjustment=(params['level_weight']*(last.wait_time-median(last.datetime))+params['trend_weight']*slope)*np.exp(-lead/params['decay_hours'])
        output[(name,target)]=float(np.clip(baseline+np.clip(adjustment,-params['max_adjustment'],params['max_adjustment']),0,300))
    return output
