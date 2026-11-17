from concurrent.futures import Future
from datetime import date
import sqlite3

import numpy as np
import pandas as pd
import pytest

import reliability
import runtime_data
import utils


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    monkeypatch.setenv("DISNEY_DISABLE_REMOTE_SYNC", "1")
    conn, _ = utils.connect_db("stage7.db")
    yield conn
    conn.close()


def live_frame(hour, waits, open_flags=None, timestamp=None):
    now = pd.Timestamp(f"2026-11-17 {hour:02d}:00", tz=reliability.JST) if timestamp is None else pd.Timestamp(timestamp)
    if now.tzinfo is None:
        now = now.tz_localize(reliability.JST)
    stamp = now.tz_convert("UTC").isoformat()
    open_flags = open_flags or [True] * len(waits)
    return pd.DataFrame([
        {"Attraction": f"Ride {i+1}", "Wait": wait, "Open": opened,
         "RideID": i+1, "ParkID": 274, "AreaID": 1, "Area": "Test",
         "UpdatedUTC": stamp, "FetchedUTC": stamp,
         "Quality": "valid" if wait is not None else "incomplete"}
        for i, (wait, opened) in enumerate(zip(waits, open_flags))
    ]), now


def test_live_index_tracks_same_day_waits_and_ignores_forecast_adjustments():
    major = [f"Ride {i}" for i in range(1, 6)]
    scenarios = [
        (9, [5, 10, 15, 20, 25]),
        (12, [30, 40, 50, 60, 70]),
        (17, [20, 30, 40, 50, 60]),
        (20, [5, 10, 15, 20, 25]),
    ]
    values = []
    for hour, waits in scenarios:
        frame, now = live_frame(hour, waits)
        index, debug = utils.get_live_crowd_index("Disneyland", frame, major, now=now, park_open=True)
        values.append(index)
        assert debug["source"] == "Queue-Times live waits"
    assert values[0] < values[1] > values[2] > values[3]


def test_closed_missing_stale_and_insufficient_waits_are_not_zero():
    frame, now = live_frame(12, [0, 0, 0, 0, 0], [True, True, True, False, False])
    index, debug = utils.get_live_crowd_index("Disneyland", frame, [f"Ride {i}" for i in range(1, 6)], now=now)
    assert index == 1.0
    assert debug["major_count"] == 3

    only_two, now = live_frame(12, [40, 50], [True, True])
    index, _ = utils.get_live_crowd_index("Disneyland", only_two, ["Ride 1", "Ride 2"], now=now)
    assert np.isnan(index)

    stale, now = live_frame(12, [30, 40, 50, 60, 70])
    old_stamp = pd.Timestamp("2026-11-17T11:00:00+09:00").tz_convert("UTC").isoformat()
    stale["UpdatedUTC"] = old_stamp
    stale["FetchedUTC"] = old_stamp
    index, _ = utils.get_live_crowd_index("Disneyland", stale, [f"Ride {i}" for i in range(1, 6)], now=now)
    assert np.isnan(index)

    closed, now = live_frame(22, [30, 40, 50, 60, 70])
    index, _ = utils.get_live_crowd_index("Disneyland", closed, [f"Ride {i}" for i in range(1, 6)], now=now, park_open=False)
    assert np.isnan(index)


def test_official_previous_evening_issue_wins_and_versions_remain(db):
    target = date(2026, 11, 18)
    assert reliability.save_daily(db, target, 5.2, {"edition": "early"}, kind="fixed", generated_at="2026-11-17T08:10:00+09:00")
    assert reliability.save_daily(db, target, 7.8, {"edition": "official"}, kind="21:00", generated_at="2026-11-17T21:07:00+09:00")
    assert not reliability.save_daily(db, target, 9.9, {"edition": "duplicate"}, kind="21:00", generated_at="2026-11-17T21:15:00+09:00")

    record = reliability.locked_daily_record(db, target)
    assert record["predicted_crowd_index"] == 7.8
    assert record["forecast_kind"] == "21:00"
    rows = db.execute("SELECT forecast_kind,predicted_crowd_index FROM daily_crowd_predictions WHERE target_date=? ORDER BY forecast_kind", (str(target),)).fetchall()
    assert rows == [("21:00", 7.8), ("fixed", 5.2)]


def test_out_of_window_evening_issue_cannot_replace_daily_fixed_value(db):
    target = date(2026, 11, 18)
    assert reliability.save_daily(db, target, 5.2, kind="fixed", generated_at="2026-11-17T08:10:00+09:00")
    assert reliability.save_daily(db, target, 9.3, kind="21:00", generated_at="2026-11-17T20:59:00+09:00")
    record = reliability.locked_daily_record(db, target)
    assert record["predicted_crowd_index"] == 5.2
    assert record["forecast_kind"] == "fixed"


def test_weekly_view_returns_only_saved_values_and_live_data_cannot_change_them(db):
    start = date(2026, 11, 17)
    assert reliability.save_daily(db, start, 6.4, kind="fixed", generated_at="2026-11-17T08:00:00+09:00")
    before = reliability.locked_week(db, start, 7)
    assert before[0]["predicted_crowd_index"] == 6.4
    assert all(row["predicted_crowd_index"] is None for row in before[1:])
    assert db.execute("SELECT count(*) FROM daily_crowd_predictions").fetchone()[0] == 1

    payload = {"rides": [{"id": 1, "name": "Ride 1", "is_open": True, "wait_time": 200,
                           "last_updated": "2026-11-17T00:00:00Z"}]}
    observation = reliability.parse_queue(payload, "Disneyland", "2026-11-17T00:01:00Z")
    reliability.save_observations(db, observation, 20, 0, "Disneyland")
    after = reliability.locked_week(db, start, 7)
    assert [r["predicted_crowd_index"] for r in after] == [r["predicted_crowd_index"] for r in before]
    assert db.execute("SELECT count(*) FROM daily_crowd_predictions").fetchone()[0] == 1


def test_jst_forecast_day_changes_at_local_midnight():
    assert reliability.jst_date("2026-11-17T14:59:00Z") == date(2026, 11, 17)
    assert reliability.jst_date("2026-11-17T15:00:00Z") == date(2026, 11, 18)


def test_locked_forecast_survives_database_backup_and_reopen(db, tmp_path):
    target = date(2026, 11, 18)
    reliability.save_daily(db, target, 7.1, kind="fixed", generated_at="2026-11-17T08:00:00+09:00")
    restored_path = tmp_path / "restored.db"
    with sqlite3.connect(restored_path) as restored:
        db.backup(restored)
    with sqlite3.connect(restored_path) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reliability.locked_daily(restored, target) == 7.1


def test_dashboard_can_render_without_waiting_for_background_sources():
    future = Future()
    assert runtime_data.immediate_result(future, "loading") == ("loading", False)
    future.set_result(("ready", 123.0))
    assert runtime_data.immediate_result(future, "loading") == ("ready", True)
