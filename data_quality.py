"""Auditable 15-minute coverage. Unknown states never become training truth."""
import pandas as pd
import reliability as r

SLOT_MINUTES = 15
MIN_DAY_COVERAGE = .75
MIN_DAY_HOURS = 8


def classify(frame, as_of=None):
    df=frame.copy()
    if df.empty:
        return df.assign(eligible=pd.Series(dtype=bool), exclusion=pd.Series(dtype=str))
    now=r.utc(as_of)
    for col in ['quality','observed_at_utc','fetched_at_utc','observation_key','park','source']:
        if col not in df: df[col]=None
    df['wait_time']=pd.to_numeric(df.wait_time,errors='coerce')
    df['is_open']=pd.to_numeric(df.get('is_open',pd.Series(index=df.index,dtype=float)),errors='coerce')
    df['stamp']=pd.to_datetime(df.observed_at_utc,utc=True,errors='coerce')
    df['fetched']=pd.to_datetime(df.fetched_at_utc,utc=True,errors='coerce')
    lag=(df.fetched-df.stamp).dt.total_seconds()/60
    reason=pd.Series('',index=df.index,dtype=str)
    def exclude(mask,label):
        reason.loc[mask & reason.eq('')]=label
    exclude(~df.quality.isin(['valid','verified_external']),'unverified_source')
    exclude(df.stamp.isna()|df.fetched.isna(),'missing_timestamp')
    exclude((df.stamp>now)|(df.fetched>now),'not_available_at_origin')
    if 'datetime' in df:
        # Compatibility column is explicitly naive JST in this schema.
        compat=pd.to_datetime(df.datetime,errors='coerce',format='mixed',utc=True)-pd.Timedelta(hours=9)
        exclude((compat-df.stamp).dt.total_seconds().abs().gt(60)|compat.isna(),'jst_utc_mismatch')
    exclude(lag.lt(-5),'future_source_timestamp')
    exclude(df.quality.eq('valid') & lag.gt(20),'stale_at_fetch')
    exclude(~df.is_open.isin([0,1]),'unknown_operating_state')
    exclude(df.is_open.eq(0),'closed_or_suspended')
    exclude(~df.wait_time.between(0,300),'invalid_wait')
    # Canonical ride name is the compatibility key for old/new API IDs.
    duplicate=pd.Series(False,index=df.index)
    candidates=df[reason.eq('')].copy()
    candidates['_priority']=candidates.quality.map({'valid':0,'verified_external':1})
    candidates=candidates.sort_values(['_priority','fetched'])
    repeated=candidates.duplicated(['park','attraction','stamp'],keep='first')
    duplicate.loc[candidates.index]=repeated
    exclude(duplicate,'duplicate_observation')
    df['exclusion']=reason
    df['eligible']=reason.eq('')
    df['slot']=df.stamp.dt.tz_convert(r.JST).dt.floor('15min')
    df['day']=df.slot.dt.strftime('%Y-%m-%d')
    df['hour']=df.slot.dt.hour
    return df


def training_rows(frame,as_of=None,allow_legacy_reference=False):
    classified=classify(frame,as_of)
    if classified.empty: return frame.copy()
    selected=classified.eligible
    if allow_legacy_reference:
        # Retained for continuity only; excluded from formal ground truth/coverage.
        legacy=classified.quality.fillna('legacy_unverified').eq('legacy_unverified')
        legacy &= classified.wait_time.between(1,300)&(classified.is_open.eq(1) | (classified.is_open.isna() & ('is_open' not in frame)))
        legacy_time=pd.to_datetime(classified.get('datetime'),errors='coerce',format='mixed')
        legacy &= legacy_time<r.utc(as_of).tz_convert(r.JST).tz_localize(None)
        selected |= legacy
    return frame.loc[selected,frame.columns].copy()


def coverage(frame,park,major_rides,as_of=None,open_hour=9,close_hour=21):
    """Denominator is an explicit assumed window, never inferred official hours.

    Reports both elapsed-day coverage and full-day coverage. Only completed days
    with sufficient verified major-ride samples count as evaluation-ready days.
    """
    now=r.utc(as_of).tz_convert(r.JST)
    df=classify(frame,now)
    if df.empty: return {'summary':{'park':park,'eligible_evaluation_days':0},'daily':[],'attractions':[],'hourly':[]}
    df=df[df.park.eq(park)|df.park.isna()].copy()
    verified=df[df.quality.isin(['valid','verified_external']) & df.stamp.notna()].copy()
    verified=verified[(verified.stamp<=r.utc(now)) & (verified.fetched<=r.utc(now))]
    names=sorted(set(verified.attraction.dropna())|set(major_rides))
    days=sorted(set(verified.day.dropna()))
    records=[]; hours=[]
    for day in days:
        start=pd.Timestamp(day,tz=r.JST)+pd.Timedelta(hours=open_hour)
        end=pd.Timestamp(day,tz=r.JST)+pd.Timedelta(hours=close_hour)
        full=pd.date_range(start,end,freq='15min',inclusive='left')
        elapsed=full[full+pd.Timedelta(minutes=15)<=now]
        for name in names:
            rows=verified[(verified.day==day)&(verified.attraction==name)]
            state_slots=set(rows.loc[rows.exclusion.isin(['','closed_or_suspended']),'slot'])&set(elapsed)
            good=set(rows.loc[rows.eligible,'slot'])&set(elapsed)
            completed=end<=now
            fraction=len(good)/len(full) if len(full) else 0.
            records.append({'park':park,'date':day,'attraction':name,'major':name in major_rides,
                'expected_elapsed_slots':len(elapsed),'expected_full_day_slots':len(full),
                'observed_state_slots':len(state_slots),'valid_open_slots':len(good),
                'elapsed_open_coverage':len(good)/len(elapsed) if len(elapsed) else None,
                'full_day_open_coverage':fraction,'missing_state_rate':1-len(state_slots)/len(elapsed) if len(elapsed) else None,
                'valid_hours':len({s.hour for s in good}),
                'evaluation_ready':bool(completed and fraction>=MIN_DAY_COVERAGE and len({s.hour for s in good})>=MIN_DAY_HOURS)})
            for h in range(open_hour,close_hour):
                expected={s for s in elapsed if s.hour==h}
                hours.append({'date':day,'attraction':name,'hour':h,'expected_slots':len(expected),
                    'valid_slots':len(good&expected),'coverage':len(good&expected)/len(expected) if expected else None})
    detail=pd.DataFrame(records)
    daily=[]; attraction=[]
    if len(detail):
        for day,items in detail.groupby('date'):
            majors=items[items.major]
            daily.append({'date':day,'expected_slots':int(items.expected_elapsed_slots.sum()),
                'valid_open_slots':int(items.valid_open_slots.sum()),'state_slots':int(items.observed_state_slots.sum()),
                'open_coverage':float(items.valid_open_slots.sum()/items.expected_elapsed_slots.sum()) if items.expected_elapsed_slots.sum() else None,
                'evaluation_ready':bool(len(majors)==len(major_rides) and len(major_rides)>0 and majors.evaluation_ready.all())})
        for name,items in detail.groupby('attraction'):
            attraction.append({'attraction':name,'major':bool(items.major.iloc[0]),'days_with_valid_open':int(items.valid_open_slots.gt(0).sum()),
                'evaluation_ready_days':int(items.evaluation_ready.sum()),'valid_slots':int(items.valid_open_slots.sum()),
                'expected_elapsed_slots':int(items.expected_elapsed_slots.sum()),'valid_hours':int(items.valid_hours.sum())})
    latest=verified.stamp.max()
    summary={'park':park,'as_of_utc':r.iso(now),'rows':len(df),'verified_rows':len(verified),
        'valid_open_rows':int(df.eligible.sum()),'open_positive_rows':int((df.eligible & df.wait_time.gt(0)).sum()),
        'open_zero_rows':int((df.eligible & df.wait_time.eq(0)).sum()),'legacy_reference_rows':int(df.quality.fillna('legacy_unverified').eq('legacy_unverified').sum()),
        'exclusions':df.exclusion[df.exclusion.ne('')].value_counts().to_dict(),
        'semantic_duplicates':int(df.duplicated(['park','attraction','stamp']).where(df.stamp.notna(),False).sum()),
        'legacy_exact_duplicates':int(df[df.quality.fillna('legacy_unverified').eq('legacy_unverified')].duplicated(['datetime','attraction']).sum()) if 'datetime' in df else None,
        'days_with_open_observations':int(df.loc[df.eligible,'day'].nunique()),
        'eligible_evaluation_days':sum(x['evaluation_ready'] for x in daily),
        'latest_observed_utc':r.iso(latest) if pd.notna(latest) else None,
        'freshness_minutes':float((r.utc(now)-latest).total_seconds()/60) if pd.notna(latest) else None,
        'denominator':f'Assumed {open_hour:02d}:00–{close_hour:02d}:00 JST, 15-minute slots; not verified official hours',
        'ready_rule':f'Completed day; each major ride >= {MIN_DAY_COVERAGE:.0%} of full-day slots and >= {MIN_DAY_HOURS} hours'}
    return {'summary':summary,'daily':daily,'attractions':attraction,'hourly':hours,'daily_attraction':records}
