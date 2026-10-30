"""Validated observations and immutable, target-dated forecast storage.

Legacy tables/rows remain intact. UTC columns are authoritative for new rows;
the original datetime column remains JST for compatibility with existing charts.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import math
import sqlite3

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = ZoneInfo('Asia/Tokyo')
UTC = timezone.utc
PARK_IDS = {'Disneyland': 274, 'DisneySea': 275}
MODEL_VERSION = 'legacy-corrected-v1'
WAIT_COLUMNS = ['Attraction', 'Wait', 'Open', 'RideID', 'ParkID', 'AreaID',
                'Area', 'UpdatedUTC', 'FetchedUTC', 'Quality']
LOG = logging.getLogger(__name__)


def utc(value=None):
    stamp = pd.Timestamp(value if value is not None else datetime.now(UTC))
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(JST)
    return stamp.tz_convert('UTC')


def iso(value=None):
    return utc(value).isoformat()


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_queue(data, park, fetched_at=None):
    if not isinstance(data, dict):
        raise ValueError('Queue-Times: JSON root must be an object')
    if not any(k in data for k in ('lands', 'rides')):
        raise ValueError('Queue-Times: unknown JSON schema (no lands/rides)')
    candidates = []
    lands, rides = data.get('lands', []), data.get('rides', [])
    if not isinstance(lands, list) or not isinstance(rides, list):
        raise ValueError('Queue-Times: lands/rides must be arrays')
    for land in lands:
        if not isinstance(land, dict) or not isinstance(land.get('rides', []), list):
            raise ValueError('Queue-Times: invalid land schema')
        candidates.extend((r, land) for r in land.get('rides', []))
    candidates.extend((r, {}) for r in rides)
    unique = {}
    fetched = iso(fetched_at)
    for ride, land in candidates:
        if not isinstance(ride, dict) or not isinstance(ride.get('id'), int) or not ride.get('name'):
            LOG.warning('Ignoring malformed Queue-Times ride without ID/name')
            continue
        stamp = None
        try:
            stamp = iso(ride['last_updated']) if ride.get('last_updated') else None
        except (ValueError, TypeError):
            pass
        wait = finite(ride.get('wait_time'))
        if wait is not None and not 0 <= wait <= 300:
            wait = None
        opened = ride.get('is_open') if isinstance(ride.get('is_open'), bool) else None
        quality = 'valid' if stamp and opened is not None and (wait is not None or not opened) else 'incomplete'
        if stamp and utc(stamp) > utc(fetched) + pd.Timedelta(minutes=5):
            quality = 'future_timestamp'
        row = dict(Attraction=' '.join(str(ride['name']).split()), Wait=wait, Open=opened,
                   RideID=ride['id'], ParkID=PARK_IDS[park], AreaID=land.get('id'),
                   Area=land.get('name', ''), UpdatedUTC=stamp, FetchedUTC=fetched, Quality=quality)
        previous = unique.get(ride['id'])
        if previous is None or (stamp or '') > (previous['UpdatedUTC'] or ''):
            if previous and not row['Area']:
                row['Area'], row['AreaID'] = previous['Area'], previous['AreaID']
            unique[ride['id']] = row
    return pd.DataFrame(unique.values(), columns=WAIT_COLUMNS)


def fetch_queue(settings):
    park = settings.get('park') or ('DisneySea' if '/275/' in settings['url'] else 'Disneyland')
    session = requests.Session()
    retry = Retry(total=1, backoff_factor=.3, status_forcelist=[429, 502, 503, 504],
                  allowed_methods=['GET'], respect_retry_after_header=False)
    session.mount('https://', HTTPAdapter(max_retries=retry))
    try:
        response = session.get(settings['url'], timeout=(3, 6),
                               headers={'User-Agent': 'DisneyCrowdAI/1.0 (+https://github.com/sorapanel7533/disney-crowd-ai)'})
        response.raise_for_status()
        frame = parse_queue(response.json(), park)
        if frame.empty:
            raise ValueError('Queue-Times: empty attraction response')
        # Canonical names are keyed by stable IDs, including seasonal name changes.
        catalog = load_catalog()
        frame['Attraction'] = frame.apply(lambda r: catalog.get(str(r.RideID), {}).get('name', r.Attraction), axis=1)
        return frame, frame[frame.Attraction.isin(settings['rides'])].copy()
    except (requests.RequestException, ValueError) as exc:
        LOG.warning('Queue-Times park=%s url=%s error=%s', park, settings['url'], exc)
        raise
    finally:
        session.close()


def load_catalog():
    from pathlib import Path
    path = Path(__file__).with_name('attractions.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def japanese_name(name):
    for row in load_catalog().values():
        if row['name'] == name:
            return row.get('ja', name)
    return name


def migrate(conn):
    additions = {
        'wait_times': {'ride_id':'INTEGER', 'park_id':'INTEGER', 'area':'TEXT',
                       'observed_at_utc':'TEXT', 'fetched_at_utc':'TEXT',
                       'quality':"TEXT DEFAULT 'legacy_unverified'", 'observation_key':'TEXT'},
        'predictions': {'generated_at_utc':'TEXT', 'target_at_utc':'TEXT', 'park':'TEXT',
                        'model':'TEXT', 'model_version':'TEXT', 'feature_cutoff_utc':'TEXT',
                        'features_json':'TEXT', 'prediction_key':'TEXT',
                        'evaluation_status':"TEXT DEFAULT 'legacy_target_unknown'", 'evaluated_at_utc':'TEXT'},
        'daily_crowd_predictions': {'generated_at_utc':'TEXT', 'model_version':'TEXT',
                                    'forecast_key':'TEXT', 'features_json':'TEXT', 'forecast_kind':'TEXT'},
    }
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        for table, columns in additions.items():
            existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            for name, kind in columns.items():
                if name not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_observation ON wait_times(observation_key) WHERE observation_key IS NOT NULL')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_prediction ON predictions(prediction_key) WHERE prediction_key IS NOT NULL')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_daily_forecast ON daily_crowd_predictions(forecast_key) WHERE forecast_key IS NOT NULL')
        conn.execute('CREATE INDEX IF NOT EXISTS history_time_ride ON wait_times(datetime, attraction)')
        conn.execute('CREATE INDEX IF NOT EXISTS prediction_target ON predictions(target_at_utc, attraction)')
        conn.execute('CREATE TABLE IF NOT EXISTS collection_runs (run_key TEXT PRIMARY KEY, fetched_at_utc TEXT, park TEXT, status TEXT, message TEXT, saved_count INTEGER)')
        conn.execute('CREATE TABLE IF NOT EXISTS dpa_revisions (revised_at_utc TEXT, original_row_json TEXT)')
    # Additive only. New operational tables also participate in snapshot merging.
    import operations, continuous_evaluation, durable_admin, ocr_review
    for module in (operations,continuous_evaluation,durable_admin,ocr_review):
        module.migrate(conn)


def save_observations(conn, frame, temperature, rain, park):
    saved = 0
    with conn:
        for row in frame.to_dict('records'):
            if row.get('Quality') != 'valid' or not row.get('UpdatedUTC'):
                continue
            stamp = utc(row['UpdatedUTC'])
            key = f"queue-times|{PARK_IDS[park]}|{row['RideID']}|{stamp.isoformat()}"
            c = conn.execute('''INSERT OR IGNORE INTO wait_times
                (datetime,attraction,wait_time,temperature,rain,is_open,park,source,
                 ride_id,park_id,area,observed_at_utc,fetched_at_utc,quality,observation_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (stamp.tz_convert(JST).strftime('%Y-%m-%d %H:%M:%S'), row['Attraction'], finite(row['Wait']),
                 finite(temperature), finite(rain), int(row['Open']), park, 'queue-times',
                 int(row['RideID']), PARK_IDS[park], row['Area'], stamp.isoformat(),
                 row['FetchedUTC'], row['Quality'], key))
            saved += c.rowcount
    return saved


def load_history(conn):
    frame = pd.read_sql_query('SELECT * FROM wait_times ORDER BY datetime', conn)
    frame['datetime'] = pd.to_datetime(frame['datetime'], errors='coerce', format='mixed')
    frame['hour'] = frame.datetime.dt.hour
    frame['date'] = frame.datetime.dt.date
    frame['wait_time'] = pd.to_numeric(frame.wait_time, errors='coerce')
    # Keep closed/missing records for graph gaps and operational diagnosis.
    return frame.dropna(subset=['datetime'])


def last_snapshot(conn, park):
    frame = pd.read_sql_query('''SELECT attraction AS Attraction,wait_time AS Wait,
        is_open AS Open,ride_id AS RideID,park_id AS ParkID,area AS Area,
        observed_at_utc AS UpdatedUTC,fetched_at_utc AS FetchedUTC,quality AS Quality
        FROM wait_times WHERE park=? AND fetched_at_utc=(SELECT MAX(fetched_at_utc)
        FROM wait_times WHERE park=? AND source='queue-times')''', conn, params=(park,park))
    if frame.empty:
        return pd.DataFrame(columns=WAIT_COLUMNS)
    frame['Open'] = frame.Open.eq(1)
    return frame


def fresh_open(frame, now=None):
    if frame.empty:
        return frame.copy()
    keep = frame.Open.eq(True) & pd.to_numeric(frame.Wait, errors='coerce').between(0,300)
    if 'UpdatedUTC' in frame:
        stamps = pd.to_datetime(frame.UpdatedUTC, errors='coerce', utc=True)
        keep &= stamps.between(utc(now)-pd.Timedelta(minutes=20), utc(now)+pd.Timedelta(minutes=5))
    if 'Quality' in frame:
        keep &= frame.Quality.eq('valid')
    return frame[keep].copy()


def record_collection(conn, park, status, message, count=0, now=None):
    stamp = utc(now)
    key = f'{park}|{stamp.floor("5min").isoformat()}|{status}'
    with conn:
        conn.execute('INSERT OR IGNORE INTO collection_runs VALUES (?,?,?,?,?,?)',
                     (key, stamp.isoformat(), park, status, str(message)[:1000], count))


def save_predictions(conn, frame, attraction, target_date=None, park=None, generated_at=None, features=None):
    if frame.empty:
        return 0
    generated = utc(generated_at)
    target_date = target_date or frame.attrs.get('target_date')
    if target_date is None:
        raise ValueError('A prediction must have an explicit target date')
    version = frame.attrs.get('model_version', MODEL_VERSION)
    park = park or frame.attrs.get('park')
    cutoff = iso(frame.attrs.get('feature_cutoff_utc', generated))
    if utc(cutoff) > generated:
        raise ValueError('Feature cutoff exceeds generation time')
    feature_json = json.dumps(features or frame.attrs.get('features', {}), ensure_ascii=False, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(feature_json.encode()).hexdigest()[:16]
    saved = 0
    with conn:
        for row in frame.to_dict('records'):
            value = finite(row.get('Predicted Wait'))
            if value is None:
                continue
            hour, minute = int(row['Hour']), int(row.get('Minute', 0))
            target = utc(pd.Timestamp(target_date).replace(hour=hour, minute=minute).tz_localize(JST))
            if target <= generated:
                continue  # retrospective fitted curves are not forecasts
            name = row.get('Attraction', attraction)
            key = f'{park}|{name}|{target.isoformat()}|{generated.floor("15min").isoformat()}|{version}|{fingerprint}'
            c = conn.execute('''INSERT OR IGNORE INTO predictions
                (created_at,target_hour,target_minute,target_time,predicted_wait,attraction,
                 generated_at_utc,target_at_utc,park,model,model_version,feature_cutoff_utc,
                 features_json,prediction_key,evaluation_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (generated.tz_convert(JST).strftime('%Y-%m-%d %H:%M:%S'),hour,minute,f'{hour:02d}:{minute:02d}',value,name,
                 generated.isoformat(),target.isoformat(),park,'weighted-history',version,cutoff,feature_json,key,'pending'))
            saved += c.rowcount
    return saved


def evaluate_predictions(conn, major_rides, now=None):
    now = utc(now)
    with conn:
        conn.execute("UPDATE predictions SET evaluation_status='insufficient_actual' WHERE evaluation_status='pending' AND target_at_utc < ?",((now-pd.Timedelta(days=7)).isoformat(),))
    pending = pd.read_sql_query("SELECT rowid,* FROM predictions WHERE evaluation_status='pending'",conn)
    if pending.empty:
        return 0
    hist = load_history(conn)
    from data_quality import classify
    hist=classify(hist,now)
    hist=hist[hist.eligible].copy()
    if hist.empty:
        return 0
    hist['stamp'] = pd.to_datetime(hist.observed_at_utc, utc=True, errors='coerce')
    done = 0
    with conn:
        for row in pending.to_dict('records'):
            target = utc(row['target_at_utc'])
            if target + pd.Timedelta(minutes=15) > now:
                continue
            rows = hist[hist.stamp.between(target, target+pd.Timedelta(minutes=15), inclusive='left')]
            if row['park']:
                rows = rows[rows.park.eq(row['park'])]
            if row['attraction'] in ('__ALL__','__MAJOR_5_AVERAGE__'):
                rows = rows[rows.attraction.isin(major_rides)]
                if rows.attraction.nunique() != len(major_rides):
                    continue
                actual = rows.groupby('attraction').wait_time.mean().mean()
            else:
                rows = rows[rows.attraction.eq(row['attraction'])]
                if rows.empty:
                    continue
                actual = rows.wait_time.mean()
            conn.execute("UPDATE predictions SET actual_wait=?,error=?,evaluation_status='evaluated',evaluated_at_utc=? WHERE rowid=? AND evaluation_status='pending'",
                         (float(actual),float(actual)-row['predicted_wait'],now.isoformat(),row['rowid']))
            done += 1
    return done


def save_daily(conn, target_date, value, features=None, kind='fixed', generated_at=None):
    value = finite(value)
    if conn is None or value is None:
        return False
    generated = utc(generated_at)
    target = str(pd.Timestamp(target_date).date())
    key = f'{MODEL_VERSION}|{target}|{kind}'
    if kind == '21:00':
        key += '|' + str(generated.tz_convert(JST).date())
    with conn:
        c = conn.execute('''INSERT OR IGNORE INTO daily_crowd_predictions
            (created_at,target_date,predicted_crowd_index,generated_at_utc,model_version,forecast_key,features_json,forecast_kind)
            VALUES (?,?,?,?,?,?,?,?)''', (generated.tz_convert(JST).strftime('%Y-%m-%d %H:%M:%S'),target,value,
            generated.isoformat(),MODEL_VERSION,key,json.dumps(features or {},ensure_ascii=False,default=str),kind))
    return c.rowcount == 1


def locked_daily(conn, target_date):
    if conn is None:
        return None
    row = conn.execute('''SELECT predicted_crowd_index FROM daily_crowd_predictions
        WHERE target_date=? AND model_version=? AND forecast_kind='fixed' ORDER BY generated_at_utc LIMIT 1''',
        (str(pd.Timestamp(target_date).date()),MODEL_VERSION)).fetchone()
    return row[0] if row else None


def diagnostics(conn):
    def scalar(sql):
        return conn.execute(sql).fetchone()[0]
    return {
        '保存データ数':scalar('SELECT count(*) FROM wait_times'),
        '保存日数':scalar('SELECT count(distinct substr(datetime,1,10)) FROM wait_times'),
        '対象施設数':scalar('SELECT count(distinct attraction) FROM wait_times'),
        '最終観測UTC':scalar('SELECT max(observed_at_utc) FROM wait_times'),
        '最終取得UTC':scalar('SELECT max(fetched_at_utc) FROM wait_times'),
        '取得失敗件数':scalar("SELECT count(*) FROM collection_runs WHERE status='failed'"),
        '対象日時が明確な予測':scalar('SELECT count(*) FROM predictions WHERE target_at_utc IS NOT NULL'),
        '評価済み予測数':scalar("SELECT count(*) FROM predictions WHERE evaluation_status='evaluated'"),
        '旧予測（評価除外）':scalar("SELECT count(*) FROM predictions WHERE evaluation_status='legacy_target_unknown'"),
        '予測MAE':scalar("SELECT avg(abs(error)) FROM predictions WHERE evaluation_status='evaluated'"),
    }
