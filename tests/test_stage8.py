from datetime import date, datetime, timedelta

import pandas as pd
import requests
import utils


def test_official_ticket_calendar_keeps_cross_month_dates_on_the_right_day():
    html = """
    <h2>2026年11月</h2>
    <table>
      <tr><th>日</th><th>月</th><th>火</th><th>水</th><th>木</th><th>金</th><th>土</th></tr>
      <tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td></tr>
      <tr><td>8</td><td>9</td><td>10</td><td>11</td><td>12</td><td>13</td><td>14</td></tr>
      <tr><td>15</td><td>16</td><td>17</td><td>18 10900</td><td>19 10900</td><td>20 10900</td><td>21 12400</td></tr>
      <tr><td>22 12400</td><td>23 10900</td><td>24 10900</td><td>25 10900</td><td>26 10900</td><td>27 10900</td><td>28 12400</td></tr>
      <tr><td>29 11900</td><td>30 10900</td><td>1 10900</td><td>2 10900</td><td>3 10900</td><td>4 10900</td><td>5 12400</td></tr>
    </table>
    """
    prices = utils._parse_official_ticket_calendar(html)

    assert prices[date(2026, 11, 18)] == 10900
    assert prices[date(2026, 11, 29)] == 11900
    assert prices[date(2026, 12, 1)] == 10900
    assert date(2026, 11, 1) not in prices


def test_monthly_show_parser_keeps_multiple_times_and_statuses():
    html = """
    <h2>2026年11月</h2>
    <table>
      <tr><td>18(水)</td><td>10:50 / 12:15 / 13:40 / 15:45 / 17:10</td></tr>
      <tr><td>27(金)</td><td>休止</td></tr>
      <tr><td>28(土)</td><td>未発表</td></tr>
    </table>
    <h2>2026年12月</h2>
    <table><tr><td>1(火)</td><td>10:50 / 12:15</td></tr></table>
    """
    rows = utils._parse_monthly_show_page(
        html, "https://www.tokyodisneyresort.jp/tdl/show/schedule/895/", date(2026, 11, 18)
    )
    frame = pd.DataFrame(rows)

    assert frame.loc[frame.target_date.eq(date(2026, 11, 18)), "show_time"].tolist() == [
        "10:50", "12:15", "13:40", "15:45", "17:10"
    ]
    assert frame.loc[frame.target_date.eq(date(2026, 11, 27)), "status"].tolist() == ["suspended"]
    assert frame.loc[frame.target_date.eq(date(2026, 11, 28)), "status"].tolist() == ["unannounced"]
    assert frame.loc[frame.target_date.eq(date(2026, 12, 1)), "show_time"].tolist() == ["10:50", "12:15"]


def test_official_ticket_fetch_uses_official_japanese_page_and_reports_dated_prices(monkeypatch):
    body = """
    <h2>2026年11月</h2><table><tr><td>18 10900</td><td>19 10900</td></tr></table>
    <a href="/ticket/index/202612/">12月</a>
    """
    next_month = "<h2>2026年12月</h2><table><tr><td>1 10900</td><td>2 12400</td></tr></table>"
    calls = []
    sleeps = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = (body if len(calls) == 1 else next_month).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(utils.requests, "get", get)
    monkeypatch.setattr(utils.time, "sleep", sleeps.append)
    prices, source = utils.fetch_official_ticket_prices(date(2026, 11, 18))

    assert prices[date(2026, 11, 18)] == 10900
    assert prices[date(2026, 12, 1)] == 10900
    assert "公式チケット" in source
    assert calls[0][0] == utils.OFFICIAL_TICKET_URL
    assert calls[0][1]["timeout"] == (4, 12)
    assert calls[1][0].endswith("/ticket/index/202612/")
    assert sleeps == [2]


def test_official_ticket_partial_fetch_keeps_verified_month_and_stops_on_rate_limit(monkeypatch):
    body = "<h2>2026年11月</h2><table><tr><td>18 10900</td></tr></table><a href='/ticket/index/202612/'>12月</a>"
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200 if len(calls) == 1 else 429
        response._content = body.encode() if len(calls) == 1 else b""
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(utils.requests, "get", get)
    monkeypatch.setattr(utils.time, "sleep", lambda *_: None)
    prices, source = utils.fetch_official_ticket_prices(date(2026, 11, 18))

    assert prices == {date(2026, 11, 18): 10900}
    assert "一部取得" in source and len(calls) == 2


def test_official_show_fetch_records_both_parks_and_stops_on_access_denial(monkeypatch):
    pages = {
        "https://www.tokyodisneyresort.jp/tdl/show/schedule/895/": """
            <h2>2026年11月</h2><table><tr><td>18(水)</td><td>10:50 / 12:15</td></tr></table>
        """,
        "https://www.tokyodisneyresort.jp/tds/show/schedule/967/": """
            <h2>2026年11月</h2><table><tr><td>18(水)</td><td>19:30</td></tr></table>
        """,
    }
    requested = []
    sleeps = []

    class Response:
        status_code = 200
        encoding = "utf-8"
        apparent_encoding = "utf-8"

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def get(url, **kwargs):
        requested.append(url)
        return Response(pages[url])

    monkeypatch.setattr(utils.requests, "get", get)
    monkeypatch.setattr(utils.time, "sleep", sleeps.append)
    frame, message = utils.fetch_official_show_schedule(
        {"show_urls": list(pages)}, target_date=date(2026, 11, 18)
    )
    assert set(frame.show_name) == {
        "ミッキーのマジカルミュージックワールド", "ビリーヴ！～シー・オブ・ドリームス～"
    }
    assert len(frame) == 3
    assert frame.show_time.tolist() == ["10:50", "12:15", "19:30"]
    assert frame.attrs["fetch_status"] == "success"
    assert frame.attrs["requested_count"] == 2
    assert len(requested) == 2 and sleeps == [2]
    assert "3件取得" in message

    denied = Response("")
    denied.status_code = 403
    denials = []
    monkeypatch.setattr(utils.requests, "get", lambda url, **kwargs: denials.append(url) or denied)
    result, _ = utils.fetch_official_show_schedule({"show_urls": list(pages)}, date(2026, 11, 18))
    assert result.empty and result.attrs["fetch_status"] == "blocked"
    assert result.attrs["requested_count"] == 1 and len(denials) == 1


def test_show_schedule_refresh_replaces_only_returned_dates(tmp_path, monkeypatch):
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    conn, cursor = utils.connect_db("stage8.db")
    try:
        original = pd.DataFrame([
            {"target_date": date(2026, 11, 18), "show_name": "ショー", "show_time": "10:00", "source": "official"},
            {"target_date": date(2026, 11, 19), "show_name": "ショー", "show_time": "11:00", "source": "official"},
        ])
        assert utils.save_show_schedule_rows(cursor, conn, "Disneyland", original) == 2
        update = pd.DataFrame([
            {"target_date": date(2026, 11, 18), "show_name": "ショー", "show_time": "10:30", "source": "official"},
        ])
        assert utils.save_show_schedule_rows(cursor, conn, "Disneyland", update) == 1
        saved = pd.read_sql_query(
            "SELECT target_date, show_time FROM show_schedules ORDER BY target_date", conn
        )
        assert list(saved.itertuples(index=False, name=None)) == [
            ("2026-11-18", "10:30"), ("2026-11-19", "11:00")
        ]
    finally:
        conn.close()


def test_show_status_migration_preserves_existing_rows(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "older.db"
    with sqlite3.connect(db_path) as old_db:
        old_db.execute("""
            CREATE TABLE show_schedules (
                observed_at TEXT, target_date TEXT, park TEXT, show_name TEXT,
                show_time TEXT, category TEXT, source TEXT, note TEXT
            )
        """)
        old_db.execute(
            "INSERT INTO show_schedules VALUES (?,?,?,?,?,?,?,?)",
            ("2026-11-01", "2026-11-18", "Disneyland", "既存ショー", "10:00", "ショー", "official", "existing"),
        )
    monkeypatch.setenv("DISNEY_DB_DIR", str(tmp_path))
    conn, _ = utils.connect_db("older.db")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(show_schedules)")}
        saved = conn.execute("SELECT show_name, show_time FROM show_schedules").fetchone()
        assert "status" in columns
        assert saved == ("既存ショー", "10:00")
    finally:
        conn.close()


def test_actual_series_keeps_zero_and_latest_closed_state_as_gap():
    today = pd.Timestamp.now(tz=utils.JST).tz_localize(None).normalize()
    rows = [
        ("zero", 9, 5, 0, 1),
        ("closed", 9, 1, 50, 1),
        ("closed", 9, 14, 0, 0),
        ("ride", 9, 30, 35, 1),
        ("unknown", 10, 0, None, None),
    ]
    history = pd.DataFrame([
        {"datetime": today + pd.Timedelta(hours=hour, minutes=minute),
         "attraction": name, "wait_time": wait, "is_open": opened,
         "date": today.date(), "quality": "valid"}
        for name, hour, minute, wait, opened in rows
    ])

    averaged = utils.get_actual_wait_series_for_today(history, major_rides=["zero", "closed", "ride"])
    by_time = averaged.set_index("TimeLabel")
    assert by_time.loc["09:00", "Actual Wait"] == 0
    assert by_time.loc["09:00", "Observation Count"] == 1
    assert pd.isna(by_time.loc["09:15", "Actual Wait"])
    assert by_time.loc["09:30", "Actual Wait"] == 35
    assert averaged.attrs["expected_slots"] == 48
    assert averaged.attrs["valid_slots"] == 2

    closed = utils.get_actual_wait_series_for_today(history, attraction="closed").set_index("TimeLabel")
    assert pd.isna(closed.loc["09:00", "Actual Wait"])
    assert closed.loc["09:00", "Observation Status"] == "休止・閉園"
    unknown = utils.get_actual_wait_series_for_today(history, attraction="unknown").set_index("TimeLabel")
    assert unknown.loc["10:00", "Observation Status"] == "欠測"


def test_dark_theme_css_and_mobile_text_sizes_are_explicit():
    source = open("app.py", encoding="utf-8").read()
    assert "--ios-bg: transparent" in source
    assert "--ios-text: currentColor" in source
    assert "--ios-control-text: currentColor" in source
    assert "color-mix(in srgb, currentColor" in source
    assert ".stApp {" in source
    assert "[data-testid=\"stAppViewContainer\"]" in source
    assert "background: transparent;" in source
    assert '[data-theme="dark"]' not in source
    assert "@media (max-width: 760px)" in source
    assert "font-size:13px" not in source and "font-size: 13px" not in source


def test_daily_external_data_retry_keeps_success_but_retries_stale_failure():
    today = datetime.now(utils.JST).date()
    success = pd.DataFrame([{
        "target_date": today, "park": "Disneyland", "source": "ticket_price",
        "status": "success", "fetched_at": datetime.now(utils.JST),
    }])
    failed = success.copy()
    failed.loc[0, "status"] = "failed"
    failed.loc[0, "fetched_at"] = datetime.now(utils.JST) - timedelta(minutes=61)

    assert not utils.should_retry_daily_fetch(success, "Disneyland", "ticket_price", today)
    assert utils.should_retry_daily_fetch(failed, "Disneyland", "ticket_price", today)



def test_calendar_capture_dates_coexist_with_full_timestamp_rows():
    import sqlite3

    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE park_hours (observed_at TEXT, target_date TEXT, park TEXT, open_hour REAL, close_hour REAL, ticket_price INTEGER, source TEXT, note TEXT)")
        conn.executemany(
            "INSERT INTO park_hours VALUES (?,?,?,?,?,?,?,?)",
            [
                ("2026-11-18T08:00:00+09:00", "2026-11-18", "Disneyland", 9, 21, 10900, "live", "before import"),
                ("2026-11-18", "2026-11-19", "Disneyland", 9, 21, 10900, "official_calendar_archive_2026-11-18", "imported capture date"),
            ],
        )
        conn.execute("CREATE TABLE ticket_price_snapshots (observed_at TEXT, target_date TEXT, park TEXT, price INTEGER, source TEXT)")
        conn.executemany(
            "INSERT INTO ticket_price_snapshots VALUES (?,?,?,?,?)",
            [
                ("2026-11-18T08:00:00+09:00", "2026-11-18", "Disneyland", 10900, "live"),
                ("2026-11-18", "2026-11-19", "Disneyland", 10900, "official_calendar_archive_2026-11-18"),
            ],
        )

        hours = utils.load_park_hours(conn)
        prices = utils.load_ticket_price_snapshots(conn)

    assert len(hours) == 2
    assert hours["observed_at"].notna().all()
    assert hours.loc[0, "observed_at"] == pd.Timestamp("2026-11-18 08:00:00")
    assert hours.loc[1, "observed_at"] == pd.Timestamp("2026-11-18 00:00:00")
    assert len(prices) == 2
    assert prices["observed_at"].notna().all()
