"""Validated, versioned storage for the supplied official calendar snapshot.

The normalized tables retain the source fields and explicit "not listed" states.
The legacy tables are populated as a read-through fallback for existing screens.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse


PARKS = {"TDL": "Disneyland", "TDS": "DisneySea"}
TICKET_SOURCE = "official_calendar_archive"
TICKET_TYPES = {"1_day_passport_adult"}
SHOW_STATUSES = {"scheduled", "suspended", "no_performance", "reservation_required"}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def ensure_calendar_tables(conn: sqlite3.Connection) -> None:
    """Create the import catalog tables; safe to call on every app startup."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_calendar_ticket_prices (
            target_date TEXT NOT NULL,
            park TEXT NOT NULL,
            ticket_type TEXT NOT NULL,
            price INTEGER,
            price_status TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            hours_status TEXT NOT NULL,
            source_image TEXT NOT NULL,
            source_captured_jst TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (target_date, park, ticket_type)
        );
        CREATE TABLE IF NOT EXISTS official_calendar_shows (
            target_date TEXT NOT NULL,
            park TEXT NOT NULL,
            show_name TEXT NOT NULL,
            start_times_jst TEXT NOT NULL,
            show_status TEXT NOT NULL,
            raw_schedule_text TEXT NOT NULL,
            official_url TEXT NOT NULL,
            retrieved_jst TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (target_date, park, show_name)
        );
        CREATE TABLE IF NOT EXISTS calendar_import_batches (
            batch_id TEXT PRIMARY KEY,
            source_archive TEXT NOT NULL,
            source_captured_jst TEXT NOT NULL,
            imported_at_utc TEXT NOT NULL,
            ticket_sha256 TEXT NOT NULL,
            shows_sha256 TEXT NOT NULL,
            ticket_rows INTEGER NOT NULL,
            show_rows INTEGER NOT NULL,
            data_quality_json TEXT NOT NULL
        );
        """
    )


def _read_csv(path: str | Path) -> tuple[list[dict[str, str]], bytes]:
    raw = Path(path).read_bytes()
    try:
        rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"CSVをUTF-8として読めません: {Path(path).name}") from exc
    if not rows or not rows[0]:
        raise ValueError(f"CSVにデータ行がありません: {Path(path).name}")
    return rows, raw


def _valid_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}の日付が不正です: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label}の日付形式が不正です: {value!r}")
    return parsed


def _valid_time(value: str, label: str) -> None:
    if not TIME_RE.fullmatch(value or ""):
        raise ValueError(f"{label}の時刻が不正です: {value!r}")


def validate_calendar_rows(
    ticket_rows: list[dict[str, str]], show_rows: list[dict[str, str]],
) -> dict[str, object]:
    """Reject inconsistent or ambiguous rows before opening a write transaction."""
    if not ticket_rows or not show_rows:
        raise ValueError("チケット/営業時間とショーの両方にデータが必要です")

    ticket_keys: set[tuple[str, str, str]] = set()
    ticket_days: dict[str, set[str]] = {park: set() for park in PARKS}
    captures: set[str] = set()
    for index, row in enumerate(ticket_rows, 2):
        label = f"ticket row {index}"
        target = row.get("date", "")
        _valid_date(target, label)
        code = row.get("park", "")
        if code not in PARKS:
            raise ValueError(f"{label}のパークが不正です: {code!r}")
        ticket_type = row.get("ticket_type", "")
        if ticket_type not in TICKET_TYPES:
            raise ValueError(f"{label}のチケット種別が未対応です: {ticket_type!r}")
        key = (target, code, ticket_type)
        if key in ticket_keys:
            raise ValueError(f"チケット重複キー: {key}")
        ticket_keys.add(key)
        ticket_days[code].add(target)

        status = row.get("price_status", "")
        price_text = row.get("price_jpy", "").strip()
        if status == "published":
            if not price_text.isdigit() or not 5_000 <= int(price_text) <= 50_000:
                raise ValueError(f"{label}の掲載価格が不正です")
        elif status == "not_listed":
            if price_text:
                raise ValueError(f"{label}: not_listedに価格が入っています")
        else:
            raise ValueError(f"{label}の価格状態が未対応です: {status!r}")

        if row.get("hours_status") != "listed":
            raise ValueError(f"{label}の営業時間状態が不正です")
        opened, closed = row.get("open_time", ""), row.get("close_time", "")
        _valid_time(opened, f"{label} open_time")
        _valid_time(closed, f"{label} close_time")
        if opened >= closed:
            raise ValueError(f"{label}の閉園時刻が開園時刻以前です")
        capture = row.get("source_captured_jst", "")
        _valid_date(capture, f"{label} source_captured_jst")
        if not row.get("source_image", "").strip():
            raise ValueError(f"{label}の出典画像がありません")
        captures.add(capture)

    if any(not ticket_days[park] for park in PARKS):
        raise ValueError("チケット/営業時間の対象パークが欠けています")
    if ticket_days["TDL"] != ticket_days["TDS"]:
        raise ValueError("チケット/営業時間の日付範囲が2パークで一致しません")
    if len(captures) != 1:
        raise ValueError("チケット価格・営業時間の取得基準日が統一されていません")

    show_keys: set[tuple[str, str, str]] = set()
    show_days: dict[str, set[str]] = {park: set() for park in PARKS}
    for index, row in enumerate(show_rows, 2):
        label = f"show row {index}"
        target = row.get("date", "")
        _valid_date(target, label)
        code = row.get("park", "")
        if code not in PARKS:
            raise ValueError(f"{label}のパークが不正です: {code!r}")
        name = row.get("show_name", "").strip()
        if not name:
            raise ValueError(f"{label}のショー名が空です")
        key = (target, code, name)
        if key in show_keys:
            raise ValueError(f"ショー重複キー: {key}")
        show_keys.add(key)
        show_days[code].add(target)

        status = row.get("show_status", "")
        if status not in SHOW_STATUSES:
            raise ValueError(f"{label}の公演状態が未対応です: {status!r}")
        times = row.get("start_times_jst", "").strip()
        if status == "scheduled":
            if not times:
                raise ValueError(f"{label}: scheduledに公演時刻がありません")
            for value in times.split("|"):
                _valid_time(value, f"{label} start_times_jst")
        elif times:
            raise ValueError(f"{label}: {status}なのに公演時刻があります")

        parsed_url = urlparse(row.get("official_url", ""))
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.tokyodisneyresort.jp":
            raise ValueError(f"{label}の出典URLが公式ドメインではありません")
        _valid_date(row.get("retrieved_jst", ""), f"{label} retrieved_jst")
        if not row.get("raw_schedule_text", "").strip():
            raise ValueError(f"{label}の原文記録がありません")

    if any(not show_days[park] for park in PARKS):
        raise ValueError("ショースケジュールの対象パークが欠けています")
    if show_days["TDL"] != show_days["TDS"]:
        raise ValueError("ショースケジュールの日付範囲が2パークで一致しません")

    return {
        "ticket_rows": len(ticket_rows),
        "show_rows": len(show_rows),
        "ticket_dates": len(ticket_days["TDL"]),
        "show_dates": len(show_days["TDL"]),
        "ticket_capture_jst": next(iter(captures)),
        "ticket_status_counts": {
            status: sum(row["price_status"] == status for row in ticket_rows)
            for status in ("published", "not_listed")
        },
        "show_status_counts": {
            status: sum(row["show_status"] == status for row in show_rows)
            for status in sorted(SHOW_STATUSES)
        },
    }


def read_and_validate_calendar(
    ticket_csv: str | Path, show_csv: str | Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object], str, str]:
    tickets, ticket_bytes = _read_csv(ticket_csv)
    shows, show_bytes = _read_csv(show_csv)
    quality = validate_calendar_rows(tickets, shows)
    return (
        tickets,
        shows,
        quality,
        hashlib.sha256(ticket_bytes).hexdigest(),
        hashlib.sha256(show_bytes).hexdigest(),
    )


def load_ticket_catalog(conn: sqlite3.Connection, park: str) -> list[dict[str, object]]:
    """Return saved ticket price/status rows for one park, newest batch keys first."""
    try:
        rows = conn.execute(
            """SELECT target_date, price, price_status, source_captured_jst, source_file
               FROM official_calendar_ticket_prices WHERE park=? ORDER BY target_date""",
            (park,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(zip(("target_date", "price", "price_status", "source_captured_jst", "source_file"), row)) for row in rows]


def merge_ticket_prices(
    live_prices: dict[object, object] | None,
    live_source: str,
    saved_rows: list[dict[str, object]],
) -> tuple[dict[object, object], dict[object, str], dict[object, str]]:
    """Prefer an official live value; retain curated official values over third-party fallback."""
    prices: dict[object, object] = {}
    statuses: dict[object, str] = {}
    sources: dict[object, str] = {}
    by_iso: dict[str, object] = {}
    for row in saved_rows:
        day = str(row["target_date"])
        value = row.get("price")
        status = str(row.get("price_status", ""))
        by_iso[day] = date.fromisoformat(day)
        statuses[by_iso[day]] = status
        sources[by_iso[day]] = f"official_calendar_archive_{row.get('source_captured_jst', '')}"
        if status == "published" and value is not None:
            prices[by_iso[day]] = int(value)

    live_is_official = "公式" in str(live_source) or "official" in str(live_source).casefold()
    for raw_day, raw_price in (live_prices or {}).items():
        if raw_price is None:
            continue
        day = raw_day.date() if isinstance(raw_day, datetime) else raw_day
        try:
            iso = day.isoformat()
        except AttributeError:
            iso = str(day)
            day = date.fromisoformat(iso)
        if statuses.get(day) == "not_listed" and not live_is_official:
            continue
        if day not in prices or live_is_official:
            prices[day] = raw_price
            statuses[day] = "published"
            sources[day] = str(live_source or "live_ticket_source")
    return prices, statuses, sources


def ticket_source_date_label(day: object, sources: dict[object, str]) -> str:
    source = sources.get(day, "")
    prefix = "official_calendar_archive_"
    if source.startswith(prefix):
        captured = source.removeprefix(prefix)
        try:
            parsed = date.fromisoformat(captured)
            return f"（{parsed.month}/{parsed.day}確認）"
        except ValueError:
            return "（保存データ）"
    return ""


def show_status_label(status: object, show_time: object = "") -> str:
    normalized = str(status or "").strip()
    labels = {
        "suspended": "休止",
        "no_performance": "公演予定なし",
        "reservation_required": "事前予約制（時刻は予約時確認）",
        "unannounced": "未発表",
    }
    if normalized in labels:
        return labels[normalized]
    value = str(show_time or "").strip()
    return value if value else "未発表"


def _ensure_legacy_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ticket_price_snapshots (
            observed_at TEXT, target_date TEXT, park TEXT, price INTEGER, source TEXT
        );
        CREATE TABLE IF NOT EXISTS park_hours (
            observed_at TEXT, target_date TEXT, park TEXT, open_hour REAL,
            close_hour REAL, ticket_price INTEGER, source TEXT, note TEXT
        );
        CREATE TABLE IF NOT EXISTS show_schedules (
            observed_at TEXT, target_date TEXT, park TEXT, show_name TEXT,
            show_time TEXT, category TEXT, source TEXT, note TEXT, status TEXT
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(show_schedules)")}
    if "status" not in columns:
        conn.execute("ALTER TABLE show_schedules ADD COLUMN status TEXT")


def _replace_legacy_row(conn, table: str, where: str, where_values: tuple, columns: tuple[str, ...], values: tuple) -> None:
    """Replace only a changed imported row, keeping identical repeated imports write-free."""
    current = conn.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE {where}", where_values,
    ).fetchall()
    if len(current) == 1 and tuple(current[0]) == values:
        return
    conn.execute(f"DELETE FROM {table} WHERE {where}", where_values)
    marks = ",".join("?" for _ in columns)
    names = ",".join(columns)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", values)


def import_calendar_to_db(
    db_path: str | Path,
    park: str,
    ticket_csv: str | Path,
    show_csv: str | Path,
    *,
    source_archive: str = "disney_stage8_codex_import.zip",
    imported_at_utc: str | None = None,
) -> dict[str, object]:
    """Import validated rows into one park database without touching forecast history."""
    if park not in ("Disneyland", "DisneySea"):
        raise ValueError(f"Unknown park database: {park}")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"既存DBがありません。新規作成はしません: {path}")

    tickets, shows, quality, ticket_sha, shows_sha = read_and_validate_calendar(ticket_csv, show_csv)
    code = "TDL" if park == "Disneyland" else "TDS"
    park_tickets = [row for row in tickets if row["park"] == code]
    park_shows = [row for row in shows if row["park"] == code]
    if not park_tickets or not park_shows:
        raise ValueError(f"{park}の取込行がありません")
    captured = str(quality["ticket_capture_jst"])
    archive_source = f"official_calendar_archive_{captured}"
    now = imported_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds")
    batch_id = hashlib.sha256(f"{ticket_sha}:{shows_sha}:{park}".encode("ascii")).hexdigest()

    with sqlite3.connect(path, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError(f"DB integrity check failed before import: {path.name}")
        ensure_calendar_tables(conn)
        _ensure_legacy_tables(conn)
        fixed_table_count = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_crowd_predictions'").fetchone():
            fixed_table_count = conn.execute("SELECT COUNT(*) FROM daily_crowd_predictions").fetchone()[0]

        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in park_tickets:
                price = int(row["price_jpy"]) if row["price_jpy"].strip() else None
                conn.execute(
                    """INSERT INTO official_calendar_ticket_prices
                       (target_date,park,ticket_type,price,price_status,open_time,close_time,
                        hours_status,source_image,source_captured_jst,source_file,source_sha256)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(target_date,park,ticket_type) DO UPDATE SET
                        price=excluded.price,price_status=excluded.price_status,
                        open_time=excluded.open_time,close_time=excluded.close_time,
                        hours_status=excluded.hours_status,source_image=excluded.source_image,
                        source_captured_jst=excluded.source_captured_jst,
                        source_file=excluded.source_file,source_sha256=excluded.source_sha256
                       WHERE official_calendar_ticket_prices.price IS NOT excluded.price
                          OR official_calendar_ticket_prices.price_status IS NOT excluded.price_status
                          OR official_calendar_ticket_prices.open_time IS NOT excluded.open_time
                          OR official_calendar_ticket_prices.close_time IS NOT excluded.close_time
                          OR official_calendar_ticket_prices.hours_status IS NOT excluded.hours_status
                          OR official_calendar_ticket_prices.source_image IS NOT excluded.source_image
                          OR official_calendar_ticket_prices.source_captured_jst IS NOT excluded.source_captured_jst
                          OR official_calendar_ticket_prices.source_file IS NOT excluded.source_file
                          OR official_calendar_ticket_prices.source_sha256 IS NOT excluded.source_sha256""",
                    (row["date"], park, row["ticket_type"], price, row["price_status"],
                     row["open_time"], row["close_time"], row["hours_status"],
                     row["source_image"], row["source_captured_jst"],
                     "ticket_prices_and_hours.csv", ticket_sha),
                )
                open_hour = int(row["open_time"].split(":")[0]) + int(row["open_time"].split(":")[1]) / 60
                close_hour = int(row["close_time"].split(":")[0]) + int(row["close_time"].split(":")[1]) / 60
                _replace_legacy_row(
                    conn, "park_hours", "target_date=? AND park=? AND source=?",
                    (row["date"], park, archive_source),
                    ("observed_at", "target_date", "park", "open_hour", "close_hour", "ticket_price", "source", "note"),
                    (row["source_captured_jst"], row["date"], park, open_hour, close_hour,
                     price, archive_source, f"Official calendar screenshot snapshot; source file: {row['source_image']}"),
                )
                if price is not None:
                    _replace_legacy_row(
                        conn, "ticket_price_snapshots", "target_date=? AND park=? AND source=?",
                        (row["date"], park, archive_source),
                        ("observed_at", "target_date", "park", "price", "source"),
                        (row["source_captured_jst"], row["date"], park, price, archive_source),
                    )
                else:
                    conn.execute(
                        "DELETE FROM ticket_price_snapshots WHERE target_date=? AND park=? AND source=?",
                        (row["date"], park, archive_source),
                    )

            for row in park_shows:
                times = row["start_times_jst"].split("|") if row["start_times_jst"].strip() else [""]
                conn.execute(
                    """INSERT INTO official_calendar_shows
                       (target_date,park,show_name,start_times_jst,show_status,raw_schedule_text,
                        official_url,retrieved_jst,source_file,source_sha256)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(target_date,park,show_name) DO UPDATE SET
                        start_times_jst=excluded.start_times_jst,show_status=excluded.show_status,
                        raw_schedule_text=excluded.raw_schedule_text,official_url=excluded.official_url,
                        retrieved_jst=excluded.retrieved_jst,source_file=excluded.source_file,
                        source_sha256=excluded.source_sha256
                       WHERE official_calendar_shows.start_times_jst IS NOT excluded.start_times_jst
                          OR official_calendar_shows.show_status IS NOT excluded.show_status
                          OR official_calendar_shows.raw_schedule_text IS NOT excluded.raw_schedule_text
                          OR official_calendar_shows.official_url IS NOT excluded.official_url
                          OR official_calendar_shows.retrieved_jst IS NOT excluded.retrieved_jst
                          OR official_calendar_shows.source_file IS NOT excluded.source_file
                          OR official_calendar_shows.source_sha256 IS NOT excluded.source_sha256""",
                    (row["date"], park, row["show_name"], row["start_times_jst"], row["show_status"],
                     row["raw_schedule_text"], row["official_url"], row["retrieved_jst"],
                     "official_show_schedule.csv", shows_sha),
                )
                note = f"Imported schedule snapshot; checked {row['retrieved_jst']}"
                desired = [
                    (row["retrieved_jst"], row["date"], park, row["show_name"], show_time,
                     "ショー/パレード", row["official_url"], note, row["show_status"])
                    for show_time in times
                ]
                existing = conn.execute(
                    """SELECT observed_at,target_date,park,show_name,show_time,category,source,note,status
                       FROM show_schedules WHERE target_date=? AND park=? AND show_name=? AND source=?
                       ORDER BY show_time""",
                    (row["date"], park, row["show_name"], row["official_url"]),
                ).fetchall()
                if [tuple(value) for value in existing] == sorted(desired, key=lambda value: value[4]):
                    continue
                conn.execute(
                    "DELETE FROM show_schedules WHERE target_date=? AND park=? AND show_name=? AND source=?",
                    (row["date"], park, row["show_name"], row["official_url"]),
                )
                for show_time in times:
                    conn.execute(
                        """INSERT INTO show_schedules
                           (observed_at,target_date,park,show_name,show_time,category,source,note,status)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (row["retrieved_jst"], row["date"], park, row["show_name"], show_time,
                         "ショー/パレード", row["official_url"], note, row["show_status"]),
                    )

            conn.execute(
                """INSERT OR IGNORE INTO calendar_import_batches
                   (batch_id,source_archive,source_captured_jst,imported_at_utc,ticket_sha256,
                    shows_sha256,ticket_rows,show_rows,data_quality_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (batch_id, source_archive, captured, now, ticket_sha, shows_sha,
                 len(park_tickets), len(park_shows), json.dumps(quality, ensure_ascii=False, sort_keys=True)),
            )

        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError(f"DB integrity check failed after import: {path.name}")
        imported_ticket_count = conn.execute(
            "SELECT COUNT(*) FROM official_calendar_ticket_prices WHERE park=?", (park,)
        ).fetchone()[0]
        imported_show_count = conn.execute(
            "SELECT COUNT(*) FROM official_calendar_shows WHERE park=?", (park,)
        ).fetchone()[0]
        if imported_ticket_count != len(park_tickets) or imported_show_count != len(park_shows):
            raise sqlite3.DatabaseError(f"Calendar catalog count mismatch after import: {path.name}")
        if fixed_table_count is not None:
            after_count = conn.execute("SELECT COUNT(*) FROM daily_crowd_predictions").fetchone()[0]
            if after_count != fixed_table_count:
                raise sqlite3.DatabaseError("固定予測件数が変化しました。トランザクションを確認してください")

    return {
        "park": park,
        "ticket_rows": len(park_tickets),
        "show_rows": len(park_shows),
        "integrity": "ok",
        "batch_id": batch_id,
        "source_captured_jst": captured,
        "ticket_sha256": ticket_sha,
        "shows_sha256": shows_sha,
        "quality": quality,
    }
