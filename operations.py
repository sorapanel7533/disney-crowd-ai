"""Expected schedule slots, external-source freshness and bounded operations logs."""
import json
import pandas as pd
import reliability as r


def schedule_audit(runs,start,end,grace_minutes=30):
    start,end=r.utc(start),r.utc(end)
    slots=[]
    for stamp in pd.date_range(start.floor('D')-pd.Timedelta(days=1),end.ceil('D'),freq='1min'):
        local=stamp.tz_convert(r.JST)
        if 8<=local.hour<22 and local.minute in (7,22,37,52) and start<=stamp<=end:
            slots.append(stamp)
    scheduled=[x for x in runs if x.get('event')=='schedule']
    matches={}; extras=[]
    for run in sorted(scheduled,key=lambda x:x['created_at']):
        stamp=r.utc(run['created_at'])
        # Attribution is inferred; GitHub does not supply the original due time.
        candidates=[s for s in slots if s<=stamp<s+pd.Timedelta(minutes=15)]
        if not candidates: extras.append(run.get('id')); continue
        slot=candidates[-1]
        matches.setdefault(slot,[]).append(run)
    rows=[]
    for slot in slots:
        found=matches.get(slot,[])
        if found:
            run=found[0]; delay=(r.utc(run['created_at'])-slot).total_seconds()/60
            rows.append({'due_utc':r.iso(slot),'status':run.get('conclusion') or run.get('status'),
                'run_id':run.get('id'),'inferred_delay_minutes':round(delay,2),'attribution':'inferred_previous_slot'})
        else:
            rows.append({'due_utc':r.iso(slot),'status':'not_observed' if slot+pd.Timedelta(minutes=grace_minutes)<=end else 'within_grace',
                'run_id':None,'inferred_delay_minutes':None,'attribution':'absence_of_run_is_not_a_failed_job'})
    completed=[x for x in runs if x.get('status')=='completed']
    failed=[x for x in completed if x.get('conclusion') not in ('success','skipped','cancelled')]
    return {'start_utc':r.iso(start),'end_utc':r.iso(end),'grace_minutes':grace_minutes,
        'expected_slots':len(rows),'observed_scheduled_runs':len(scheduled),
        'not_observed_slots':sum(x['status']=='not_observed' for x in rows),
        'completed_jobs':len(completed),'failed_jobs':len(failed),
        'failure_rate_of_executed_jobs':len(failed)/len(completed) if completed else None,
        'note':'Missing slots may be delayed/dropped/not dispatched. They are not HTTP failures. Delay attribution is inferred.',
        'slots':rows,'unmatched_runs':extras}


def migrate(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS operation_runs
            (run_key TEXT PRIMARY KEY,started_at_utc TEXT,finished_at_utc TEXT,event TEXT,github_run_id TEXT,
             status TEXT,details_json TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS source_checks
            (run_key TEXT PRIMARY KEY,checked_at_utc TEXT,park TEXT,source TEXT,status TEXT,
             target_date TEXT,source_url TEXT,message TEXT)''')
        if 'observed_at_utc' not in {x[1] for x in conn.execute('PRAGMA table_info(source_checks)')}:
            conn.execute('ALTER TABLE source_checks ADD COLUMN observed_at_utc TEXT')


def source_check(conn,park,source,status,message='',target_date=None,url='',now=None,observed_at=None):
    migrate(conn)
    stamp=r.iso(now); key=f'{park}|{source}|{stamp}'
    with conn:
        conn.execute('INSERT OR IGNORE INTO source_checks VALUES (?,?,?,?,?,?,?,?,?)',
            (key,stamp,park,source,status,str(target_date or ''),url,str(message)[:1000],r.iso(observed_at) if observed_at is not None else None))


def freshness(conn,now=None):
    migrate(conn); now=r.utc(now)
    rows=pd.read_sql_query('SELECT * FROM source_checks ORDER BY checked_at_utc',conn)
    if rows.empty: return []
    output=[]
    limits={'weather':60,'ticket_price':1440,'park_hours':1440,'show_schedules':1440,'dpa':1440,'event_signals':1440}
    for (park,source),items in rows.groupby(['park','source']):
        latest=items.iloc[-1]; age=(now-r.utc(latest.checked_at_utc)).total_seconds()/60
        data_age=(now-r.utc(latest.observed_at_utc)).total_seconds()/60 if pd.notna(latest.observed_at_utc) else None
        success=items.status.eq('success')
        last_good=items[success].iloc[-1] if success.any() else None
        output.append({'park':park,'source':source,'latest_attempt_utc':latest.checked_at_utc,
            'source_observed_utc':latest.observed_at_utc,'target_date':latest.target_date,
            'source_age_minutes':round(data_age,1) if data_age is not None else None,
            'status':latest.status,'age_minutes':round(age,1),'stale':age>limits.get(source,1440) or (data_age is not None and (data_age>limits.get(source,1440) or data_age < -5)),
            'last_success_utc':last_good.checked_at_utc if last_good is not None else None,
            'attempts':len(items),'failed_attempts':int(items.status.isin(['failed','empty']).sum()),
            'failure_rate':float(items.status.isin(['failed','empty']).mean()),
            'estimated_attempts':int(items.status.eq('estimated').sum()),'unavailable_or_estimated_rate':float(items.status.ne('success').mean()),'url':latest.source_url,
            'message':latest.message,'sample_note':'Observed attempts only; daily caching can suppress attempts'})
    return output
