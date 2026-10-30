"""Frozen comparison models; explicit origins and guarded production selection."""
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
from threading import RLock
import numpy as np
import pandas as pd
import reliability as r
from data_quality import training_rows

ROOT=Path(__file__).with_name('models')
MODELS=('legacy-original-v0','legacy-corrected-v1','weekday-hour-median-v1','weighted-history-candidate-v1')
LOCK=RLock()


def registry():
    return json.loads((ROOT/'registry.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=2)
def frozen(version):
    item=registry()['versions'][version]; path=ROOT/item['file']
    if hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=item['sha256']:
        raise ValueError('Frozen model source hash mismatch')
    spec=importlib.util.spec_from_file_location(version.replace('-','_'),path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def source_fingerprint(version):
    return hashlib.sha256(json.dumps(registry()['versions'][version],sort_keys=True).encode()).hexdigest()


@contextmanager
def at_origin(module,origin):
    origin=r.utc(origin).tz_convert(r.JST)
    class OriginDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            value=origin.to_pydatetime()
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    with LOCK:
        previous=module.datetime; module.datetime=OriginDateTime
        try: yield
        finally: module.datetime=previous


def prepare(frame,origin,allow_legacy=False):
    df=training_rows(frame,origin,allow_legacy)
    if df.empty: return df
    df['datetime']=pd.to_datetime(df.datetime,errors='coerce',format='mixed')
    local=r.utc(origin).tz_convert(r.JST).tz_localize(None)
    # fetched_at is checked by training_rows as well as observation time.
    df=df[df.datetime<local].copy()
    if df.empty: return df
    df['datetime']=df.datetime.dt.floor('15min')
    df=df.groupby(['attraction','datetime'],as_index=False).wait_time.mean()
    df['date']=df.datetime.dt.date; df['hour']=df.datetime.dt.hour
    df['is_open']=1
    return df


def predict(version,training,settings,targets,origin):
    """Return one matched forecast per ride/hour. No future weather/price/feedback."""
    if version not in MODELS: raise ValueError('Unknown model version')
    if training.empty: return {}
    result={}
    if version.startswith('legacy-'):
        module=frozen(version)
        with at_origin(module,origin):
            for day in sorted({pd.Timestamp(t).date() for _,t in targets}):
                out=module.predict_wait_times_for_date(training,settings,day,np.nan,np.nan,pd.DataFrame(),
                    attraction_list=sorted({name for name,t in targets if pd.Timestamp(t).date()==day})) if version=='legacy-corrected-v1' else module.predict_wait_times_for_date(training,settings,day,np.nan,np.nan,pd.DataFrame())
                for row in out.to_dict('records'):
                    if row.get('Minute',0)==0:
                        stamp=pd.Timestamp(day).replace(hour=int(row['Hour'])).tz_localize(r.JST).tz_convert('UTC')
                        result[(row['Attraction'],r.iso(stamp))]=float(row['Predicted Wait'])
        return {key:result[key] for key in targets if key in result}
    params=registry()['versions'][version]
    for name,target in targets:
        rows=training[training.attraction.eq(name)].copy()
        if rows.empty: continue
        # One ride/day/hour contributes once, irrespective of sampling frequency.
        rows['datetime']=rows.datetime.dt.floor('h')
        rows=rows.groupby('datetime',as_index=False).wait_time.mean()
        stamp=r.utc(target).tz_convert(r.JST).tz_localize(None)
        if version=='weekday-hour-median-v1':
            same=rows[(rows.datetime.dt.day_of_week==stamp.day_of_week)&(rows.datetime.dt.hour==stamp.hour)]
            if same.empty: same=rows[rows.datetime.dt.hour==stamp.hour]
            if same.empty: same=rows
            value=float(same.wait_time.median())
        else:
            age=(stamp-rows.datetime).dt.total_seconds()/86400
            weights=np.exp(-age/params['half_life_scale_days'])*np.exp(-(rows.datetime.dt.hour-stamp.hour).abs()/params['hour_decay_scale'])*np.where(rows.datetime.dt.day_of_week==stamp.day_of_week,params['same_weekday_weight'],1.)
            order=np.argsort(rows.wait_time.to_numpy()); values=rows.wait_time.to_numpy()[order]; weights=weights.to_numpy()[order]
            value=float(values[np.searchsorted(np.cumsum(weights),weights.sum()/2)])
        result[(name,target)]=value
    return result


def adoption_gate(report):
    """14 dates is a minimum, not sufficient proof. Keep each park/cohort separate."""
    reasons=[]
    for park in ('Disneyland','DisneySea'):
        for cohort in ('major','nonmajor'):
            x=report.get(park,{}).get(cohort,{})
            if x.get('independent_days',0)<14: reasons.append(f'{park}/{cohort}: fewer than 14 independent evaluation dates')
            if x.get('periods_improved',0)<3: reasons.append(f'{park}/{cohort}: fewer than 3 improving disjoint periods')
            if x.get('mae_improvement',0)<.10: reasons.append(f'{park}/{cohort}: MAE improvement below 10%')
            if not x.get('paired_complete'): reasons.append(f'{park}/{cohort}: incomplete paired predictions')
            if not x.get('day_bootstrap_lower_bound_positive'): reasons.append(f'{park}/{cohort}: day-level uncertainty includes no improvement')
    return {'allowed':not reasons,'reasons':reasons,'decision':'retain legacy-corrected-v1' if reasons else 'eligible for explicit reviewed release'}


def validate_active(version,evidence=None):
    if version=='legacy-corrected-v1': return version
    if version!='weighted-history-candidate-v1' or not adoption_gate(evidence or {})['allowed']:
        raise ValueError('Model promotion lacks independent paired multi-period evidence')
    return version
