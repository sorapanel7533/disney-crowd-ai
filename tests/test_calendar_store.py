import csv
from datetime import date
import json
from pathlib import Path
import sqlite3

import pytest
import pandas as pd

from calendar_import import backup_databases
from calendar_store import (
    import_calendar_to_db,
    load_ticket_catalog,
    merge_ticket_prices,
    read_and_validate_calendar,
    show_status_label,
    ticket_source_date_label,
    validate_calendar_rows,
)


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_DATA = ROOT / "data" / "calendar-import"
TICKETS = CALENDAR_DATA / "ticket_prices_and_hours.csv"
SHOWS = CALENDAR_DATA / "official_show_schedule.csv"


def test_supplied_calendar_has_valid_keys_dates_times_and_explicit_unknowns():
    tickets, shows, quality, ticket_sha, show_sha = read_and_validate_calendar(TICKETS, SHOWS)

    assert len(tickets) == 274
    assert len(shows) == 836
    assert quality["ticket_dates"] == 137
    assert quality["show_dates"] == 44
    assert quality["ticket_status_counts"] == {"published": 250, "not_listed": 24}
    assert quality["show_status_counts"] == {
        "no_performance": 100,
        "reservation_required": 109,
        "scheduled": 599,
        "suspended": 28,
    }
    assert len(ticket_sha) == len(show_sha) == 64
    assert all(row["price_jpy"] == "" for row in tickets if row["price_status"] == "not_listed")


def test_import_is_repeatable_and_preserves_fixed_forecasts_and_unrelated_rows(tmp_path):
    db_path = tmp_path / "disneyland.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE daily_crowd_predictions (prediction_key TEXT PRIMARY KEY, value REAL)")
        conn.execute("INSERT INTO daily_crowd_predictions VALUES ('fixed-forecast-1', 6.2)")
        conn.execute("""CREATE TABLE park_hours (
            observed_at TEXT,target_date TEXT,park TEXT,open_hour REAL,close_hour REAL,
            ticket_price INTEGER,source TEXT,note TEXT)""")
        conn.execute("""CREATE TABLE show_schedules (
            observed_at TEXT,target_date TEXT,park TEXT,show_name TEXT,show_time TEXT,
            category TEXT,source TEXT,note TEXT,status TEXT)""")
        conn.execute("""CREATE TABLE ticket_price_snapshots (
            observed_at TEXT,target_date TEXT,park TEXT,price INTEGER,source TEXT)""")
        conn.execute(
            "INSERT INTO park_hours VALUES (?,?,?,?,?,?,?,?)",
            ("2026-11-01", "2030-01-01", "Disneyland", 9, 21, None, "rule_estimate", "keep"),
        )
        conn.execute(
            "INSERT INTO show_schedules VALUES (?,?,?,?,?,?,?,?,?)",
            ("2026-11-01", "2030-01-01", "Disneyland", "別の既存ショー", "10:00", "ショー", "manual", "keep", "scheduled"),
        )

    first = import_calendar_to_db(db_path, "Disneyland", TICKETS, SHOWS, imported_at_utc="2026-11-18T00:00:00+00:00")
    second = import_calendar_to_db(db_path, "Disneyland", TICKETS, SHOWS, imported_at_utc="2026-11-19T00:00:00+00:00")

    assert first["integrity"] == second["integrity"] == "ok"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM official_calendar_ticket_prices").fetchone()[0] == 137
        assert conn.execute("SELECT COUNT(*) FROM official_calendar_shows").fetchone()[0] == 440
        assert conn.execute("SELECT COUNT(*) FROM calendar_import_batches").fetchone()[0] == 1
        assert conn.execute("SELECT value FROM daily_crowd_predictions WHERE prediction_key='fixed-forecast-1'").fetchone()[0] == 6.2
        assert conn.execute("SELECT COUNT(*) FROM park_hours WHERE target_date='2030-01-01' AND source='rule_estimate'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM show_schedules WHERE show_name='別の既存ショー'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM park_hours WHERE source LIKE 'official_calendar_archive_%'").fetchone()[0] == 137
        assert conn.execute("SELECT COUNT(*) FROM ticket_price_snapshots WHERE source LIKE 'official_calendar_archive_%'").fetchone()[0] == 125
        duplicate_catalog = conn.execute(
            """SELECT 1 FROM official_calendar_ticket_prices GROUP BY target_date,park,ticket_type
               HAVING COUNT(*) > 1 LIMIT 1"""
        ).fetchone()
        duplicate_shows = conn.execute(
            """SELECT 1 FROM official_calendar_shows GROUP BY target_date,park,show_name
               HAVING COUNT(*) > 1 LIMIT 1"""
        ).fetchone()
        duplicate_legacy = conn.execute(
            """SELECT 1 FROM show_schedules WHERE note LIKE 'Imported schedule snapshot;%'
               GROUP BY target_date,park,show_name,show_time,source HAVING COUNT(*) > 1 LIMIT 1"""
        ).fetchone()
        assert duplicate_catalog is duplicate_shows is duplicate_legacy is None

        saved = load_ticket_catalog(conn, "Disneyland")
        assert len(saved) == 137
        prices, statuses, sources = merge_ticket_prices(
            {date(2026, 11, 18): 9999}, "Castel fallback", saved,
        )
        assert prices[date(2026, 11, 18)] == 10900
        assert ticket_source_date_label(date(2026, 11, 18), sources) == "（11/18確認）"
        assert statuses[date(2027, 3, 23)] == "not_listed"
        assert date(2027, 3, 23) not in prices

    third_party, _, _ = merge_ticket_prices(
        {date(2027, 3, 23): 9999}, "Castel fallback", saved,
    )
    assert date(2027, 3, 23) not in third_party

    official, _, _ = merge_ticket_prices(
        {date(2026, 11, 18): 11100}, "東京ディズニーリゾート公式ページ", saved,
    )
    assert official[date(2026, 11, 18)] == 11100


def test_invalid_price_or_show_time_is_rejected_before_import():
    tickets, shows, *_ = read_and_validate_calendar(TICKETS, SHOWS)
    tickets[0] = dict(tickets[0], price_jpy="")
    with pytest.raises(ValueError, match="掲載価格"):
        validate_calendar_rows(tickets, shows)

    tickets, shows, *_ = read_and_validate_calendar(TICKETS, SHOWS)
    scheduled = next(index for index, row in enumerate(shows) if row["show_status"] == "scheduled")
    shows[scheduled] = dict(shows[scheduled], start_times_jst="25:99")
    with pytest.raises(ValueError, match="時刻が不正"):
        validate_calendar_rows(tickets, shows)


def test_show_statuses_are_displayed_without_collapsing_no_performance_into_suspension():
    assert show_status_label("suspended") == "休止"
    assert show_status_label("no_performance") == "公演予定なし"
    assert show_status_label("reservation_required") == "事前予約制（時刻は予約時確認）"
    assert show_status_label("scheduled", "12:10") == "12:10"


def test_official_saved_hours_outweigh_newer_estimates_and_show_the_check_date():
    import utils

    rows = pd.DataFrame([
        {"observed_at": "2026-11-18", "target_date": date(2026, 11, 18), "park": "Disneyland",
         "open_hour": 9.0, "close_hour": 21.0, "source": "official_calendar_archive_2026-11-18"},
        {"observed_at": "2026-11-19", "target_date": date(2026, 11, 18), "park": "Disneyland",
         "open_hour": 10.0, "close_hour": 20.0, "source": "rule_estimate"},
    ])

    opened, closed, source = utils.get_today_park_hours(rows, "Disneyland", date(2026, 11, 18))

    assert (opened, closed) == (9.0, 21.0)
    assert "2026-11-18" in source


def test_new_calendar_catalog_tables_are_created_during_database_startup(tmp_path, monkeypatch):
    import utils

    base = tmp_path / "disneyland.db"
    with sqlite3.connect(base) as conn:
        conn.execute("CREATE TABLE wait_times (datetime TEXT, attraction TEXT, wait_time INTEGER)")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path / ".runtime"))
    monkeypatch.setenv("DISNEY_DISABLE_REMOTE_SYNC", "1")

    conn, _ = utils.connect_db("disneyland.db")
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"official_calendar_ticket_prices", "official_calendar_shows", "calendar_import_batches"}.issubset(tables)
    finally:
        conn.close()


def test_preimport_sqlite_backups_are_consistent_and_manifest_names_source_commit(tmp_path):
    db_dir, backup_dir = tmp_path / "data", tmp_path / "backup"
    db_dir.mkdir()
    for name in ("disneyland.db", "disneysea.db"):
        with sqlite3.connect(db_dir / name) as conn:
            conn.execute("CREATE TABLE retained (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO retained VALUES (1, 'before import')")
    manifest_path = tmp_path / "checkpoint.json"

    manifest = backup_databases(
        db_dir, backup_dir, manifest_path, source_commit="a" * 40,
    )

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["source_crowd_data_commit"] == "a" * 40
    assert set(saved["databases"]) == {"disneyland.db", "disneysea.db"}
    for row in saved["databases"].values():
        backup_path = backup_dir / row["backup_file"]
        assert backup_path.is_file()
        assert row["integrity"] == "ok"
        with sqlite3.connect(backup_path) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT value FROM retained WHERE id=1").fetchone()[0] == "before import"
