"""Authenticated outbox -> existing Actions -> durable DB acknowledgement."""
import json
import os
import uuid
from datetime import date
import requests
import reliability as r

REPO='sorapanel7533/disney-crowd-ai'


def migrate(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS admin_events (event_id TEXT PRIMARY KEY,park TEXT,
            created_at_utc TEXT,payload_json TEXT,status TEXT,acknowledged_at_utc TEXT)''')


def validate(payload):
    if set(payload)!={'event_id','park','attraction','target_date','sellout_hour','created_at_utc'}: raise ValueError('Unexpected event schema')
    uuid.UUID(payload['event_id'])
    park=payload['park']
    if park not in r.PARK_IDS: raise ValueError('Unknown park')
    allowed={x['name'] for x in r.load_catalog().values() if x.get('park')==park or x.get('park_id')==r.PARK_IDS[park]}
    if payload['attraction'] not in allowed: raise ValueError('Unknown attraction')
    day=date.fromisoformat(payload['target_date'])
    created=r.utc(payload['created_at_utc'])
    if abs((day-created.tz_convert(r.JST).date()).days)>366: raise ValueError('Target date outside allowed range')
    value=r.finite(payload['sellout_hour'])
    if value is None or not 0<=value<=24: raise ValueError('Invalid sellout time')
    return payload


def enqueue(conn,park,attraction,target_date,sellout_hour,authorized=False):
    if not authorized: raise PermissionError('Administrator authorization required')
    migrate(conn)
    payload=validate({'event_id':str(uuid.uuid4()),'park':park,'attraction':attraction,'target_date':str(target_date),
        'sellout_hour':sellout_hour,'created_at_utc':r.iso()})
    with conn:
        conn.execute('INSERT INTO admin_events VALUES (?,?,?,?,?,NULL)',(payload['event_id'],park,payload['created_at_utc'],json.dumps(payload,ensure_ascii=False),'pending'))
    return payload


def dispatch(payload,token,authorized=False):
    if not authorized: raise PermissionError('Administrator authorization required')
    validate(payload)
    if not token: return 'not_configured'
    response=requests.post(f'https://api.github.com/repos/{REPO}/actions/workflows/collect.yml/dispatches',
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},
        json={'ref':'main','inputs':{'admin_event':json.dumps(payload,ensure_ascii=False)}},timeout=(3,10))
    if response.status_code!=204:
        # Never include response request headers/token or the response body.
        raise RuntimeError(f'GitHub dispatch HTTP {response.status_code}')
    return 'submitted' # Durable only after crowd-data acknowledgement is synced.


def apply(conn,payload):
    """Only called in trusted Actions after workflow/environment validation."""
    import utils
    validate(payload); migrate(conn)
    if conn.execute("SELECT 1 FROM admin_events WHERE event_id=? AND status='acknowledged'",(payload['event_id'],)).fetchone(): return 0
    utils.save_dpa_sellout(conn.cursor(),conn,payload['attraction'],payload['sellout_hour'],'manual',payload['target_date'])
    with conn:
        conn.execute('INSERT INTO admin_events VALUES (?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,acknowledged_at_utc=excluded.acknowledged_at_utc',
            (payload['event_id'],payload['park'],payload['created_at_utc'],json.dumps(payload,ensure_ascii=False),'acknowledged',r.iso()))
    return 1
