"""SQLite reads and background refresh for the Streamlit process."""
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from threading import Lock
import logging
import os
import sqlite3
import time

import pandas as pd
import streamlit as st

import reliability
import utils
from persistence import snapshot_status, sync_public_snapshot


_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="park-snapshot")
_LOCK = Lock()
_SOURCE_POOL = ThreadPoolExecutor(max_workers=5, thread_name_prefix="park-source")
_SOURCE_LOCK = Lock()
_SOURCES = OrderedDict()


def source_result(name, reader, ttl, *args, on_error=None):
    # Start independent loads together and preserve each source TTL.
    key = (name, reader)

    def call():
        try:
            result = reader(*args)
        except Exception as exc:
            if on_error is None:
                raise
            result = on_error(exc)
        return result, time.monotonic()

    with _SOURCE_LOCK:
        future = _SOURCES.get(key)
        expired = future and future.done() and (
            future.exception() is not None or time.monotonic() - future.result()[1] >= ttl
        )
        if future is None or expired:
            future = _SOURCE_POOL.submit(call)
            _SOURCES[key] = future
        _SOURCES.move_to_end(key)
        while len(_SOURCES) > 16:
            _SOURCES.popitem(last=False)
    return future


def live_waits(park, reader):
    base = utils.PARK_SETTINGS[park]

    def unavailable(exc):
        empty = pd.DataFrame(columns=reliability.WAIT_COLUMNS)
        return empty, empty.copy(), str(exc)

    return source_result(park, reader, 300, base, on_error=unavailable)

_INITIALIZED = {}
_SYNCS = {}
_COLLECTED = {}


def path_for(name):
    return (Path(os.environ.get("DISNEY_DB_DIR", ".runtime")) / Path(name).name).resolve()


def file_identity(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino)


def open_database(name, path=None):
    """Return a new connection to every caller, including the refresh worker."""
    path = Path(path) if path is not None else path_for(name)
    with _LOCK:
        identity = file_identity(path)
        if identity is None or _INITIALIZED.get(path) != identity:
            if path != path_for(name):
                raise OSError("Park database location changed before initialization")
            db, _ = utils.connect_db(name)
            with closing(db):
                db.execute("CREATE INDEX IF NOT EXISTS history_attraction_time ON wait_times(attraction,datetime)")
                db.commit()
            _INITIALIZED[path] = file_identity(path)
    db = sqlite3.connect(path, timeout=10)
    return db, db.cursor()


def _refresh(name, path):
    db, _ = open_database(name, path)
    with closing(db):
        return sync_public_snapshot(db, name, path.parent)


def refresh_status(name):
    if os.environ.get("DISNEY_DISABLE_REMOTE_SYNC") == "1":
        return "同期無効（テスト/収集環境）", False, False

    path = path_for(name)
    with _LOCK:
        state = snapshot_status(name, path.parent)
        if time.time() < state.get("retry_at", 0):
            return state["message"], False, not bool(state.get("last_success"))
        job = _SYNCS.get(path)
        if job and job.done():
            try:
                job.result()
            except Exception:
                logging.getLogger(__name__).exception("Park snapshot refresh failed for %s", name)
                # Avoid repeated retries if opening the database itself failed.
                if time.monotonic() - job.submitted_at < 60:
                    return "同期に失敗しました。手元の履歴を表示します。", False, not bool(state.get("last_success"))
            job = None
        if job is None:
            job = _POOL.submit(_refresh, name, path)
            job.submitted_at = time.monotonic()
            _SYNCS[path] = job
        initial = not state.get("last_success")
        return ("過去データを同期中です。" if initial else state["message"]), True, initial


def db_revision(path):
    """Include both the SQLite database and any uncheckpointed WAL changes."""
    path = Path(path)
    revisions = []
    for part in (path, path.with_name(path.name + "-wal")):
        try:
            stat = part.stat()
            revisions.append((stat.st_ino, stat.st_size, stat.st_mtime_ns) if stat.st_size else None)
        except FileNotFoundError:
            revisions.append(None)
    return tuple(revisions)


@st.cache_data(max_entries=16, show_spinner=False)
def model_history(path, revision):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        return utils.load_history(db)


@st.cache_data(max_entries=20, show_spinner=False)
def model_errors(path, revision):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        return utils.load_prediction_history(db, evaluated_only=True)


@st.cache_data(max_entries=16, show_spinner=False)
def daily_history(path, revision):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        return utils.load_daily_crowd_predictions(db)


@st.cache_data(max_entries=12, show_spinner=False)
def history_names(path, revision, park):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        return tuple(row[0] for row in db.execute(
            "SELECT DISTINCT attraction FROM wait_times WHERE (park=? OR park IS NULL) AND attraction IS NOT NULL",
            (park,),
        ))


@st.cache_data(max_entries=40, show_spinner=False)
def ride_history(path, revision, park, ride):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        frame = pd.read_sql_query(
            "SELECT datetime,wait_time FROM wait_times "
            "WHERE attraction=? AND (park=? OR park IS NULL) AND wait_time>0 ORDER BY datetime",
            db, params=(ride, park),
        )
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce", format="mixed")
    frame["hour"] = frame["datetime"].dt.hour
    return frame.dropna(subset=["datetime"])


def record_wait_batch(db, park, frame, temperature, rain, error):
    """Save one real API response once per process; SQLite keys still deduplicate on disk."""
    path = path_for(utils.PARK_SETTINGS[park]["db"])
    stamp = frame["FetchedUTC"].max() if not frame.empty else reliability.utc().floor("5min").isoformat()
    token = (file_identity(path), str(stamp), error)
    with _LOCK:
        if _COLLECTED.get(path) == token:
            return False
        if error:
            reliability.record_collection(db, park, "failed", error)
        else:
            count = utils.save_wait_times(db.cursor(), db, frame, temperature, rain, park)
            reliability.record_collection(db, park, "success", f"{len(frame)}施設", count)
        _COLLECTED[path] = token
    return not error
