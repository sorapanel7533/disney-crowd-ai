import re
import sqlite3
from datetime import date, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

try:
    import jpholiday
except ImportError:
    jpholiday = None

JST = ZoneInfo("Asia/Tokyo")
OPEN_HOUR = 9
CROWD_END_HOUR = 21

PARK_SETTINGS = {
    "DisneySea": {
        "url": "https://queue-times.com/parks/275/queue_times.json",
        "urtrip_url": "https://urtrip.jp/tds-attraction-waitingtime-realtime/#pass_status",
        "show_url": "https://www.tokyodisneyresort.jp/tds/daily/calendar.html",
        "show_urls": [
            "https://www.tokyodisneyresort.jp/tds/daily/calendar.html",
            "https://www.tokyodisneyresort.jp/tds/realtime",
            "https://www.tokyodisneyresort.jp/tds/show/schedule/967/",
            "https://www.tokyodisneyresort.jp/tds/show/schedule/7801/",
            "https://www.tokyodisneyresort.jp/tds/show/schedule/7602/",
            "https://www.tokyodisneyresort.jp/tds/show/schedule/7604/",
            "https://www.tokyodisneyresort.jp/tds/show/schedule/7405/"
        ],
        "db": "disneysea.db",
        "rides": [
            "Journey to the Center of the Earth",
            "Tower of Terror",
            "Anna and Elsa's Frozen Journey",
            "Soaring: Fantastic Flight",
            "Toy Story Mania!"
        ],
        "dpa_order": [
            "Soaring: Fantastic Flight",
            "Anna and Elsa's Frozen Journey",
            "Toy Story Mania!",
            "Journey to the Center of the Earth",
            "Rapunzel's Lantern Festival",
            "Peter Pan's Never Land Adventure",
            "Tower of Terror"
        ]
    },
    "Disneyland": {
        "url": "https://queue-times.com/parks/274/queue_times.json",
        "urtrip_url": "https://urtrip.jp/tdl-attraction-waitingtime-realtime/#pass_status",
        "show_url": "https://www.tokyodisneyresort.jp/tdl/daily/calendar.html",
        "show_urls": [
            "https://www.tokyodisneyresort.jp/tdl/daily/calendar.html",
            "https://www.tokyodisneyresort.jp/tdl/realtime",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/7800/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/913/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/895/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/7000/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/7202/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/7002/",
            "https://www.tokyodisneyresort.jp/tdl/show/schedule/985/"
        ],
        "db": "disneyland.db",
        "rides": [
            "Enchanted Tale of Beauty and the Beast",
            "The Happy Ride with Baymax",
            "Monsters, Inc. Ride & Go Seek!",
            "Pooh's Hunny Hunt",
            "Splash Mountain"
        ],
        "dpa_order": [
            "Enchanted Tale of Beauty and the Beast",
            "The Happy Ride with Baymax",
            "Splash Mountain"
        ]
    }
}

CASTEL_TICKET_URL = "https://castel.jp/p/7339"
OFFICIAL_TICKET_URL = "https://www.tokyodisneyresort.co.jp/en/ticket/index.html"
OFFICIAL_CALENDAR_URL = "https://www.tokyodisneyresort.jp/en/tdr/calendar.html"
GLOBAL_PREDICTION_NAME = "__ALL__"
MAJOR_AVERAGE_NAME = "__MAJOR_5_AVERAGE__"
PARK_CROWD_BASELINES = {
    "DisneySea": {
        "avg_wait_normal": 28,
        "top_quartile_wait_normal": 62,
        "max_wait_normal": 125,
        "std_wait_normal": 34,
        "score_scale": 0.92,
    },
    "Disneyland": {
        "avg_wait_normal": 22,
        "top_quartile_wait_normal": 52,
        "max_wait_normal": 105,
        "std_wait_normal": 29,
        "score_scale": 1.02,
    },
}
HOUR_PROFILE = {
    9: 0.82,
    10: 0.96,
    11: 1.08,
    12: 1.00,
    13: 1.03,
    14: 1.12,
    15: 1.16,
    16: 1.09,
    17: 0.96,
    18: 0.84,
    19: 0.74,
    20: 0.66,
}


def is_crowd_hour(dt):
    return OPEN_HOUR <= dt.hour < CROWD_END_HOUR


def is_japanese_holiday(d):
    if jpholiday is None:
        return False

    return jpholiday.is_holiday(d)


def filter_crowd_history(df):
    if len(df) == 0:
        return df

    filtered_df = df.copy()

    if "hour" not in filtered_df.columns and "datetime" in filtered_df.columns:
        filtered_df["hour"] = pd.to_datetime(filtered_df["datetime"]).dt.hour

    if "hour" not in filtered_df.columns:
        return filtered_df

    return filtered_df[
        (filtered_df["hour"] >= OPEN_HOUR)
        &
        (filtered_df["hour"] < CROWD_END_HOUR)
    ].copy()


def connect_db(db_name):
    conn = sqlite3.connect(db_name, check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wait_times (
        datetime TEXT,
        attraction TEXT,
        wait_time INTEGER,
        temperature REAL,
        rain REAL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        created_at TEXT,
        target_hour INTEGER,
        predicted_wait REAL,
        actual_wait REAL,
        error REAL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_crowd_predictions (
        created_at TEXT,
        target_date TEXT,
        predicted_crowd_index REAL,
        actual_crowd_index REAL,
        error REAL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dpa_sellouts (
        observed_at TEXT,
        target_date TEXT,
        attraction TEXT,
        sellout_hour REAL,
        source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dpa_fetch_logs (
        fetched_at TEXT,
        target_date TEXT,
        park TEXT,
        source TEXT,
        status TEXT,
        message TEXT,
        saved_count INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS data_fetch_logs (
        fetched_at TEXT,
        target_date TEXT,
        park TEXT,
        source TEXT,
        status TEXT,
        message TEXT,
        saved_count INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS weather_snapshots (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        temperature REAL,
        rain REAL,
        weather_text TEXT,
        source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_price_snapshots (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        price INTEGER,
        source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attraction_status_snapshots (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        attraction TEXT,
        wait_time INTEGER,
        is_open INTEGER,
        is_major INTEGER,
        source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS park_hours (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        open_hour REAL,
        close_hour REAL,
        ticket_price INTEGER,
        source TEXT,
        note TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS event_signals (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        event_name TEXT,
        bonus REAL,
        source TEXT,
        note TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS show_schedules (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        show_name TEXT,
        show_time TEXT,
        category TEXT,
        source TEXT,
        note TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS show_wait_context (
        observed_at TEXT,
        target_date TEXT,
        park TEXT,
        show_name TEXT,
        show_time TEXT,
        minutes_to_show REAL,
        avg_major_wait REAL,
        max_major_wait REAL,
        open_major_count INTEGER,
        source TEXT
    )
    """)

    conn.commit()

    cursor.execute("PRAGMA table_info(predictions)")
    columns = [row[1] for row in cursor.fetchall()]

    if "attraction" not in columns:
        cursor.execute("ALTER TABLE predictions ADD COLUMN attraction TEXT")
        cursor.execute("""
        UPDATE predictions
        SET attraction = ?
        WHERE attraction IS NULL
        """, (GLOBAL_PREDICTION_NAME,))
        conn.commit()

    if "target_minute" not in columns:
        cursor.execute("ALTER TABLE predictions ADD COLUMN target_minute INTEGER DEFAULT 0")
        conn.commit()

    if "target_time" not in columns:
        cursor.execute("ALTER TABLE predictions ADD COLUMN target_time TEXT")
        cursor.execute("""
        UPDATE predictions
        SET target_time = printf('%02d:00', target_hour)
        WHERE target_time IS NULL
        """)
        conn.commit()

    return conn, cursor


def get_weather():
    try:
        weather_url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=35.6329"
            "&longitude=139.8804"
            "&current=temperature_2m,precipitation,weather_code"
            "&hourly=temperature_2m,precipitation"
            "&daily=temperature_2m_max,precipitation_sum"
            "&timezone=Asia%2FTokyo"
        )

        res = requests.get(weather_url, timeout=5)
        data = res.json()

        current = data["current"]
        hourly = pd.DataFrame(data.get("hourly", {}))
        daily = pd.DataFrame(data.get("daily", {}))

        if len(hourly) > 0 and "time" in hourly.columns:
            hourly["time"] = pd.to_datetime(hourly["time"])

        if len(daily) > 0 and "time" in daily.columns:
            daily["time"] = pd.to_datetime(daily["time"])

        temp = current.get("temperature_2m", 0)
        rain = current.get("precipitation", 0)
        code = current.get("weather_code", 0)

        if rain > 0:
            weather_text = "雨"
        elif code in [0, 1]:
            weather_text = "晴れ"
        elif code in [2, 3]:
            weather_text = "くもり"
        else:
            weather_text = "その他"

        return temp, rain, weather_text, hourly, daily

    except Exception:
        return 0, 0, "取得失敗", pd.DataFrame(), pd.DataFrame()


def fetch_wait_times(settings):
    response = requests.get(
        settings["url"],
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )

    data = response.json()

    all_results = []

    for ride in data["rides"]:
        all_results.append({
            "Attraction": ride["name"],
            "Wait": ride["wait_time"],
            "Open": ride["is_open"]
        })

    all_df = pd.DataFrame(all_results)

    target_df = all_df[
        all_df["Attraction"].isin(settings["rides"])
    ].copy()

    return all_df, target_df


def get_valid_open_df(df):
    if df.empty:
        return df

    return df[
        (df["Open"] == True)
        &
        (df["Wait"] > 0)
    ].copy()


def fetch_castel_ticket_prices():
    try:
        res = requests.get(
            CASTEL_TICKET_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=4
        )

        html = res.text
        plain = re.sub(r"<[^>]+>", "\n", html)
        plain = unescape(plain)
        plain = re.sub(r"\r", "\n", plain)

        if "ディズニーチケット価格カレンダー(月曜始まり)" in plain:
            plain = plain.split("ディズニーチケット価格カレンダー(月曜始まり)")[0]

        lines = [x.strip() for x in plain.split("\n") if x.strip()]

        prices = {}
        current_year = None
        current_month = None
        pending_day = None

        for line in lines:
            month_match = re.search(
                r"(\d{4})年\s*(\d{1,2})月\s*ディズニーチケット価格表",
                line
            )

            if month_match:
                current_year = int(month_match.group(1))
                current_month = int(month_match.group(2))
                pending_day = None
                continue

            if current_year is None or current_month is None:
                continue

            day_match = re.match(r"^(\d{1,2})(?:\s|$)", line)

            if day_match:
                day = int(day_match.group(1))
                if 1 <= day <= 31:
                    pending_day = day
                continue

            price_match = re.match(r"^(7900|8400|8900|9400|9900|10900)$", line)

            if price_match and pending_day is not None:
                price = int(price_match.group(1))

                try:
                    d = datetime(current_year, current_month, pending_day).date()
                    prices[d] = price
                except ValueError:
                    pass

                pending_day = None

        return prices, "Castelから取得"

    except Exception:
        return {}, "取得失敗"


def fetch_official_ticket_prices():
    try:
        res = requests.get(
            OFFICIAL_TICKET_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=4
        )
        res.raise_for_status()

        plain = re.sub(r"<[^>]+>", "\n", res.text)
        plain = unescape(plain)
        lines = [x.strip() for x in plain.splitlines() if x.strip()]

        month_map = {
            "Jan": 1,
            "Feb": 2,
            "Mar": 3,
            "Apr": 4,
            "May": 5,
            "Jun": 6,
            "Jul": 7,
            "Aug": 8,
            "Sep": 9,
            "Oct": 10,
            "Nov": 11,
            "Dec": 12,
        }

        prices = {}
        current_year = None
        month_queue = []
        current_month = None
        pending_day = None

        for line in lines:
            year_match = re.search(r"\b(20\d{2})\b", line)
            month_hits = re.findall(
                r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
                line
            )

            if year_match and month_hits:
                current_year = int(year_match.group(1))
                month_queue = [month_map[x] for x in month_hits]
                current_month = month_queue.pop(0)
                pending_day = None
                continue

            if "As of" in line:
                if month_queue:
                    current_month = month_queue.pop(0)
                pending_day = None
                continue

            if current_year is None or current_month is None:
                continue

            if re.fullmatch(r"\d{1,2}", line):
                day = int(line)
                if 1 <= day <= 31:
                    pending_day = day
                continue

            price_match = re.fullmatch(
                r"(7,900|8,400|8,900|9,400|9,900|10,900|7900|8400|8900|9400|9900|10900)",
                line
            )

            if price_match and pending_day is not None:
                price = int(price_match.group(1).replace(",", ""))
                try:
                    prices[date(current_year, current_month, pending_day)] = price
                except ValueError:
                    pass
                pending_day = None

        return prices, "東京ディズニーリゾート公式サイト"

    except Exception:
        return {}, "公式サイト取得失敗"


def fetch_ticket_prices():
    official_prices, official_source = fetch_official_ticket_prices()

    if official_prices:
        return official_prices, official_source

    castel_prices, castel_source = fetch_castel_ticket_prices()
    return castel_prices, castel_source


def get_ticket_price_from_castel(target_date, price_map):
    d = target_date.date() if isinstance(target_date, datetime) else target_date

    if d in price_map:
        return price_map[d], "価格表から取得"

    return estimate_ticket_price(d), "公式価格未取得のため日付要因から推定"


def estimate_ticket_price(target_date):
    d = target_date.date() if isinstance(target_date, datetime) else target_date
    month = d.month
    day = d.day
    weekday = d.weekday()

    price = 7900

    if weekday >= 5:
        price = 9900
    elif weekday == 4:
        price = 8900
    else:
        price = 8400

    if is_japanese_holiday(d):
        price = max(price, 9900)

    if (month == 3 and day >= 20) or (month == 4 and day <= 7):
        price = max(price, 9900)

    if (month == 4 and day >= 27) or (month == 5 and day <= 6):
        price = max(price, 10900)

    if (month == 7 and day >= 20) or month == 8 or month == 10:
        price = max(price, 9900)

    if (month == 12 and day <= 25) or (month == 12 and day >= 26) or (month == 1 and day <= 5):
        price = max(price, 10900)

    return price


def get_ticket_bonus(price):
    bonus = 0
    reasons = []

    if price is None:
        reasons.append("チケット価格未取得")
        return bonus, reasons

    if price >= 10900:
        bonus += 4
        reasons.append("最高価格帯")
    elif price >= 9900:
        bonus += 3
        reasons.append("高価格帯")
    elif price >= 8900:
        bonus += 2
        reasons.append("やや高価格帯")
    elif price >= 8400:
        bonus += 1
        reasons.append("中価格帯")
    else:
        reasons.append("低価格帯")

    return bonus, reasons


def get_calendar_bonus(target_date, price):
    bonus = 0
    reasons = []

    d = target_date.date() if isinstance(target_date, datetime) else target_date
    month = d.month
    day = d.day
    weekday = d.weekday()

    if weekday >= 5:
        bonus += 2
        reasons.append("土日")

    if is_japanese_holiday(d):
        bonus += 2
        reasons.append("祝日")
    elif jpholiday is None:
        reasons.append("祝日判定なし")

    tomorrow = d + timedelta(days=1)

    if is_japanese_holiday(tomorrow):
        bonus += 1
        reasons.append("祝前日")

    if month == 3 and day >= 20:
        bonus += 3
        reasons.append("春休み")

    if month == 4 and day <= 7:
        bonus += 2
        reasons.append("春休み")

    if (month == 4 and day >= 27) or (month == 5 and day <= 6):
        bonus += 3
        reasons.append("GW")

    if (month == 7 and day >= 20) or month == 8:
        bonus += 3
        reasons.append("夏休み")

    if month == 8 and 10 <= day <= 18:
        bonus += 2
        reasons.append("お盆")

    if month == 9 and day >= 15:
        bonus += 1
        reasons.append("ハロウィン前半")

    if month == 10:
        bonus += 3
        reasons.append("ハロウィン")

    if month == 11 and day >= 8:
        bonus += 1
        reasons.append("クリスマス前半")

    if month == 12 and day <= 25:
        bonus += 3
        reasons.append("クリスマス")

    if month == 12 and day >= 26:
        bonus += 4
        reasons.append("年末")

    if month == 1 and day <= 5:
        bonus += 4
        reasons.append("年始")

    if month == 1 and 15 <= day <= 31 and weekday < 5:
        bonus += 1
        reasons.append("学校休み/入試休み推定")

    if month == 2 and 1 <= day <= 20 and weekday < 5:
        bonus += 1
        reasons.append("学校休み/入試休み推定")

    if month == 6 and 15 <= day <= 25 and weekday < 5:
        bonus += 1
        reasons.append("県民の日/学校休み推定")

    ticket_bonus, ticket_reasons = get_ticket_bonus(price)
    bonus += ticket_bonus
    reasons.extend(ticket_reasons)

    return bonus, reasons


def load_history(conn):
    history_df = pd.read_sql_query(
        "SELECT * FROM wait_times",
        conn
    )

    if len(history_df) > 0:
        history_df["datetime"] = pd.to_datetime(history_df["datetime"])
        history_df["hour"] = history_df["datetime"].dt.hour
        history_df["date"] = history_df["datetime"].dt.date

    return history_df


def load_prediction_history(conn):
    prediction_df = pd.read_sql_query(
        "SELECT * FROM predictions",
        conn
    )

    if len(prediction_df) > 0:
        prediction_df["created_at"] = pd.to_datetime(prediction_df["created_at"])

        if "attraction" not in prediction_df.columns:
            prediction_df["attraction"] = GLOBAL_PREDICTION_NAME

        prediction_df["attraction"] = prediction_df["attraction"].fillna(
            GLOBAL_PREDICTION_NAME
        )

    return prediction_df


def save_wait_times(cursor, conn, valid_open_df, temperature, rain_mm):
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    if len(valid_open_df) > 0:
        for _, row in valid_open_df.iterrows():
            cursor.execute("""
            INSERT INTO wait_times
            VALUES (?, ?, ?, ?, ?)
            """, (
                now,
                row["Attraction"],
                row["Wait"],
                temperature,
                rain_mm
            ))

        conn.commit()


def save_prediction_rows(cursor, conn, pred_df, attraction_name):
    created_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    for _, row in pred_df.iterrows():
        hour = int(row.get("Hour", 0))
        minute = int(row.get("Minute", 0)) if not pd.isna(row.get("Minute", 0)) else 0
        target_time = str(row.get("TimeLabel", f"{hour:02d}:{minute:02d}"))
        cursor.execute("""
        INSERT INTO predictions
        (
            created_at,
            target_hour,
            target_minute,
            target_time,
            predicted_wait,
            actual_wait,
            error,
            attraction
        )
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
        """, (
            created_at,
            hour,
            minute,
            target_time,
            float(row["Predicted Wait"]),
            attraction_name
        ))

    conn.commit()


def update_prediction_feedback(cursor, conn, valid_open_df, avg_wait):
    now = datetime.now(JST)
    now_hour = now.hour
    now_minute = (now.minute // 15) * 15
    now_time = f"{now_hour:02d}:{now_minute:02d}"

    if not is_crowd_hour(now):
        return

    if len(valid_open_df) == 0:
        return

    actual_now = float(avg_wait)
    today_prefix = now.strftime("%Y-%m-%d")

    cursor.execute("""
    UPDATE predictions
    SET actual_wait = ?,
        error = ? - predicted_wait
    WHERE target_hour = ?
    AND COALESCE(target_minute, 0) = ?
    AND actual_wait IS NULL
    AND attraction = ?
    AND created_at LIKE ?
    """, (
        actual_now,
        actual_now,
        now_hour,
        now_minute,
        GLOBAL_PREDICTION_NAME,
        f"{today_prefix}%"
    ))

    for _, row in valid_open_df.iterrows():
        attraction = row["Attraction"]
        actual_wait = float(row["Wait"])

        cursor.execute("""
        UPDATE predictions
        SET actual_wait = ?,
            error = ? - predicted_wait
        WHERE target_hour = ?
        AND COALESCE(target_minute, 0) = ?
        AND actual_wait IS NULL
        AND attraction = ?
        AND created_at LIKE ?
        """, (
            actual_wait,
            actual_wait,
            now_hour,
            now_minute,
            attraction,
            f"{today_prefix}%"
        ))

    conn.commit()


def get_feedback_error(prediction_history, attraction_name=GLOBAL_PREDICTION_NAME):
    if len(prediction_history) == 0:
        return 0

    if "attraction" not in prediction_history.columns:
        return 0

    if "error" not in prediction_history.columns:
        return 0

    error_df = prediction_history[
        (prediction_history["attraction"] == attraction_name)
        &
        (prediction_history["error"].notna())
    ]

    if len(error_df) > 0:
        return error_df["error"].tail(30).mean()

    return 0


def load_daily_crowd_predictions(conn):
    try:
        daily_df = pd.read_sql_query(
            "SELECT * FROM daily_crowd_predictions",
            conn
        )
    except Exception:
        return pd.DataFrame()

    if len(daily_df) > 0:
        daily_df["created_at"] = pd.to_datetime(daily_df["created_at"])
        daily_df["target_date"] = pd.to_datetime(daily_df["target_date"]).dt.date

    return daily_df


def get_daily_crowd_feedback_error(daily_prediction_history):
    if len(daily_prediction_history) == 0 or "error" not in daily_prediction_history.columns:
        return 0

    error_df = daily_prediction_history[
        daily_prediction_history["error"].notna()
    ]

    if len(error_df) == 0:
        return 0

    return float(error_df["error"].tail(14).mean())


def save_daily_crowd_prediction(cursor, conn, target_date, predicted_crowd_index):
    created_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    target_date_text = target_date.strftime("%Y-%m-%d")

    cursor.execute("""
    DELETE FROM daily_crowd_predictions
    WHERE target_date = ?
    AND actual_crowd_index IS NULL
    """, (target_date_text,))

    cursor.execute("""
    INSERT INTO daily_crowd_predictions
    (
        created_at,
        target_date,
        predicted_crowd_index,
        actual_crowd_index,
        error
    )
    VALUES (?, ?, ?, NULL, NULL)
    """, (
        created_at,
        target_date_text,
        float(predicted_crowd_index)
    ))

    conn.commit()


def get_locked_daily_prediction(conn, target_date):
    if conn is None:
        return None

    target_date_text = target_date.strftime("%Y-%m-%d")
    try:
        row = pd.read_sql_query(
            """
            SELECT predicted_crowd_index
            FROM daily_crowd_predictions
            WHERE target_date = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            conn,
            params=(target_date_text,)
        )
    except Exception:
        return None

    if len(row) == 0:
        return None

    value = row.iloc[0].get("predicted_crowd_index")
    if value is None or pd.isna(value):
        return None

    return float(value)


def save_locked_daily_prediction(cursor, conn, target_date, predicted_crowd_index):
    if cursor is None or conn is None:
        return False

    existing = get_locked_daily_prediction(conn, target_date)
    if existing is not None:
        return False

    created_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    target_date_text = target_date.strftime("%Y-%m-%d")

    cursor.execute("""
    INSERT INTO daily_crowd_predictions
    (
        created_at,
        target_date,
        predicted_crowd_index,
        actual_crowd_index,
        error
    )
    VALUES (?, ?, ?, NULL, NULL)
    """, (
        created_at,
        target_date_text,
        float(predicted_crowd_index)
    ))

    conn.commit()
    return True


def update_daily_crowd_feedback(cursor, conn, history_df, settings, park=None):
    if history_df is None or len(history_df) == 0:
        return

    today = datetime.now(JST).date()
    target_history = filter_crowd_history(history_df.copy())
    if len(target_history) == 0:
        return

    cursor.execute("""
    SELECT rowid, target_date
    FROM daily_crowd_predictions
    WHERE actual_crowd_index IS NULL
    """)
    rows = cursor.fetchall()

    for rowid, target_date_text in rows:
        target_day = datetime.strptime(target_date_text, "%Y-%m-%d").date()
        if target_day >= today:
            continue

        day_df = target_history[
            (target_history["date"] == target_day)
            & (target_history["wait_time"] > 0)
        ].copy()
        if len(day_df) < 24:
            continue
        if "attraction" in day_df.columns and day_df["attraction"].nunique() < 3:
            continue

        stats = get_all_attraction_crowd_stats(None, day_df, target_day)
        if stats["sample_count"] < 24 or stats["open_count"] < 3:
            continue

        actual_index = get_crowd_index_for_park(
            park or settings.get("park", "DisneySea"),
            stats["avg_wait"],
            stats["max_wait"],
            stats["top_quartile_wait"],
            stats["std_wait"],
            stats["open_count"],
            stats["closed_count"],
            0,
            0,
            0,
        )

        cursor.execute("""
        UPDATE daily_crowd_predictions
        SET actual_crowd_index = ?,
            error = ? - predicted_crowd_index
        WHERE rowid = ?
        """, (
            float(actual_index),
            float(actual_index),
            rowid
        ))

    conn.commit()

def load_dpa_sellouts(conn):
    try:
        sellout_df = pd.read_sql_query(
            "SELECT * FROM dpa_sellouts",
            conn
        )
    except Exception:
        return pd.DataFrame()

    if len(sellout_df) > 0:
        sellout_df["observed_at"] = pd.to_datetime(sellout_df["observed_at"])
        sellout_df["target_date"] = pd.to_datetime(sellout_df["target_date"]).dt.date

    return sellout_df


def load_dpa_fetch_logs(conn):
    try:
        log_df = pd.read_sql_query(
            "SELECT * FROM dpa_fetch_logs",
            conn
        )
    except Exception:
        return pd.DataFrame()

    if len(log_df) > 0:
        log_df["fetched_at"] = pd.to_datetime(log_df["fetched_at"])
        log_df["target_date"] = pd.to_datetime(log_df["target_date"]).dt.date

    return log_df


def log_dpa_fetch(cursor, conn, park, source, status, message, saved_count=0, target_date=None):
    now = datetime.now(JST)
    target_date = target_date or now.date()
    target_date_text = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)

    cursor.execute("""
    INSERT INTO dpa_fetch_logs
    (
        fetched_at,
        target_date,
        park,
        source,
        status,
        message,
        saved_count
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        now.strftime("%Y-%m-%d %H:%M:%S"),
        target_date_text,
        park,
        source,
        status,
        str(message),
        int(saved_count)
    ))
    conn.commit()


def load_data_fetch_logs(conn):
    try:
        log_df = pd.read_sql_query(
            "SELECT * FROM data_fetch_logs",
            conn
        )
    except Exception:
        return pd.DataFrame()

    if len(log_df) > 0:
        log_df["fetched_at"] = pd.to_datetime(log_df["fetched_at"])
        log_df["target_date"] = pd.to_datetime(log_df["target_date"]).dt.date

    return log_df


def log_data_fetch(cursor, conn, park, source, status, message, saved_count=0, target_date=None):
    now = datetime.now(JST)
    target_date = target_date or now.date()
    target_date_text = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)

    cursor.execute("""
    INSERT INTO data_fetch_logs
    (
        fetched_at,
        target_date,
        park,
        source,
        status,
        message,
        saved_count
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        now.strftime("%Y-%m-%d %H:%M:%S"),
        target_date_text,
        park,
        source,
        status,
        str(message),
        int(saved_count)
    ))
    conn.commit()


def should_fetch_data_today(fetch_logs, park, source, target_date=None):
    target_date = target_date or datetime.now(JST).date()

    if len(fetch_logs) == 0 or "target_date" not in fetch_logs.columns:
        return True

    today_logs = fetch_logs[
        (fetch_logs["target_date"] == target_date)
        & (fetch_logs.get("park", "") == park)
        & (fetch_logs.get("source", "") == source)
    ]

    return len(today_logs) == 0


def save_weather_snapshot(cursor, conn, park, temperature, rain_mm, weather_text, source="open-meteo"):
    now = datetime.now(JST)
    today_text = now.date().strftime("%Y-%m-%d")

    cursor.execute("""
    INSERT INTO weather_snapshots
    (
        observed_at,
        target_date,
        park,
        temperature,
        rain,
        weather_text,
        source
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        now.strftime("%Y-%m-%d %H:%M:%S"),
        today_text,
        park,
        float(temperature or 0),
        float(rain_mm or 0),
        str(weather_text),
        source
    ))
    conn.commit()


def save_ticket_price_snapshots(cursor, conn, park, ticket_price_map, source):
    if not ticket_price_map:
        return 0

    now = datetime.now(JST)
    saved_count = 0

    for target_date, price in ticket_price_map.items():
        target_date_text = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)

        cursor.execute("""
        DELETE FROM ticket_price_snapshots
        WHERE target_date = ?
        AND park = ?
        AND source = ?
        """, (
            target_date_text,
            park,
            source
        ))

        cursor.execute("""
        INSERT INTO ticket_price_snapshots
        (
            observed_at,
            target_date,
            park,
            price,
            source
        )
        VALUES (?, ?, ?, ?, ?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            target_date_text,
            park,
            int(price),
            source
        ))
        saved_count += 1

    conn.commit()
    return saved_count


def _date_text(target_date):
    return target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)


def _parse_hour_text(text):
    match = re.search(r"(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.)", str(text), re.IGNORECASE)

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    suffix = match.group(3).lower()

    if suffix.startswith("p") and hour != 12:
        hour += 12
    if suffix.startswith("a") and hour == 12:
        hour = 0

    return hour + minute / 60


def save_attraction_status_snapshots(cursor, conn, park, all_df, major_rides):
    if len(all_df) == 0:
        return 0

    now = datetime.now(JST)
    saved_count = 0

    for _, row in all_df.iterrows():
        cursor.execute("""
        INSERT INTO attraction_status_snapshots
        (
            observed_at,
            target_date,
            park,
            attraction,
            wait_time,
            is_open,
            is_major,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.date().strftime("%Y-%m-%d"),
            park,
            row["Attraction"],
            int(row["Wait"]),
            1 if bool(row["Open"]) else 0,
            1 if row["Attraction"] in major_rides else 0,
            "queue-times"
        ))
        saved_count += 1

    conn.commit()
    return saved_count


def load_attraction_status_snapshots(conn):
    try:
        df = pd.read_sql_query("SELECT * FROM attraction_status_snapshots", conn)
    except Exception:
        return pd.DataFrame()

    if len(df) > 0:
        df["observed_at"] = pd.to_datetime(df["observed_at"])
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    return df


def get_attraction_status_summary(status_df, settings):
    if len(status_df) == 0:
        return pd.DataFrame([{
            "項目": "アトラクション営業状態",
            "状態": "未保存",
            "説明": "待ち時間APIから営業中/休止状態を保存すると、休止による予測ズレを判定できます。"
        }])

    today = datetime.now(JST).date()
    today_df = status_df[status_df["target_date"] == today].copy()

    if len(today_df) == 0:
        return pd.DataFrame([{
            "項目": "アトラクション営業状態",
            "状態": "今日の保存なし",
            "説明": "今日の営業状態がまだ保存されていません。"
        }])

    latest_time = today_df["observed_at"].max()
    latest_df = today_df[today_df["observed_at"] == latest_time]
    major_df = latest_df[latest_df["attraction"].isin(settings["rides"])]
    major_closed = int((major_df["is_open"] == 0).sum()) if len(major_df) > 0 else 0
    all_closed = int((latest_df["is_open"] == 0).sum())

    return pd.DataFrame([
        {
            "項目": "5大アトラクション休止数",
            "状態": f"{major_closed}件",
            "説明": "5大内の休止が多いと、他施設へ待ち時間が集中しやすくなります。"
        },
        {
            "項目": "全体休止数",
            "状態": f"{all_closed}件",
            "説明": "全体の休止が多い日は、通常の曜日パターンより待ち時間が上振れしやすくなります。"
        },
    ])


def build_estimated_park_hours(start_date=None, days=90):
    start_date = start_date or datetime.now(JST).date()
    rows = []

    for i in range(days):
        d = start_date + timedelta(days=i)
        rows.append({
            "target_date": d,
            "open_hour": 9.0,
            "close_hour": 21.0,
            "ticket_price": estimate_ticket_price(d),
            "source": "rule_estimate",
            "note": "公式営業時間を取得できない場合の推定"
        })

    return pd.DataFrame(rows)


def fetch_official_park_hours():
    try:
        res = requests.get(
            OFFICIAL_CALENDAR_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        res.raise_for_status()
    except Exception as exc:
        estimated_df = build_estimated_park_hours()
        return estimated_df, f"公式カレンダー取得失敗。推定営業時間を使用: {exc}"

    plain = re.sub(r"<[^>]+>", "\n", res.text)
    plain = unescape(plain)
    plain = re.sub(r"\s+", " ", plain)
    today = datetime.now(JST).date()
    rows = []

    month_names = {
        "Jan": 1, "January": 1,
        "Feb": 2, "February": 2,
        "Mar": 3, "March": 3,
        "Apr": 4, "April": 4,
        "May": 5,
        "Jun": 6, "June": 6,
        "Jul": 7, "July": 7,
        "Aug": 8, "August": 8,
        "Sep": 9, "September": 9,
        "Oct": 10, "October": 10,
        "Nov": 11, "November": 11,
        "Dec": 12, "December": 12,
    }

    current_year = today.year
    current_month = today.month

    month_match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})", plain)
    if month_match:
        current_month = month_names[month_match.group(1)]
        current_year = int(month_match.group(2))

    pattern = re.compile(
        r"(\d{1,2})\s*\([A-Z]\)\s+"
        r"(\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.))\s*-\s*"
        r"(\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.))\s+"
        r"([\d,]{4,6})\s*yen",
        re.IGNORECASE
    )

    for match in pattern.finditer(plain):
        day = int(match.group(1))
        try:
            d = date(current_year, current_month, day)
        except ValueError:
            continue

        if d < today - timedelta(days=7):
            continue

        rows.append({
            "target_date": d,
            "open_hour": _parse_hour_text(match.group(2)) or 9.0,
            "close_hour": _parse_hour_text(match.group(3)) or 21.0,
            "ticket_price": int(match.group(4).replace(",", "")),
            "source": "official_calendar",
            "note": "東京ディズニーリゾート公式カレンダー"
        })

    if not rows:
        estimated_df = build_estimated_park_hours()
        return estimated_df, "公式カレンダーの解析に失敗。推定営業時間を使用"

    return pd.DataFrame(rows).drop_duplicates(["target_date"], keep="first"), f"公式カレンダーから{len(rows)}件取得"


def save_park_hours_rows(cursor, conn, park, hours_df):
    if len(hours_df) == 0:
        return 0

    now = datetime.now(JST)
    saved_count = 0

    for _, row in hours_df.iterrows():
        target_date_text = _date_text(row["target_date"])
        cursor.execute("""
        DELETE FROM park_hours
        WHERE target_date = ?
        AND park = ?
        AND source = ?
        """, (
            target_date_text,
            park,
            row.get("source", "")
        ))
        cursor.execute("""
        INSERT INTO park_hours
        (
            observed_at,
            target_date,
            park,
            open_hour,
            close_hour,
            ticket_price,
            source,
            note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            target_date_text,
            park,
            float(row.get("open_hour", 9.0)),
            float(row.get("close_hour", 21.0)),
            int(row.get("ticket_price", 0) or 0),
            row.get("source", ""),
            row.get("note", "")
        ))
        saved_count += 1

    conn.commit()
    return saved_count


def load_park_hours(conn):
    try:
        df = pd.read_sql_query("SELECT * FROM park_hours", conn)
    except Exception:
        return pd.DataFrame()

    if len(df) > 0:
        df["observed_at"] = pd.to_datetime(df["observed_at"])
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    return df


def get_park_hours_bonus(park_hours_df, target_date):
    if len(park_hours_df) == 0:
        return 0, []

    d = target_date.date() if isinstance(target_date, datetime) else target_date
    day_rows = park_hours_df[park_hours_df["target_date"] == d]

    if len(day_rows) == 0:
        return 0, []

    row = day_rows.sort_values("observed_at", ascending=False).iloc[0]
    close_hour = float(row.get("close_hour", 21.0) or 21.0)
    open_hour = float(row.get("open_hour", 9.0) or 9.0)
    duration = close_hour - open_hour

    if duration <= 9:
        return 1, [f"短縮営業推定({open_hour:.0f}:00-{close_hour:.0f}:00)"]

    return 0, [f"営業時間{open_hour:.0f}:00-{close_hour:.0f}:00"]


def build_event_signal_rows(park, start_date=None, days=120):
    start_date = start_date or datetime.now(JST).date()
    rows = []

    for i in range(days):
        d = start_date + timedelta(days=i)
        signals = []

        if d.month == 1 and d.day <= 5:
            signals.append(("年始混雑", 2.0, "年始休暇の需要増"))
        if d.month == 3 and d.day >= 20:
            signals.append(("春休み", 1.5, "学生休暇の需要増"))
        if d.month == 4 and d.day <= 7:
            signals.append(("春休み", 1.0, "学生休暇の需要増"))
        if (d.month == 4 and d.day >= 27) or (d.month == 5 and d.day <= 6):
            signals.append(("ゴールデンウィーク", 2.0, "大型連休"))
        if (d.month == 7 and d.day >= 20) or d.month == 8:
            signals.append(("夏休み", 1.5, "学生休暇の需要増"))
        if d.month == 8 and 10 <= d.day <= 18:
            signals.append(("お盆", 1.5, "帰省/旅行需要"))
        if d.month == 10:
            signals.append(("ハロウィーン", 1.0, "季節イベント"))
        if d.month == 12 and d.day <= 25:
            signals.append(("クリスマス", 1.0, "季節イベント"))
        if d.month == 12 and d.day >= 26:
            signals.append(("年末混雑", 2.0, "年末休暇の需要増"))
        if d.weekday() == 0 and 15 <= d.day <= 25 and d.month in [1, 2, 6]:
            signals.append(("学校行事休み推定", 0.5, "地域差のある平日上振れ候補"))

        for event_name, bonus, note in signals:
            rows.append({
                "target_date": d,
                "park": park,
                "event_name": event_name,
                "bonus": bonus,
                "source": "rule_estimate",
                "note": note
            })

    return pd.DataFrame(rows)


def save_event_signal_rows(cursor, conn, park, event_df):
    now = datetime.now(JST)
    cursor.execute(
        "DELETE FROM event_signals WHERE park = ? AND source = ?",
        (park, "rule_estimate")
    )

    if len(event_df) == 0:
        conn.commit()
        return 0

    saved_count = 0

    for _, row in event_df.iterrows():
        cursor.execute("""
        INSERT INTO event_signals
        (
            observed_at,
            target_date,
            park,
            event_name,
            bonus,
            source,
            note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            _date_text(row["target_date"]),
            park,
            row["event_name"],
            float(row["bonus"]),
            row["source"],
            row["note"]
        ))
        saved_count += 1

    conn.commit()
    return saved_count


def load_event_signals(conn):
    try:
        df = pd.read_sql_query("SELECT * FROM event_signals", conn)
    except Exception:
        return pd.DataFrame()

    if len(df) > 0:
        df["observed_at"] = pd.to_datetime(df["observed_at"])
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    return df


def get_event_bonus(event_signals, target_date, park=None):
    if len(event_signals) == 0:
        return 0, []

    d = target_date.date() if isinstance(target_date, datetime) else target_date
    rows = event_signals[event_signals["target_date"] == d].copy()

    if park is not None and "park" in rows.columns:
        rows = rows[rows["park"] == park]

    if len(rows) == 0:
        return 0, []

    bonus = float(rows["bonus"].sum())
    reasons = rows["event_name"].dropna().astype(str).unique().tolist()
    return bonus, reasons


def get_forecast_weather_for_date(daily_weather, target_date, fallback_temperature, fallback_rain):
    d = target_date.date() if isinstance(target_date, datetime) else target_date

    if daily_weather is None or len(daily_weather) == 0 or "time" not in daily_weather.columns:
        return fallback_temperature, fallback_rain, "現在天気を使用"

    weather_df = daily_weather.copy()
    weather_df["forecast_date"] = pd.to_datetime(weather_df["time"]).dt.date
    rows = weather_df[weather_df["forecast_date"] == d]

    if len(rows) == 0:
        return fallback_temperature, fallback_rain, "日別天気予報なし。現在天気を使用"

    row = rows.iloc[0]
    forecast_temperature = row.get("temperature_2m_max", fallback_temperature)
    forecast_rain = row.get("precipitation_sum", fallback_rain)

    if pd.isna(forecast_temperature):
        forecast_temperature = fallback_temperature

    if pd.isna(forecast_rain):
        forecast_rain = fallback_rain

    return float(forecast_temperature), float(forecast_rain), "日別天気予報を使用"



def _parse_show_time_to_hour(show_time):
    text = str(show_time).strip().lower().replace("\u00a0", " ")
    match = re.search(r"(\d{1,2}):(\d{2})\s*(a\.m\.|p\.m\.|am|pm)?", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    suffix = match.group(3) or ""

    if "p" in suffix and hour < 12:
        hour += 12
    if "a" in suffix and hour == 12:
        hour = 0

    return hour + minute / 60


def _clean_show_name(value):
    text = re.sub(r"\s+", " ", unescape(str(value))).strip()
    skip_words = [
        "operation", "schedule", "duration", "about", "search", "filter",
        "previous month", "next month", "disney premier access", "休止", "運営"
    ]
    if not text or len(text) > 90:
        return ""
    if _is_broken_show_text(text):
        return ""
    if any(word in text.lower() for word in skip_words):
        return ""
    return text


def _is_broken_show_text(value):
    text = str(value or "").strip()
    if not text:
        return True
    broken_markers = ["???", "????", "\ufffd", "譁", "縺", "繝", "豌", "髯", "螟", "莠"]
    return any(marker in text for marker in broken_markers)


def _safe_show_text(value, fallback):
    text = str(value or "").strip()
    if _is_broken_show_text(text):
        return fallback
    return text


def _sanitize_show_rows(show_df, fallback_name="ショー/パレード（時刻のみ）"):
    if show_df is None or len(show_df) == 0:
        return pd.DataFrame()

    df = show_df.copy()
    if "show_name" in df.columns:
        df["show_name"] = df["show_name"].apply(lambda x: _safe_show_text(x, fallback_name))
    if "category" in df.columns:
        df["category"] = df["category"].apply(lambda x: _safe_show_text(x, "ショー/パレード"))
    if "note" in df.columns:
        df["note"] = df["note"].apply(lambda x: _safe_show_text(x, "公式または推定時刻"))
    return df


SHOW_NAME_BY_ID = {
    "967": "ビリーヴ！〜シー・オブ・ドリームス〜",
    "7801": "ディズニー・ライト・ザ・ナイト",
    "7602": "ビッグバンドビート〜ア・スペシャルトリート〜",
    "7604": "ジャンボリミッキー！レッツ・ダンス！",
    "7405": "スカイ・フル・オブ・カラーズ",
    "7800": "ディズニー・ライト・ザ・ナイト",
    "913": "エレクトリカルパレード・ドリームライツ",
    "895": "ミッキーのマジカルミュージックワールド",
    "7000": "クラブマウスビート",
    "7202": "Reach for the Stars",
    "7002": "ディズニー・ハーモニー・イン・カラー",
    "985": "ジャンボリミッキー！レッツ・ダンス！",
}



def _known_show_schedule_fallback(park, target_date):
    return pd.DataFrame()


def _show_name_from_url(url):
    for key, value in SHOW_NAME_BY_ID.items():
        if f"/{key}" in str(url):
            return value
    return ""


def _parse_monthly_show_page(html, url, target_date):
    show_name = _show_name_from_url(url)
    if not show_name:
        return []

    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text).replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    day_pattern = re.compile(rf"^{target_date.day}\s*\([^)]*\)\s*(?:\|| )\s*(.+)$")
    rows = []
    for line in lines:
        match = day_pattern.search(line)
        if not match:
            continue
        value = match.group(1)
        if "??" in value or "Rest" in value:
            continue
        times = re.findall(r"\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm)?", value, re.I)
        for show_time in times:
            rows.append({
                "target_date": target_date,
                "show_name": show_name,
                "show_time": show_time.replace("a.m.", "am").replace("p.m.", "pm"),
                "category": "ショー/パレード",
                "source": url,
                "note": "公式月間スケジュールから取得",
            })
    return rows


def _parse_daily_show_page(html, url, target_date):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text).replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    rows = []
    seen = set()
    time_pattern = re.compile(r"\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm)?", re.I)
    stop_words = ["アトラクション", "レストラン", "ショップ", "休止", "運営時間"]

    in_show_section = False
    for line in lines:
        if "ショー/パレード" in line or "Parades and Shows" in line:
            in_show_section = True
            continue
        if in_show_section and any(word in line for word in stop_words):
            break
        if not in_show_section:
            continue
        times = time_pattern.findall(line)
        if not times:
            continue
        name = time_pattern.sub("", line)
        name = re.sub(r"エントリー受付|ディズニー・プレミアアクセス|NEW", "", name)
        name = re.sub(r"\s*/\s*", " / ", name).strip(" ?/")
        name = _clean_show_name(name)
        if not name:
            continue
        for show_time in times:
            key = (name, show_time)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "target_date": target_date,
                "show_name": name[:80],
                "show_time": show_time,
                "category": "ショー/パレード",
                "source": url,
                "note": "公式日別ページから取得",
            })
    return rows


def fetch_official_show_schedule(settings, target_date=None):
    target_date = target_date or datetime.now(JST).date()
    urls = settings.get("show_urls") or [settings.get("show_url", "")]
    urls = [url for url in urls if url]
    if not urls:
        return pd.DataFrame(), "ショー/パレード取得URLが設定されていません"

    rows = []
    messages = []
    failed_count = 0
    for url in urls:
        try:
            res = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )
            if not res.encoding or res.encoding.lower() in ("iso-8859-1", "ascii"):
                res.encoding = res.apparent_encoding or "utf-8"
            res.raise_for_status()
        except Exception as exc:
            failed_count += 1
            messages.append(f"{url}: {exc}")
            if failed_count >= 2 and not rows:
                break
            continue

        if "/show/schedule/" in url:
            rows.extend(_parse_monthly_show_page(res.text, url, target_date))
        else:
            rows.extend(_parse_daily_show_page(res.text, url, target_date))

    if not rows:
        return pd.DataFrame(), "公式ショー時刻を取得できませんでした"

    df = pd.DataFrame(rows).drop_duplicates(["show_name", "show_time"], keep="first")
    df = _sanitize_show_rows(df)
    df["_hour"] = df["show_time"].apply(_parse_show_time_to_hour)
    df = df[df["_hour"].notna()].sort_values("_hour").drop(columns=["_hour"])
    if len(df) == 0:
        return pd.DataFrame(), "公式ショー時刻を取得できませんでした"
    return df.reset_index(drop=True), f"公式ショー/パレード時刻を{len(df)}件取得"

def save_show_schedule_rows(cursor, conn, park, show_df, target_date=None):
    if show_df is None or len(show_df) == 0:
        return 0

    target_date = target_date or datetime.now(JST).date()
    cursor.execute(
        "DELETE FROM show_schedules WHERE target_date = ? AND park = ?",
        (str(target_date), park)
    )

    saved = 0
    observed_at = datetime.now(JST).isoformat()
    show_df = _sanitize_show_rows(show_df)
    for _, row in show_df.iterrows():
        cursor.execute("""
        INSERT INTO show_schedules
        (observed_at, target_date, park, show_name, show_time, category, source, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            observed_at,
            str(row.get("target_date", target_date)),
            park,
            str(row.get("show_name", "")),
            str(row.get("show_time", "")),
            str(row.get("category", "ショー")),
            str(row.get("source", "official")),
            str(row.get("note", "")),
        ))
        saved += 1

    conn.commit()
    return saved


def load_show_schedules(conn):
    try:
        return _sanitize_show_rows(pd.read_sql_query("SELECT * FROM show_schedules", conn))
    except Exception:
        return pd.DataFrame()


def save_show_wait_context(cursor, conn, park, show_df, current_target_df):
    if show_df is None or len(show_df) == 0 or current_target_df is None or len(current_target_df) == 0:
        return 0

    now = datetime.now(JST)
    valid_df = current_target_df[(current_target_df["Open"] == True) & (current_target_df["Wait"] > 0)].copy()
    if len(valid_df) == 0:
        return 0

    avg_wait = float(valid_df["Wait"].mean())
    max_wait = float(valid_df["Wait"].max())
    open_count = int(len(valid_df))
    saved = 0

    for _, row in show_df.iterrows():
        hour_value = _parse_show_time_to_hour(row.get("show_time", ""))
        if hour_value is None:
            continue
        show_dt = datetime.combine(now.date(), datetime.min.time(), tzinfo=JST) + timedelta(hours=hour_value)
        minutes_to_show = (show_dt - now).total_seconds() / 60
        if -180 <= minutes_to_show <= 360:
            cursor.execute("""
            INSERT INTO show_wait_context
            (observed_at, target_date, park, show_name, show_time, minutes_to_show,
             avg_major_wait, max_major_wait, open_major_count, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now.isoformat(),
                str(now.date()),
                park,
                str(row.get("show_name", "")),
                str(row.get("show_time", "")),
                round(minutes_to_show, 1),
                round(avg_wait, 1),
                round(max_wait, 1),
                open_count,
                "show_wait_context",
            ))
            saved += 1

    conn.commit()
    return saved


def load_show_wait_context(conn):
    try:
        return pd.read_sql_query("SELECT * FROM show_wait_context", conn)
    except Exception:
        return pd.DataFrame()


def get_show_wait_insights(show_wait_context):
    if show_wait_context is None or len(show_wait_context) == 0:
        return pd.DataFrame([{
            "分析": "データ不足",
            "内容": "ショー時刻と待ち時間の関係データはまだありません。自動取得後に蓄積されます。",
        }])

    df = show_wait_context.copy()
    if "minutes_to_show" not in df.columns or "avg_major_wait" not in df.columns:
        return pd.DataFrame([{"分析": "列不足", "内容": "分析に必要な列がまだありません。"}])

    df["minutes_to_show"] = pd.to_numeric(df["minutes_to_show"], errors="coerce")
    df["avg_major_wait"] = pd.to_numeric(df["avg_major_wait"], errors="coerce")
    df = df.dropna(subset=["minutes_to_show", "avg_major_wait"])

    if len(df) < 5:
        return pd.DataFrame([{
            "分析": "蓄積中",
            "内容": f"現在{len(df)}件です。傾向を見るにはもう少し記録が必要です。",
        }])

    before = df[(df["minutes_to_show"] >= 0) & (df["minutes_to_show"] <= 90)]
    after = df[(df["minutes_to_show"] < 0) & (df["minutes_to_show"] >= -90)]
    rows = []

    if len(before) > 0:
        rows.append({
            "分析": "ショー前90分",
            "内容": f"5大平均待ち時間は約{before['avg_major_wait'].mean():.1f}分です。",
        })
    if len(after) > 0:
        rows.append({
            "分析": "ショー後90分",
            "内容": f"5大平均待ち時間は約{after['avg_major_wait'].mean():.1f}分です。",
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame([{"分析": "蓄積中", "内容": "ショー前後の比較対象がまだ不足しています。"}])

def auto_collect_prediction_context(cursor, conn, park):
    logs = load_data_fetch_logs(conn)
    today = datetime.now(JST).date()
    results = []

    if should_fetch_data_today(logs, park, "park_hours", today):
        hours_df, message = fetch_official_park_hours()
        saved_count = save_park_hours_rows(cursor, conn, park, hours_df)
        log_data_fetch(
            cursor,
            conn,
            park,
            "park_hours",
            "success" if saved_count > 0 else "empty",
            message,
            saved_count,
            today
        )
        results.append(f"営業時間を{saved_count}件保存")
    else:
        results.append("営業時間は確認済み")

    if should_fetch_data_today(logs, park, "event_signals", today):
        event_df = build_event_signal_rows(park)
        saved_count = save_event_signal_rows(cursor, conn, park, event_df)
        log_data_fetch(
            cursor,
            conn,
            park,
            "event_signals",
            "success" if saved_count > 0 else "empty",
            "イベント/休暇シグナル",
            saved_count,
            today
        )
        results.append(f"イベント/休暇シグナルを{saved_count}件保存")
    else:
        results.append("イベント/休暇シグナルは確認済み")

    existing_show_df = load_show_schedules(conn)
    has_today_shows = False
    if len(existing_show_df) > 0 and "target_date" in existing_show_df.columns and "park" in existing_show_df.columns:
        has_today_shows = len(existing_show_df[
            (existing_show_df["target_date"] == str(today))
            & (existing_show_df["park"] == park)
        ]) > 0

    should_fetch_shows = should_fetch_data_today(logs, park, "show_schedules", today) or not has_today_shows

    if should_fetch_shows:
        show_settings = dict(PARK_SETTINGS.get(park, {}))
        show_settings["park"] = park
        show_df, show_message = fetch_official_show_schedule(show_settings, today)
        saved_count = save_show_schedule_rows(cursor, conn, park, show_df, today)
        log_data_fetch(
            cursor,
            conn,
            park,
            "show_schedules",
            "success" if saved_count > 0 else "empty",
            show_message,
            saved_count,
            today
        )
        results.append(f"ショー/パレード時刻を{saved_count}件保存")
    else:
        results.append("ショー/パレード時刻は確認済み")

    return results

def auto_save_context_data(cursor, conn, park, ticket_price_map, ticket_source, temperature, rain_mm, weather_text):
    logs = load_data_fetch_logs(conn)
    today = datetime.now(JST).date()
    results = []

    if should_fetch_data_today(logs, park, "weather", today):
        save_weather_snapshot(
            cursor,
            conn,
            park,
            temperature,
            rain_mm,
            weather_text
        )
        log_data_fetch(
            cursor,
            conn,
            park,
            "weather",
            "success",
            "weather snapshot saved",
            1,
            today
        )
        results.append("天気を保存")
    else:
        results.append("天気は確認済み")

    if should_fetch_data_today(logs, park, "ticket_price", today):
        saved_count = save_ticket_price_snapshots(
            cursor,
            conn,
            park,
            ticket_price_map,
            ticket_source
        )
        status = "success" if saved_count > 0 else "empty"
        message = f"チケット価格を{saved_count}件保存"
        log_data_fetch(
            cursor,
            conn,
            park,
            "ticket_price",
            status,
            message,
            saved_count,
            today
        )
        results.append(message)
    else:
        results.append("チケット価格は確認済み")

    return results


def load_weather_snapshots(conn):
    try:
        df = pd.read_sql_query("SELECT * FROM weather_snapshots", conn)
    except Exception:
        return pd.DataFrame()

    if len(df) > 0:
        df["observed_at"] = pd.to_datetime(df["observed_at"])
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    return df


def load_ticket_price_snapshots(conn):
    try:
        df = pd.read_sql_query("SELECT * FROM ticket_price_snapshots", conn)
    except Exception:
        return pd.DataFrame()

    if len(df) > 0:
        df["observed_at"] = pd.to_datetime(df["observed_at"])
        df["target_date"] = pd.to_datetime(df["target_date"]).dt.date

    return df


def get_next_feature_plan():
    rows = [
        {
            "優先度": "高",
            "追加するデータ/機能": "DPA売切れ実績",
            "自動取得": "urtripから1日1回取得。失敗理由も保存。",
            "予測への効果": "DPA売切れ時刻を現在待ち時間だけでなく過去実績から補正できる。"
        },
        {
            "優先度": "高",
            "追加するデータ/機能": "チケット価格履歴",
            "自動取得": "公式/補助サイトから取得した価格表をDB保存。",
            "予測への効果": "高価格日を需要が強い日として1週間予測に反映しやすくなる。"
        },
        {
            "優先度": "高",
            "追加するデータ/機能": "天気スナップショット",
            "自動取得": "Open-Meteoの現在天気を起動時に保存。",
            "予測への効果": "雨・気温と待ち時間の関係を後から学習できる。"
        },
        {
            "優先度": "中",
            "追加するデータ/機能": "パーク営業時間/短縮営業",
            "自動取得": "公式ページから取得できる場合に追加。",
            "予測への効果": "夜間や短縮営業日の誤差を減らせる。"
        },
        {
            "優先度": "中",
            "追加するデータ/機能": "イベント/新エリア/休止情報",
            "自動取得": "公式ニュースや休止施設ページから取得できる場合に追加。",
            "予測への効果": "通常の曜日パターンでは説明できない混雑を補正できる。"
        },
        {
            "優先度": "中",
            "追加するデータ/機能": "アトラクション休止/一時停止",
            "自動取得": "待ち時間APIのOpen状態を履歴化。",
            "予測への効果": "0分や休止を誤差学習に混ぜない判断が強くなる。"
        },
        {
            "優先度": "低",
            "追加するデータ/機能": "学校休み/大型連休カレンダー",
            "自動取得": "まずはルール推定。公開データがあれば追加。",
            "予測への効果": "春休み・夏休み・入試休みなどの上振れを拾える。"
        },
    ]
    return pd.DataFrame(rows)


def should_auto_fetch_dpa(fetch_logs, park, source="urtrip", target_date=None):
    target_date = target_date or datetime.now(JST).date()

    if len(fetch_logs) == 0:
        return True

    if "target_date" not in fetch_logs.columns:
        return True

    today_logs = fetch_logs[
        (fetch_logs["target_date"] == target_date)
        & (fetch_logs.get("park", "") == park)
        & (fetch_logs.get("source", "") == source)
    ]

    return len(today_logs) == 0


def auto_fetch_dpa_if_needed(cursor, conn, settings, park):
    today = datetime.now(JST).date()
    logs = load_dpa_fetch_logs(conn)

    if not should_auto_fetch_dpa(logs, park, "urtrip", today):
        latest = logs[
            (logs["target_date"] == today)
            & (logs.get("park", "") == park)
            & (logs.get("source", "") == "urtrip")
        ].sort_values("fetched_at", ascending=False).iloc[0]

        return {
            "status": "skipped",
            "message": f"today already checked: {latest.get('message', '')}",
            "saved_count": int(latest.get("saved_count", 0) or 0),
        }

    scraped_df, message = fetch_urtrip_dpa_sellouts(settings)

    if len(scraped_df) == 0:
        log_dpa_fetch(
            cursor,
            conn,
            park,
            "urtrip",
            "failed",
            message,
            0,
            today
        )
        return {
            "status": "failed",
            "message": message,
            "saved_count": 0,
        }

    saved_count = save_dpa_sellout_rows(
        cursor,
        conn,
        scraped_df,
        "urtrip"
    )
    log_dpa_fetch(
        cursor,
        conn,
        park,
        "urtrip",
        "success" if saved_count > 0 else "empty",
        message,
        saved_count,
        today
    )

    return {
        "status": "success" if saved_count > 0 else "empty",
        "message": message,
        "saved_count": saved_count,
    }


def save_dpa_sellout(cursor, conn, attraction, sellout_hour, source="manual", target_date=None):
    now = datetime.now(JST)
    target_date = target_date or now.date()
    target_date_text = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)

    cursor.execute("""
    DELETE FROM dpa_sellouts
    WHERE target_date = ?
    AND attraction = ?
    AND source = ?
    """, (
        target_date_text,
        attraction,
        source
    ))

    cursor.execute("""
    INSERT INTO dpa_sellouts
    (
        observed_at,
        target_date,
        attraction,
        sellout_hour,
        source
    )
    VALUES (?, ?, ?, ?, ?)
    """, (
        now.strftime("%Y-%m-%d %H:%M:%S"),
        target_date_text,
        attraction,
        float(sellout_hour),
        source
    ))
    conn.commit()


def clear_dpa_sellouts(cursor, conn, source=None):
    if source:
        cursor.execute(
            "DELETE FROM dpa_sellouts WHERE source = ?",
            (source,)
        )
    else:
        cursor.execute("DELETE FROM dpa_sellouts")

    conn.commit()


def _time_text_to_hour(time_text):
    match = re.search(r"(\d{1,2}):(\d{2})", str(time_text).strip())

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None

    return hour + minute / 60


def _parse_urtrip_date(date_text, page_date):
    match = re.match(r"(\d{1,2})/(\d{1,2})", date_text)

    if not match:
        return None

    month = int(match.group(1))
    day = int(match.group(2))
    year = page_date.year

    if month > page_date.month + 1:
        year -= 1

    try:
        return date(year, month, day)
    except ValueError:
        return None


def fetch_urtrip_dpa_sellouts(settings):
    url = settings.get("urtrip_url")

    if not url:
        return pd.DataFrame(), "DPA取得URL未設定"

    try:
        res = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12
        )
        res.raise_for_status()
    except Exception as exc:
        return pd.DataFrame(), f"urtrip取得失敗: {exc}"

    plain = re.sub(r"<[^>]+>", "\n", res.text)
    plain = unescape(plain)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]

    page_date = datetime.now(JST).date()

    for line in lines[:160]:
        match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", line)

        if match:
            page_date = date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3))
            )
            break

    dpa_start = None

    for i, line in enumerate(lines):
        if "ディズニー・プレミアアクセス" in line and "DPA" in line:
            dpa_start = i
            break

    if dpa_start is None:
        return pd.DataFrame(), "urtripにDPA欄が見つかりません"

    dpa_end = len(lines)

    for i in range(dpa_start + 1, len(lines)):
        if "40周年記念" in lines[i] or "アトラクション待ち時間" in lines[i]:
            dpa_end = i
            break

    section = lines[dpa_start:dpa_end]
    dpa_order = settings.get("dpa_order", [])
    rows = []
    status_rows = []

    for i, line in enumerate(section):
        if line == "今日":
            tokens = section[i + 1:]
            current_values = []
            skip_next = False

            for token_index, token in enumerate(tokens):
                if skip_next:
                    skip_next = False
                    continue

                if re.match(r"\d{1,2}/\d{1,2}", token):
                    break

                if _time_text_to_hour(token) is not None:
                    current_values.append(token)

                    if token_index + 1 < len(tokens) and tokens[token_index + 1] == "発行終了":
                        skip_next = True

                elif token in ["発行中", "発行なし"]:
                    current_values.append(token)

            for attraction, value in zip(dpa_order, current_values):
                sellout_hour = _time_text_to_hour(value)
                status = "発行終了" if sellout_hour is not None else value
                status_rows.append({
                    "target_date": page_date,
                    "attraction": attraction,
                    "sellout_hour": sellout_hour,
                    "status": status,
                    "source": url
                })

                if sellout_hour is not None:
                    rows.append(status_rows[-1])

            break

    for i, line in enumerate(section):
        if not re.match(r"\d{1,2}/\d{1,2}", line):
            continue

        target_day = _parse_urtrip_date(line, page_date)

        if target_day is None:
            continue

        values = []

        for token in section[i + 1:]:
            if re.match(r"\d{1,2}/\d{1,2}", token):
                break

            if "40周年記念" in token or "アトラクション待ち時間" in token:
                break

            values.append(token)

            if len(values) >= len(dpa_order):
                break

        for attraction, value in zip(dpa_order, values):
            sellout_hour = _time_text_to_hour(value)

            if sellout_hour is None:
                continue

            rows.append({
                "target_date": target_day,
                "attraction": attraction,
                "sellout_hour": sellout_hour,
                "status": "発行終了",
                "source": url
            })

    df = pd.DataFrame(rows)

    if len(df) == 0:
        return pd.DataFrame(status_rows), "urtripからDPA売切れ時刻は取得できませんでした"

    df = df.drop_duplicates(
        ["target_date", "attraction", "sellout_hour"],
        keep="first"
    ).reset_index(drop=True)

    return df, f"urtripから{len(df)}件取得"


def save_dpa_sellout_rows(cursor, conn, sellout_df, source="urtrip"):
    if len(sellout_df) == 0:
        return 0

    saved_count = 0

    for _, row in sellout_df.iterrows():
        if pd.isna(row.get("sellout_hour")):
            continue

        target_date_value = row["target_date"]

        if isinstance(target_date_value, pd.Timestamp):
            target_date_value = target_date_value.date()

        save_dpa_sellout(
            cursor,
            conn,
            row["attraction"],
            row["sellout_hour"],
            source,
            target_date_value
        )
        saved_count += 1

    return saved_count


def _weighted_average(values):
    clean_values = [(float(v), float(w)) for v, w in values if v is not None and not pd.isna(v) and w > 0]

    if not clean_values:
        return 0

    total_weight = sum(w for _, w in clean_values)
    return sum(v * w for v, w in clean_values) / total_weight


def build_time_slots(start_hour=9, end_hour=21, step_minutes=15):
    rows = []
    current = datetime.combine(date.today(), datetime.min.time()).replace(hour=start_hour)
    end = datetime.combine(date.today(), datetime.min.time()).replace(hour=end_hour)
    while current < end:
        rows.append({
            "Time": current.hour + current.minute / 60,
            "TimeLabel": current.strftime("%H:%M"),
            "Hour": current.hour,
            "Minute": current.minute,
        })
        current += timedelta(minutes=step_minutes)
    return rows


def _prediction_attractions(history_df, settings, current_target_df=None):
    names = []
    if history_df is not None and len(history_df) > 0 and "attraction" in history_df.columns:
        names.extend(history_df["attraction"].dropna().astype(str).unique().tolist())
    if current_target_df is not None and len(current_target_df) > 0 and "Attraction" in current_target_df.columns:
        names.extend(current_target_df["Attraction"].dropna().astype(str).unique().tolist())
    names.extend(settings.get("rides", []))
    unique_names = []
    for name in names:
        if name not in unique_names:
            unique_names.append(name)
    return unique_names


def _prepare_model_history(history_df, rides=None):
    if len(history_df) == 0:
        return history_df

    model_df = history_df[history_df["wait_time"] > 0].copy()
    if rides:
        model_df = model_df[model_df["attraction"].isin(rides)].copy()
    model_df = filter_crowd_history(model_df)

    if len(model_df) > 0:
        model_df["weekday"] = model_df["datetime"].dt.weekday
        model_df["month"] = model_df["datetime"].dt.month
        model_df["minute"] = model_df["datetime"].dt.minute
        model_df["minute_slot"] = (model_df["minute"] // 15) * 15
        model_df["time_slot"] = model_df["hour"] + model_df["minute_slot"] / 60

    return model_df


def predict_wait_times_for_date(
    history_df,
    settings,
    target_date,
    temperature,
    rain_mm,
    prediction_history,
    ticket_price=None,
    current_target_df=None,
):
    target_date = target_date.date() if isinstance(target_date, datetime) else target_date
    attractions = _prediction_attractions(history_df, settings, current_target_df)
    model_df = _prepare_model_history(history_df, attractions)
    today = datetime.now(JST).date()
    now = datetime.now(JST)
    target_bonus, target_reasons = get_calendar_bonus(target_date, ticket_price)
    rows = []

    if len(model_df) > 0:
        time_all = model_df.groupby("time_slot")["wait_time"].median()
        hour_all = model_df.groupby("hour")["wait_time"].median()
        attraction_all = model_df.groupby("attraction")["wait_time"].median()
        attraction_time = model_df.groupby(["attraction", "time_slot"])["wait_time"].median()
        attraction_hour = model_df.groupby(["attraction", "hour"])["wait_time"].median()
        same_weekday = model_df[model_df["weekday"] == target_date.weekday()]
        same_month = model_df[model_df["month"] == target_date.month]
        overall = model_df["wait_time"].median()
    else:
        time_all = pd.Series(dtype=float)
        hour_all = pd.Series(dtype=float)
        attraction_all = pd.Series(dtype=float)
        attraction_time = pd.Series(dtype=float)
        attraction_hour = pd.Series(dtype=float)
        same_weekday = pd.DataFrame()
        same_month = pd.DataFrame()
        overall = 45

    current_map = {}
    if current_target_df is not None and len(current_target_df) > 0:
        current_map = dict(zip(current_target_df["Attraction"], current_target_df["Wait"]))

    slots = build_time_slots(OPEN_HOUR, CROWD_END_HOUR, 15)
    for attraction in attractions:
        attraction_feedback = get_feedback_error(prediction_history, attraction)
        ride_base = attraction_all.get(attraction, None) if len(attraction_all) > 0 else None
        raw_predictions = []

        for slot in slots:
            hour = int(slot["Hour"])
            minute = int(slot["Minute"])
            time_value = float(slot["Time"])
            specific = attraction_time.get((attraction, time_value), None) if len(attraction_time) > 0 else None
            hour_specific = attraction_hour.get((attraction, hour), None) if len(attraction_hour) > 0 else None
            hour_base = hour_all.get(hour, None) if len(hour_all) > 0 else None
            time_base = time_all.get(time_value, None) if len(time_all) > 0 else None

            weekday_value = None
            if len(same_weekday) > 0:
                rows_weekday = same_weekday[
                    (same_weekday["attraction"] == attraction)
                    & (same_weekday["time_slot"] == time_value)
                ]
                if len(rows_weekday) > 0:
                    weekday_value = rows_weekday["wait_time"].median()

            month_value = None
            if len(same_month) > 0:
                rows_month = same_month[
                    (same_month["attraction"] == attraction)
                    & (same_month["hour"] == hour)
                ]
                if len(rows_month) > 0:
                    month_value = rows_month["wait_time"].median()

            profile_value = None
            if ride_base is not None and not pd.isna(ride_base):
                profile_value = ride_base * HOUR_PROFILE.get(hour, 1.0)

            base_wait = _weighted_average([
                (specific, 0.30),
                (hour_specific, 0.20),
                (weekday_value, 0.16),
                (month_value, 0.10),
                (profile_value, 0.12),
                (ride_base, 0.07),
                (time_base, 0.03),
                (hour_base, 0.02),
                (overall, 0.02),
            ])

            current_reason = ""
            if target_date == today and attraction in current_map:
                slot_minutes = hour * 60 + minute
                now_minutes = now.hour * 60 + (now.minute // 15) * 15
                distance_slots = abs(slot_minutes - now_minutes) / 15
                current_weight = (0.08 if now.hour < 11 else 0.16 if now.hour < 13 else 0.26) / (distance_slots + 1)
                if slot_minutes < now_minutes:
                    current_weight *= 0.35
                base_wait = _weighted_average([
                    (base_wait, 1 - current_weight),
                    (current_map[attraction], current_weight),
                ])
                current_reason = "????????????"

            adjustment = target_bonus * 2.5
            if rain_mm > 0:
                adjustment -= min(8, rain_mm * 2)
            if temperature >= 30:
                adjustment -= 4
            elif temperature <= 5:
                adjustment -= 3

            raw_predictions.append(max(0, base_wait + adjustment + attraction_feedback))

        if raw_predictions:
            smooth = pd.Series(raw_predictions).rolling(window=3, center=True, min_periods=1).mean().tolist()
        else:
            smooth = []

        for slot, predicted in zip(slots, smooth):
            rows.append({
                "Time": slot["Time"],
                "TimeLabel": slot["TimeLabel"],
                "Hour": int(slot["Hour"]),
                "Minute": int(slot["Minute"]),
                "Attraction": attraction,
                "Predicted Wait": round(float(predicted), 1),
                "??": " / ".join([
                    "15????",
                    "??????",
                    "??????",
                    "??????",
                    current_reason,
                ] + target_reasons).strip(" / ")
            })

    return pd.DataFrame(rows)

def make_major_average_prediction(wait_prediction_df, major_rides=None):
    columns = ["Time", "TimeLabel", "Hour", "Minute", "Predicted Wait"]
    if wait_prediction_df is None or len(wait_prediction_df) == 0:
        return pd.DataFrame(columns=columns)

    df = wait_prediction_df.copy()
    if major_rides is not None and "Attraction" in df.columns:
        df = df[df["Attraction"].isin(major_rides)].copy()
    if len(df) == 0:
        return pd.DataFrame(columns=columns)

    group_cols = [col for col in ["Time", "TimeLabel", "Hour", "Minute"] if col in df.columns]
    if not group_cols:
        group_cols = ["Hour"]
    avg_df = df.groupby(group_cols)["Predicted Wait"].mean().reset_index()
    return avg_df.sort_values(group_cols).rename(columns={"Predicted Wait": "Predicted Wait"})


def get_all_attraction_crowd_stats(valid_all_df=None, history_df=None, target_date=None):
    if target_date is None:
        target_date = datetime.now(JST).date()
    target_date = target_date.date() if isinstance(target_date, datetime) else target_date

    waits = pd.Series(dtype=float)
    open_count = 0
    closed_count = 0
    source = "???????"

    if history_df is not None and len(history_df) > 0:
        hist = filter_crowd_history(history_df.copy())
        if "date" not in hist.columns and "datetime" in hist.columns:
            hist["date"] = pd.to_datetime(hist["datetime"]).dt.date
        day_df = hist[(hist.get("date") == target_date) & (hist["wait_time"] > 0)].copy() if "date" in hist.columns else pd.DataFrame()
        if len(day_df) > 0:
            waits = day_df["wait_time"].astype(float)
            open_count = int(day_df["attraction"].nunique()) if "attraction" in day_df.columns else 0
            source = "??9:00?20:59???????????"

    if len(waits) == 0 and valid_all_df is not None and len(valid_all_df) > 0:
        df = valid_all_df.copy()
        open_df = df[(df.get("Open", True) == True) & (df["Wait"] > 0)].copy()
        waits = open_df["Wait"].astype(float) if len(open_df) > 0 else pd.Series(dtype=float)
        open_count = int(len(open_df))
        closed_count = int(len(df) - len(open_df))
        source = "?????????????????"

    if len(waits) == 0:
        return {
            "avg_wait": 0.0,
            "max_wait": 0.0,
            "top_quartile_wait": 0.0,
            "std_wait": 0.0,
            "open_count": open_count,
            "closed_count": closed_count,
            "sample_count": 0,
            "source": source,
        }

    top_count = max(1, int(np.ceil(len(waits) * 0.25)))
    top_quartile = waits.sort_values(ascending=False).head(top_count).mean()
    std_wait = waits.std()
    if pd.isna(std_wait):
        std_wait = 0

    return {
        "avg_wait": float(waits.mean()),
        "max_wait": float(waits.max()),
        "top_quartile_wait": float(top_quartile),
        "std_wait": float(std_wait),
        "open_count": open_count,
        "closed_count": closed_count,
        "sample_count": int(len(waits)),
        "source": source,
    }


def get_prediction_crowd_stats(wait_prediction_df):
    if wait_prediction_df is None or len(wait_prediction_df) == 0 or "Predicted Wait" not in wait_prediction_df.columns:
        return {
            "avg_wait": 0.0,
            "max_wait": 0.0,
            "top_quartile_wait": 0.0,
            "std_wait": 0.0,
            "open_count": 0,
            "closed_count": 0,
            "sample_count": 0,
        }
    df = wait_prediction_df.copy()
    waits = df["Predicted Wait"].astype(float)
    by_attraction = df.groupby("Attraction")["Predicted Wait"].mean() if "Attraction" in df.columns else waits
    top_count = max(1, int(np.ceil(len(by_attraction) * 0.25)))
    top_quartile = by_attraction.sort_values(ascending=False).head(top_count).mean()
    std_wait = waits.std()
    if pd.isna(std_wait):
        std_wait = 0
    return {
        "avg_wait": float(waits.mean()),
        "max_wait": float(waits.max()),
        "top_quartile_wait": float(top_quartile),
        "std_wait": float(std_wait),
        "open_count": int(df["Attraction"].nunique()) if "Attraction" in df.columns else 0,
        "closed_count": 0,
        "sample_count": int(len(waits)),
    }

def predict_crowd_index_for_date(
    history_df,
    settings,
    target_date,
    temperature,
    rain_mm,
    prediction_history,
    daily_prediction_history,
    ticket_price=None,
    current_target_df=None,
    event_signals=None,
    park_hours_df=None,
    park=None,
    daily_weather=None,
):
    wait_df = predict_wait_times_for_date(
        history_df,
        settings,
        target_date,
        temperature,
        rain_mm,
        prediction_history,
        ticket_price,
        current_target_df,
    )

    stats = get_prediction_crowd_stats(wait_df)
    target_bonus, reasons = get_calendar_bonus(target_date, ticket_price)
    event_bonus, event_reasons = get_event_bonus(event_signals if event_signals is not None else pd.DataFrame(), target_date, park)
    hours_bonus, hours_reasons = get_park_hours_bonus(park_hours_df if park_hours_df is not None else pd.DataFrame(), target_date)
    target_bonus += event_bonus + hours_bonus
    reasons.extend(event_reasons)
    reasons.extend(hours_reasons)
    weather_score = get_weather_score("?" if rain_mm > 0 else "??", rain_mm, temperature)
    feedback_error = get_feedback_error(prediction_history, GLOBAL_PREDICTION_NAME)
    daily_feedback = get_daily_crowd_feedback_error(daily_prediction_history)

    crowd_index = get_crowd_index_for_park(
        park or settings.get("park", "DisneySea"),
        stats["avg_wait"],
        stats["max_wait"],
        stats["top_quartile_wait"],
        stats["std_wait"],
        stats["open_count"],
        stats["closed_count"],
        weather_score,
        feedback_error + daily_feedback,
        target_bonus,
    )

    major_df = wait_df[wait_df["Attraction"].isin(settings.get("rides", []))].copy() if len(wait_df) > 0 else pd.DataFrame()
    major_avg = float(major_df["Predicted Wait"].mean()) if len(major_df) > 0 else 0

    reasons = reasons + [
        f"?????? {stats['avg_wait']:.1f}?",
        f"??25%?? {stats['top_quartile_wait']:.1f}?",
        f"5????? {major_avg:.1f}?",
        f"?????? {daily_feedback:+.1f}",
    ]

    return crowd_index, wait_df, reasons

def make_week_forecast(
    history_df,
    settings,
    start_date,
    temperature,
    rain_mm,
    prediction_history,
    daily_prediction_history,
    ticket_price_map,
    current_target_df=None,
    event_signals=None,
    park_hours_df=None,
    park=None,
    daily_weather=None,
    cursor=None,
    conn=None,
    use_live_current_data=False,
):
    rows = []

    for i in range(7):
        d = start_date + timedelta(days=i)
        locked_value = get_locked_daily_prediction(conn, d) if conn is not None else None
        ticket_price, ticket_source = get_ticket_price_from_castel(d, ticket_price_map)
        forecast_temperature, forecast_rain, weather_source = get_forecast_weather_for_date(
            daily_weather,
            d,
            temperature,
            rain_mm
        )
        live_df = current_target_df if use_live_current_data and d == datetime.now(JST).date() else None

        if locked_value is not None:
            crowd_index = locked_value
            wait_df = predict_wait_times_for_date(
                history_df,
                settings,
                d,
                forecast_temperature,
                forecast_rain,
                prediction_history,
                ticket_price,
                None,
            )
            reasons = ["保存済みの日別固定予測を使用"]
            prediction_source = "保存済み"
        else:
            crowd_index, wait_df, reasons = predict_crowd_index_for_date(
                history_df,
                settings,
                d,
                forecast_temperature,
                forecast_rain,
                prediction_history,
                daily_prediction_history,
                ticket_price,
                live_df,
                event_signals,
                park_hours_df,
                park,
            )
            save_locked_daily_prediction(cursor, conn, d, crowd_index)
            prediction_source = "新規固定保存" if conn is not None else "一時予測"

        rows.append({
            "Date": d.strftime("%m/%d"),
            "Crowd Index": round(float(crowd_index), 1),
            "予測種別": prediction_source,
            "予報気温": round(forecast_temperature, 1),
            "予報降水量": round(forecast_rain, 1),
            "天気取得元": weather_source,
            "全体平均待ち時間": round(get_prediction_crowd_stats(wait_df)["avg_wait"], 1),
            "上位25%平均待ち時間": round(get_prediction_crowd_stats(wait_df)["top_quartile_wait"], 1),
            "5大平均待ち時間": round(
                wait_df[wait_df["Attraction"].isin(settings.get("rides", []))]["Predicted Wait"].mean(), 1
            ) if len(wait_df) > 0 and "Attraction" in wait_df.columns else 0,
            "チケット価格": "未取得" if ticket_price is None else ticket_price,
            "主な理由": " / ".join(reasons[:4]),
            "価格取得元": ticket_source,
        })

    return pd.DataFrame(rows)


def make_locked_week_forecast(*args, **kwargs):
    kwargs["use_live_current_data"] = False
    return make_week_forecast(*args, **kwargs)



def _time_to_minutes(value):
    if isinstance(value, str):
        h, m = value.split(":")[:2]
        return int(h) * 60 + int(m)
    if isinstance(value, (int, float)):
        return int(float(value) * 60)
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    return OPEN_HOUR * 60


def _minutes_to_label(minutes):
    minutes = int(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_predicted_wait_at_time(wait_pred_df, attraction, current_time):
    if wait_pred_df is None or len(wait_pred_df) == 0:
        return None
    if "Attraction" not in wait_pred_df.columns or "Predicted Wait" not in wait_pred_df.columns:
        return None
    df = wait_pred_df[wait_pred_df["Attraction"] == attraction].copy()
    if len(df) == 0:
        return None
    target_minutes = _time_to_minutes(current_time)
    if "Time" in df.columns:
        df["_minutes"] = (df["Time"].astype(float) * 60).round().astype(int)
    else:
        df["_minutes"] = df["Hour"].astype(int) * 60 + df.get("Minute", 0).astype(int)
    df["_distance"] = (df["_minutes"] - target_minutes).abs()
    row = df.sort_values("_distance").iloc[0]
    return float(row["Predicted Wait"])


def _simulate_route_order(wait_pred_df, order, start_minutes, end_minutes):
    rows = []
    current = int(start_minutes)
    total_wait = 0.0
    warnings = []
    for idx, attraction in enumerate(order, start=1):
        if current >= end_minutes:
            warnings.append(f"{attraction} ???????????????")
            break
        wait = get_predicted_wait_at_time(wait_pred_df, attraction, _minutes_to_label(current))
        if wait is None:
            warnings.append(f"{attraction} ????????")
            continue
        rows.append({
            "??": idx,
            "????": _minutes_to_label(current),
            "Attraction": attraction,
            "??????": round(wait, 1),
            "?????????": round(wait + 20, 1),
        })
        total_wait += wait
        current += int(round(wait + 20))
    return rows, total_wait, current, warnings


def build_optimal_route_plan(wait_pred_df, selected_attractions, start_time, end_time):
    selected = [a for a in selected_attractions if a]
    start_minutes = _time_to_minutes(start_time)
    end_minutes = _time_to_minutes(end_time)
    if not selected:
        return pd.DataFrame(), {
            "total_wait": 0,
            "end_time": _minutes_to_label(start_minutes),
            "message": "?????????????????????",
            "warnings": [],
        }
    if end_minutes <= start_minutes:
        return pd.DataFrame(), {
            "total_wait": 0,
            "end_time": _minutes_to_label(start_minutes),
            "message": "????????????????????",
            "warnings": [],
        }

    if len(selected) <= 7:
        from itertools import permutations
        candidates = permutations(selected)
    else:
        remaining = selected[:]
        order = []
        current = start_minutes
        while remaining:
            waits = [(get_predicted_wait_at_time(wait_pred_df, a, _minutes_to_label(current)) or 999, a) for a in remaining]
            _, chosen = sorted(waits)[0]
            order.append(chosen)
            wait = get_predicted_wait_at_time(wait_pred_df, chosen, _minutes_to_label(current)) or 0
            current += int(round(wait + 20))
            remaining.remove(chosen)
        candidates = [order]

    best = None
    for order in candidates:
        rows, total_wait, finish, warnings = _simulate_route_order(wait_pred_df, order, start_minutes, end_minutes)
        score = total_wait + max(0, finish - end_minutes) * 3 + len(warnings) * 50
        if best is None or score < best[0]:
            best = (score, rows, total_wait, finish, warnings, list(order))

    _, rows, total_wait, finish, warnings, order = best
    route_df = pd.DataFrame(rows)
    peak_avoided = "????????????????????????" if len(route_df) > 0 else "???????????????"
    meta = {
        "total_wait": round(total_wait, 1),
        "end_time": _minutes_to_label(finish),
        "message": peak_avoided,
        "warnings": warnings,
        "order": order,
    }
    return route_df, meta


def format_route_plan_cards(route_df):
    if route_df is None or len(route_df) == 0:
        return "<div class='ios-card'>???????????????</div>"
    html = ["<div class='ios-list-card'>"]
    for _, row in route_df.iterrows():
        html.append(
            "<div class='ios-list-row'>"
            f"<div><div class='ios-list-title'>{int(row['??'])}. {row['????']} {row['Attraction']}</div>"
            f"<div class='ios-list-detail'>??????? ?{row['?????????']:.0f}?</div></div>"
            f"<div class='ios-list-value'>{row['??????']:.0f}?</div>"
            "</div>"
        )
    html.append("</div>")
    return "".join(html)

def predict_dpa_sellout_time(attraction, wait, crowd_10, ticket_price, bonus, dpa_sellout_history):
    historical_hour = None
    sample_count = 0
    early_ratio = 0

    if len(dpa_sellout_history) > 0:
        one_history = dpa_sellout_history[
            dpa_sellout_history["attraction"] == attraction
        ]
        if len(one_history) > 0:
            recent_history = one_history.sort_values("target_date").tail(14)
            sample_count = len(recent_history)
            early_ratio = float((recent_history["sellout_hour"] < 18).mean())
            historical_hour = float(recent_history["sellout_hour"].median())

    risk_text, risk_score = predict_dpa_risk(wait, crowd_10, ticket_price, bonus)

    if historical_hour is not None:
        predicted_hour = historical_hour - max(0, risk_score - 3) * 0.35
        reason = f"過去{sample_count}件の中央値を基準に、現在の待ち時間と混雑指数で補正"
    else:
        predicted_hour = 20.5 - risk_score * 0.85
        reason = "売切れ履歴がないため、待ち時間・混雑指数・需要補正から推定"

    predicted_hour = max(9.5, min(20.5, predicted_hour))
    hour = int(predicted_hour)
    minute = int(round((predicted_hour - hour) * 60 / 5) * 5)

    if minute >= 60:
        hour += 1
        minute = 0

    return {
        "Attraction": attraction,
        "Wait": wait,
        "Risk": risk_text,
        "Risk Score": risk_score,
        "予測売切れ時刻": f"{hour:02d}:{minute:02d}",
        "根拠": reason,
        "履歴件数": sample_count,
        "18時前売切れ率": round(early_ratio, 2),
    }


def get_data_quality_report(history_df, prediction_history, dpa_sellout_history, settings):
    rows = []

    target_history = pd.DataFrame()

    if len(history_df) > 0:
        target_history = history_df[
            history_df["attraction"].isin(settings["rides"])
        ].copy()
        target_history = filter_crowd_history(target_history)

    if len(target_history) == 0:
        rows.append({
            "項目": "5大アトラクション履歴",
            "状態": "不足",
            "対応": "9:00〜20:59にアプリを起動し、待ち時間を蓄積してください。"
        })
    else:
        days = target_history["date"].nunique()
        rides = target_history["attraction"].nunique()
        rows.append({
            "項目": "5大アトラクション履歴",
            "状態": f"{days}日 / {rides}施設",
            "対応": "曜日差を学習するには複数週の履歴があると安定します。"
        })

    error_count = 0

    if len(prediction_history) > 0 and "error" in prediction_history.columns:
        error_count = len(prediction_history[prediction_history["error"].notna()])

    rows.append({
        "項目": "待ち時間予測誤差",
        "状態": f"{error_count}件",
        "対応": "少ない場合は予測補正が弱くなります。対象時刻にアプリが動いているほど増えます。"
    })

    dpa_count = len(dpa_sellout_history)
    dpa_sources = ""

    if dpa_count > 0 and "source" in dpa_sellout_history.columns:
        dpa_sources = " / ".join(sorted(dpa_sellout_history["source"].dropna().unique()))

    rows.append({
        "項目": "DPA売切れ履歴",
        "状態": f"{dpa_count}件" + (f" ({dpa_sources})" if dpa_sources else ""),
        "対応": "urtrip取得または手入力で増えるほど、時刻予測が現在値だけに依存しなくなります。"
    })

    return pd.DataFrame(rows)


def get_prediction_confidence(
    history_df,
    prediction_history,
    dpa_sellout_history,
    settings,
    target_date=None,
    weather_source="",
):
    target_date = target_date or datetime.now(JST).date()
    target_history = pd.DataFrame()

    if len(history_df) > 0:
        target_history = history_df[
            history_df["attraction"].isin(settings["rides"])
        ].copy()
        target_history = filter_crowd_history(target_history)

    history_days = target_history["date"].nunique() if len(target_history) > 0 and "date" in target_history.columns else 0
    ride_coverage = target_history["attraction"].nunique() if len(target_history) > 0 else 0

    error_count = 0
    recent_error = None

    if len(prediction_history) > 0 and "error" in prediction_history.columns:
        error_df = prediction_history[prediction_history["error"].notna()].copy()
        error_count = len(error_df)

        if len(error_df) > 0:
            recent_error = float(error_df["error"].abs().tail(50).mean())

    dpa_count = len(dpa_sellout_history)
    weather_bonus = 10 if "日別" in str(weather_source) or "予報" in str(weather_source) else 4

    score = 0
    score += min(35, history_days * 4)
    score += min(20, ride_coverage * 4)
    score += min(25, error_count / 2)
    score += min(10, dpa_count / 5)
    score += weather_bonus

    if recent_error is not None:
        if recent_error <= 10:
            score += 10
        elif recent_error <= 20:
            score += 5
        elif recent_error >= 35:
            score -= 10

    score = int(max(0, min(100, round(score))))

    if score >= 75:
        label = "高い"
    elif score >= 50:
        label = "中くらい"
    else:
        label = "低い"

    notes = []

    if history_days < 7:
        notes.append("5大アトラクションの履歴日数が少ない")
    if ride_coverage < len(settings["rides"]):
        notes.append("5大アトラクションの一部で履歴が不足")
    if error_count < 20:
        notes.append("予測誤差の学習データが少ない")
    if dpa_count < 10:
        notes.append("DPA売切れ履歴が少ない")
    if not ("日別" in str(weather_source) or "予報" in str(weather_source)):
        notes.append("対象日の天気予報ではなく現在天気を使っている可能性")

    if not notes:
        notes.append("主要な補正データはそろっています")

    return {
        "score": score,
        "label": label,
        "history_days": int(history_days),
        "ride_coverage": int(ride_coverage),
        "error_count": int(error_count),
        "dpa_count": int(dpa_count),
        "recent_mae": None if recent_error is None else round(recent_error, 1),
        "notes": notes,
    }


def get_prediction_accuracy_report(prediction_history):
    if len(prediction_history) == 0 or "error" not in prediction_history.columns:
        return pd.DataFrame([{
            "対象": "全体",
            "誤差データ数": 0,
            "平均絶対誤差": None,
            "平均ズレ": None,
            "状態": "予測誤差データがまだありません",
        }])

    error_df = prediction_history[prediction_history["error"].notna()].copy()

    if len(error_df) == 0:
        return pd.DataFrame([{
            "対象": "全体",
            "誤差データ数": 0,
            "平均絶対誤差": None,
            "平均ズレ": None,
            "状態": "対象時刻の実測値がまだ入っていません",
        }])

    rows = []

    for attraction, group in error_df.groupby("attraction"):
        mae = float(group["error"].abs().mean())
        bias = float(group["error"].mean())

        if mae <= 10:
            status = "良好"
        elif mae <= 20:
            status = "注意"
        else:
            status = "要改善"

        rows.append({
            "対象": attraction,
            "誤差データ数": int(len(group)),
            "平均絶対誤差": round(mae, 1),
            "平均ズレ": round(bias, 1),
            "状態": status,
        })

    return pd.DataFrame(rows).sort_values(
        ["状態", "平均絶対誤差"],
        ascending=[True, False]
    )


def get_prediction_alerts(wait_prediction_df, confidence):
    alerts = []

    if confidence["score"] < 50:
        alerts.append({
            "注意点": "信頼度が低め",
            "内容": "履歴や誤差データが少ないため、指数よりも傾向として見てください。"
        })

    if len(wait_prediction_df) > 0:
        max_wait = float(wait_prediction_df["Predicted Wait"].max())
        avg_wait = float(wait_prediction_df["Predicted Wait"].mean())

        if max_wait >= 150:
            alerts.append({
                "注意点": "長時間待ち予測",
                "内容": f"最大{max_wait:.0f}分の予測があります。DPAや朝の優先取得を検討してください。"
            })

        if avg_wait >= 100:
            alerts.append({
                "注意点": "5大平均が高め",
                "内容": f"平均{avg_wait:.0f}分前後の予測です。午後に集中しすぎない計画がおすすめです。"
            })

    if not alerts:
        alerts.append({
            "注意点": "大きな警告なし",
            "内容": "現在のデータでは、特別に強い警告はありません。"
        })

    return pd.DataFrame(alerts)


def get_wait_trend(history_df, attraction, recent_count=5):
    if len(history_df) == 0 or "attraction" not in history_df.columns:
        return {"label": "→ 安定", "message": "履歴データ不足のため、増減傾向はまだ判定できません。", "strength": "normal", "delta": 0, "recent_count": 0}

    one_df = history_df[(history_df["attraction"] == attraction) & (history_df["wait_time"] > 0)].copy()
    one_df = filter_crowd_history(one_df).sort_values("datetime").tail(recent_count)

    if len(one_df) < 3:
        return {"label": "→ 安定", "message": "直近データが少ないため、増減傾向はまだ判定できません。", "strength": "normal", "delta": 0, "recent_count": len(one_df)}

    waits = one_df["wait_time"].astype(float).tolist()
    diffs = np.diff(waits)
    avg_delta = float(np.mean(diffs)) if len(diffs) > 0 else 0
    total_delta = float(waits[-1] - waits[0])

    if avg_delta >= 8 or total_delta >= 30:
        label, message, strength = "↗ 急上昇傾向", "今後待ち時間が大きく伸びる可能性があります。早めの利用を検討してください。", "strong-up"
    elif avg_delta >= 3 or total_delta >= 12:
        label, message, strength = "↗ 上昇傾向", "今後待ち時間が伸びる可能性があります。", "up"
    elif avg_delta <= -8 or total_delta <= -30:
        label, message, strength = "↘ 急下降傾向", "少し待つとさらに下がる可能性があります。", "strong-down"
    elif avg_delta <= -3 or total_delta <= -12:
        label, message, strength = "↘ 下降傾向", "少し待つと下がる可能性があります。", "down"
    else:
        label, message, strength = "→ 安定", "大きな変化は少なそうです。", "normal"

    return {"label": label, "message": message, "strength": strength, "delta": round(total_delta, 1), "recent_count": len(one_df)}


def get_emptying_candidates(history_df, current_df, settings, limit=5):
    if len(history_df) == 0 or len(current_df) == 0:
        return pd.DataFrame([{"候補": "履歴データ不足", "理由": "過去の時間帯平均、または現在待ち時間が不足しています。"}])

    model_df = filter_crowd_history(history_df.copy())
    model_df = model_df[model_df["wait_time"] > 0].copy()

    if len(model_df) == 0 or "hour" not in model_df.columns:
        return pd.DataFrame([{"候補": "履歴データ不足", "理由": "9:00〜20:59の有効な履歴が不足しています。"}])

    now_hour = datetime.now(JST).hour
    rows = []

    for _, current_row in current_df.iterrows():
        attraction = current_row["Attraction"]
        current_wait = float(current_row["Wait"])
        one_df = model_df[model_df["attraction"] == attraction]

        if len(one_df) < 10:
            continue

        hourly = one_df.groupby("hour")["wait_time"].mean()
        future_hours = [h for h in hourly.index if h > now_hour and h < CROWD_END_HOUR]

        if not future_hours:
            continue

        future_min = float(hourly.loc[future_hours].min())
        future_hour = int(hourly.loc[future_hours].idxmin())
        drop = current_wait - future_min

        if drop >= 10:
            rows.append({"候補": attraction, "おすすめ時間": f"{future_hour}時以降", "現在待ち時間": int(current_wait), "予想平均との差": round(drop, 1), "理由": f"{future_hour}時以降に下がりやすい候補です。"})

    if not rows:
        return pd.DataFrame([{"候補": "履歴データ不足", "理由": "今後明確に下がりやすい候補はまだ判定できません。"}])

    return pd.DataFrame(rows).sort_values("予想平均との差", ascending=False).head(limit)


DISNEYSEA_AREA_RULES = {
    "ファンタジースプリングス": ["Anna", "Elsa", "Rapunzel", "Peter Pan", "Tinker Bell"],
    "メディテレーニアンハーバー": ["Soaring", "Transit Steamer", "Gondolas", "Fortress"],
    "アメリカンウォーターフロント": ["Tower of Terror", "Toy Story", "Turtle Talk", "Electric Railway", "Big City"],
    "ポートディスカバリー": ["Aquatopia", "Nemo", "SeaRider"],
    "ロストリバーデルタ": ["Indiana Jones", "Raging Spirits"],
    "アラビアンコースト": ["Sinbad", "Magic Lamp", "Caravan", "Jasmine"],
    "マーメイドラグーン": ["Mermaid", "Flounder", "Scuttle", "Jumpin", "Blowfish", "Whirlpool"],
    "ミステリアスアイランド": ["Journey to the Center", "20,000 Leagues"],
}


DISNEYLAND_AREA_RULES = {
    "ワールドバザール": ["Omnibus", "Penny Arcade"],
    "アドベンチャーランド": ["Pirates", "Jungle Cruise", "Tiki", "Western River"],
    "ウエスタンランド": ["Big Thunder", "Country Bear", "Mark Twain", "Tom Sawyer"],
    "クリッターカントリー": ["Splash Mountain", "Beaver Brothers"],
    "ファンタジーランド": ["Pooh", "Peter Pan", "Pinocchio", "Snow White", "Small World", "Haunted Mansion", "Castle Carrousel", "Dumbo", "PhilharMagic"],
    "トゥーンタウン": ["Roger Rabbit", "Gadget", "Go Coaster", "Minnie's House", "Donald's Boat", "Chip"],
    "トゥモローランド": ["Baymax", "Monsters", "Space Mountain", "Buzz", "Star Tours", "Stitch"],
    "美女と野獣エリア": ["Beauty and the Beast", "Enchanted Tale"],
}


def classify_attraction_area(attraction, park):
    rules = DISNEYSEA_AREA_RULES if park == "DisneySea" else DISNEYLAND_AREA_RULES

    for area, keywords in rules.items():
        for keyword in keywords:
            if keyword.lower() in str(attraction).lower():
                return area

    return "その他"


def get_area_crowd_map(all_df, park):
    if len(all_df) == 0 or "Attraction" not in all_df.columns:
        return pd.DataFrame([{"エリア": "データなし", "平均待ち時間": 0, "営業中施設数": 0, "混雑レベル": "データなし"}])

    df = all_df.copy()
    df["Area"] = df["Attraction"].apply(lambda x: classify_attraction_area(x, park))
    df = df[(df["Open"] == True) & (df["Wait"] > 0)].copy()

    if len(df) == 0:
        return pd.DataFrame([{"エリア": "データなし", "平均待ち時間": 0, "営業中施設数": 0, "混雑レベル": "営業中データなし"}])

    rows = []

    for area, group in df.groupby("Area"):
        avg_wait = float(group["Wait"].mean())
        if avg_wait >= 100:
            level = "🔴 非常に混雑"
        elif avg_wait >= 60:
            level = "🟠 混雑"
        elif avg_wait >= 30:
            level = "🟡 普通"
        else:
            level = "🟢 空いている"

        rows.append({"エリア": area, "平均待ち時間": round(avg_wait, 1), "営業中施設数": int(len(group)), "混雑レベル": level})

    return pd.DataFrame(rows).sort_values("平均待ち時間", ascending=False)


def get_historical_crowd_rank(history_df, settings, lookback_days=30):
    if len(history_df) == 0 or "date" not in history_df.columns:
        return {"message": "比較には履歴データが不足しています。", "percentile": None, "today_avg": None, "days": 0, "level": "データ不足"}

    target_df = history_df[history_df["attraction"].isin(settings["rides"]) & (history_df["wait_time"] > 0)].copy()
    target_df = filter_crowd_history(target_df)

    if len(target_df) == 0:
        return {"message": "比較には9:00〜20:59の5大アトラクション履歴が不足しています。", "percentile": None, "today_avg": None, "days": 0, "level": "データ不足"}

    today = datetime.now(JST).date()
    start_date = today - timedelta(days=lookback_days)
    target_df = target_df[target_df["date"] >= start_date]
    daily_avg = target_df.groupby("date")["wait_time"].mean()

    if today not in daily_avg.index or len(daily_avg) < 5:
        today_avg = round(float(daily_avg.get(today, 0)), 1) if today in daily_avg.index else None
        return {"message": "比較には履歴データが不足しています。", "percentile": None, "today_avg": today_avg, "days": int(len(daily_avg)), "level": "データ不足"}

    today_avg = float(daily_avg.loc[today])
    percentile = float((daily_avg <= today_avg).mean() * 100)

    if percentile >= 80:
        level = "かなり混雑しています"
    elif percentile >= 60:
        level = "やや混雑しています"
    elif percentile >= 40:
        level = "平均的です"
    else:
        level = "比較的空いています"

    return {"message": f"今日は過去{len(daily_avg)}日中 上位{100 - percentile:.0f}%の混雑です。{level}", "percentile": round(percentile, 1), "today_avg": round(today_avg, 1), "days": int(len(daily_avg)), "level": level}



def get_prediction_risk_diagnosis(
    history_df,
    prediction_history,
    current_df,
    settings,
    ticket_price=None,
    weather_source="",
):
    rows = []
    now = datetime.now(JST)

    def add(item, status, detail, severity):
        rows.append({
            "診断項目": item,
            "状態": status,
            "理由": detail,
            "重要度": severity,
        })

    if len(history_df) == 0:
        add("履歴データ", "不足", "待ち時間履歴がまだありません。予測は標準値に近くなります。", "高")
    else:
        model_df = history_df.copy()
        if "attraction" in model_df.columns:
            model_df = model_df[model_df["attraction"].isin(settings["rides"])].copy()
        model_df = filter_crowd_history(model_df)
        days = model_df["date"].nunique() if len(model_df) > 0 and "date" in model_df.columns else 0
        ride_count = model_df["attraction"].nunique() if len(model_df) > 0 and "attraction" in model_df.columns else 0
        if days < 7:
            add("履歴データ", "少ない", f"5大アトラクションの有効履歴が{days}日分です。曜日傾向が弱くなります。", "高")
        elif days < 21:
            add("履歴データ", "やや少ない", f"有効履歴は{days}日分です。季節差やイベント差はまだ弱めです。", "中")
        else:
            add("履歴データ", "良好", f"有効履歴は{days}日分あります。", "低")

        if ride_count < len(settings["rides"]):
            add("5大アトラクション網羅", "不足", f"{ride_count}/{len(settings['rides'])}施設分だけ履歴があります。", "中")
        else:
            add("5大アトラクション網羅", "良好", "5大アトラクションの履歴がそろっています。", "低")

    if len(current_df) == 0:
        add("現在値", "未取得", "現在の待ち時間が取れていません。今日の補正が弱くなります。", "中")
    else:
        current_major = current_df[current_df["Attraction"].isin(settings["rides"])] if "Attraction" in current_df.columns else pd.DataFrame()
        open_major = current_major[(current_major["Open"] == True) & (current_major["Wait"] > 0)] if len(current_major) > 0 else pd.DataFrame()
        if len(open_major) < max(3, len(settings["rides"]) - 1):
            add("現在値", "一部不足", f"現在取得できている5大アトラクションは{len(open_major)}件です。", "中")
        else:
            add("現在値", "良好", "現在の5大アトラクション待ち時間を補正に使えます。", "低")

        if len(history_df) > 0 and "hour" in history_df.columns and len(open_major) > 0:
            hist = history_df[history_df["attraction"].isin(settings["rides"])].copy()
            hist = filter_crowd_history(hist)
            same_hour = hist[hist["hour"] == now.hour] if len(hist) > 0 else pd.DataFrame()
            if len(same_hour) >= 10:
                current_avg = float(open_major["Wait"].mean())
                baseline = float(same_hour["wait_time"].median())
                diff = current_avg - baseline
                if abs(diff) >= 30:
                    add("今日の異常度", "大きい", f"同時間帯の中央値より約{diff:+.0f}分ずれています。通常日と違う可能性があります。", "高")
                elif abs(diff) >= 15:
                    add("今日の異常度", "やや大きい", f"同時間帯の中央値より約{diff:+.0f}分ずれています。", "中")
                else:
                    add("今日の異常度", "通常範囲", "同時間帯の過去傾向から大きく外れていません。", "低")

    if len(prediction_history) == 0 or "error" not in prediction_history.columns:
        add("誤差学習", "未蓄積", "予測と実測の差がまだ学習できていません。", "高")
    else:
        err = prediction_history[prediction_history["error"].notna()].copy()
        if len(err) < 20:
            add("誤差学習", "少ない", f"誤差データは{len(err)}件です。補正はまだ弱めです。", "中")
        else:
            recent_mae = float(err["error"].abs().tail(80).mean())
            if recent_mae >= 30:
                add("誤差学習", "要注意", f"最近の平均誤差が約{recent_mae:.1f}分です。予測幅を広めに見てください。", "高")
            elif recent_mae >= 18:
                add("誤差学習", "注意", f"最近の平均誤差が約{recent_mae:.1f}分です。", "中")
            else:
                add("誤差学習", "良好", f"最近の平均誤差は約{recent_mae:.1f}分です。", "低")

    if ticket_price is None or pd.isna(ticket_price):
        add("チケット価格", "未取得", "価格シグナルが使えません。休日・繁忙日の判定が少し弱くなります。", "中")
    elif float(ticket_price) >= 10900:
        add("チケット価格", "高価格日", f"チケット価格が{int(ticket_price)}円です。混雑寄りのシグナルです。", "中")
    else:
        add("チケット価格", "取得済み", f"チケット価格は{int(ticket_price)}円です。", "低")

    if not ("日別" in str(weather_source) or "予報" in str(weather_source)):
        add("天気", "現在値寄り", "対象日の天気予報ではなく、現在天気を使っている可能性があります。", "中")
    else:
        add("天気", "取得済み", "天気シグナルを予測に使えます。", "低")

    return pd.DataFrame(rows)


def get_guest_action_plan(wait_prediction_df, crowd_index=None, ticket_price=None):
    if len(wait_prediction_df) == 0 or "Predicted Wait" not in wait_prediction_df.columns:
        return pd.DataFrame([{
            "優先度": "確認",
            "おすすめ": "予測データ不足",
            "理由": "時間帯別の待ち時間予測がまだ作成できません。",
        }])

    df = wait_prediction_df.copy()
    df = df[df["Predicted Wait"].notna()].copy()
    if len(df) == 0:
        return pd.DataFrame([{
            "優先度": "確認",
            "おすすめ": "予測データ不足",
            "理由": "有効な予測値がありません。",
        }])

    hourly = df.groupby("Hour")["Predicted Wait"].mean().sort_values()
    rows = []

    if len(hourly) > 0:
        best_hour = int(hourly.index[0])
        best_wait = float(hourly.iloc[0])
        rows.append({
            "優先度": "高",
            "おすすめ": f"{best_hour}:00台を狙う",
            "理由": f"5大アトラクション平均が約{best_wait:.0f}分で、予測上いちばん軽い時間帯です。",
        })

    if len(hourly) >= 3:
        peak_hour = int(hourly.index[-1])
        peak_wait = float(hourly.iloc[-1])
        rows.append({
            "優先度": "高",
            "おすすめ": f"{peak_hour}:00台は避ける",
            "理由": f"平均待ち時間が約{peak_wait:.0f}分まで上がる予測です。",
        })

    peak_by_ride = df.groupby("Attraction")["Predicted Wait"].max().sort_values(ascending=False)
    if len(peak_by_ride) > 0:
        ride = str(peak_by_ride.index[0])
        wait = float(peak_by_ride.iloc[0])
        rows.append({
            "優先度": "中",
            "おすすめ": f"{ride}は早めに判断",
            "理由": f"最大で約{wait:.0f}分まで伸びる予測です。DPAや朝の優先利用を検討してください。",
        })

    if crowd_index is not None and crowd_index >= 7:
        rows.append({
            "優先度": "高",
            "おすすめ": "休憩と食事を前倒し",
            "理由": "混雑指数が高めです。昼前後の待ち時間増加を避けると動きやすくなります。",
        })

    if ticket_price is not None and not pd.isna(ticket_price) and float(ticket_price) >= 10900:
        rows.append({
            "優先度": "中",
            "おすすめ": "人気施設は後回しにしすぎない",
            "理由": "高価格日は来園需要が強い日として扱っています。",
        })

    return pd.DataFrame(rows)
def get_current_stats(valid_open_df):
    if len(valid_open_df) > 0:
        avg_wait = valid_open_df["Wait"].mean()
        max_wait = valid_open_df["Wait"].max()
        var_wait = valid_open_df["Wait"].var()
    else:
        avg_wait = 0
        max_wait = 0
        var_wait = 0

    if pd.isna(var_wait):
        var_wait = 0

    return avg_wait, max_wait, var_wait


def get_today_stats(history_df, valid_open_df):
    today = datetime.now(JST).date()
    history_df = filter_crowd_history(history_df)

    if len(history_df) > 0 and "date" in history_df.columns:
        today_df = history_df[
            (history_df["date"] == today)
            & (history_df["wait_time"] > 0)
        ].copy()
    else:
        today_df = pd.DataFrame()

    if len(today_df) > 0:
        avg_wait = today_df["wait_time"].mean()
        max_wait = today_df["wait_time"].max()
        var_wait = today_df["wait_time"].var()
        source = "??9:00?20:59????????????"
    elif len(valid_open_df) > 0:
        avg_wait = valid_open_df["Wait"].mean()
        max_wait = valid_open_df["Wait"].max()
        var_wait = valid_open_df["Wait"].var()
        source = "??????????????????"
    else:
        avg_wait = 0
        max_wait = 0
        var_wait = 0
        source = "???????"

    if pd.isna(var_wait):
        var_wait = 0

    return avg_wait, max_wait, var_wait, source

def get_prediction_gap_summary(prediction_history):
    if len(prediction_history) == 0:
        return pd.DataFrame([
            {
                "理由": "予測データがまだ保存されていません",
                "件数": 0,
                "説明": "アプリを営業中に起動して予測を作成すると、次回以降に誤差を記録できます。"
            }
        ])

    pending_df = prediction_history[
        prediction_history["error"].isna()
    ].copy()

    if len(pending_df) == 0:
        return pd.DataFrame([
            {
                "理由": "未採点の予測はありません",
                "件数": 0,
                "説明": "保存済みの予測には実測値が入り、誤差データが作成されています。"
            }
        ])

    now = datetime.now(JST)
    pending_df["created_date"] = pending_df["created_at"].dt.date

    def classify(row):
        target_hour = int(row["target_hour"])

        if target_hour < OPEN_HOUR or target_hour >= CROWD_END_HOUR:
            return "21:00〜翌9:00は混雑指数・誤差更新の対象外"

        if row["created_at"].date() == now.date() and target_hour > now.hour:
            return "対象時刻がまだ来ていない"

        if row["created_at"].date() == now.date() and target_hour == now.hour:
            return "現在時刻の実測待ち時間がまだ保存されていない"

        return "対象時刻に営業中の有効な待ち時間データがなかった"

    pending_df["理由"] = pending_df.apply(classify, axis=1)

    summary = pending_df.groupby(["created_date", "理由"]).size().reset_index(name="件数")
    summary = summary.rename(columns={"created_date": "予測日"})
    summary["説明"] = summary["理由"].map({
        "21:00〜翌9:00は混雑指数・誤差更新の対象外": "夜間データは仕様により混雑指数にも予測誤差にも使いません。",
        "対象時刻がまだ来ていない": "未来の予測なので、対象時刻になってから実測値と照合します。",
        "現在時刻の実測待ち時間がまだ保存されていない": "アプリの更新タイミングで実測値が入ると誤差を計算します。",
        "対象時刻に営業中の有効な待ち時間データがなかった": "休止、閉園、通信失敗、または待ち時間0分などで比較できる実測値がありませんでした。"
    })

    return summary


def get_weather_score(weather_text, rain_mm, temperature):
    score = 0

    if rain_mm > 0:
        score -= 1

    if temperature >= 30:
        score -= 1

    if weather_text == "晴れ":
        score += 1

    return score


def get_dpa_score(avg_wait, max_wait):
    score = 0

    if max_wait >= 180:
        score += 2

    if avg_wait >= 120:
        score += 2

    return score



def format_crowd_index(value):
    try:
        return f"{float(value):.1f}"
    except Exception:
        return "0.0"







def _attraction_prediction_profiles(wait_prediction_df, limit=5):
    if wait_prediction_df is None or len(wait_prediction_df) == 0:
        return []
    required = {"Attraction", "Hour", "Predicted Wait"}
    if not required.issubset(wait_prediction_df.columns):
        return []

    df = wait_prediction_df.copy()
    df = df[df["Predicted Wait"].notna()].copy()
    if len(df) == 0:
        return []

    rows = []
    for attraction, group in df.groupby("Attraction"):
        group = group.sort_values("Hour").copy()
        best = group.sort_values("Predicted Wait").head(1)
        worst = group.sort_values("Predicted Wait", ascending=False).head(1)
        if len(best) == 0 or len(worst) == 0:
            continue

        group["diff"] = group["Predicted Wait"].diff()
        surge = group.sort_values("diff", ascending=False).head(1)
        surge_hour = None
        surge_delta = 0
        if len(surge) > 0 and not pd.isna(surge.iloc[0].get("diff")):
            surge_hour = int(surge.iloc[0]["Hour"])
            surge_delta = float(surge.iloc[0]["diff"])

        best_row = best.iloc[0]
        worst_row = worst.iloc[0]
        rows.append({
            "attraction": str(attraction),
            "hour": int(best_row["Hour"]),
            "minute": int(best_row.get("Minute", 0)),
            "time_label": str(best_row.get("TimeLabel", f"{int(best_row['Hour']):02d}:00")),
            "wait": float(best_row["Predicted Wait"]),
            "peak_hour": int(worst_row["Hour"]),
            "peak_minute": int(worst_row.get("Minute", 0)),
            "peak_time_label": str(worst_row.get("TimeLabel", f"{int(worst_row['Hour']):02d}:00")),
            "peak_wait": float(worst_row["Predicted Wait"]),
            "surge_hour": surge_hour,
            "surge_delta": surge_delta,
            "range": float(worst_row["Predicted Wait"] - best_row["Predicted Wait"]),
        })

    return sorted(rows, key=lambda x: (x["wait"], -x["range"]))[:limit]


def _low_wait_slots(wait_prediction_df, limit=3):
    return _attraction_prediction_profiles(wait_prediction_df, limit=limit)


def _short_attraction_name(name):
    replacements = {
        "Soaring: Fantastic Flight": "\u30bd\u30a2\u30ea\u30f3",
        "Tower of Terror": "\u30bf\u30ef\u30c6\u30e9",
        "Toy Story Mania!": "\u30c8\u30a4\u30de\u30cb",
        "Journey to the Center of the Earth": "\u30bb\u30f3\u30bf\u30fc",
        "Anna and Elsa's Frozen Journey": "\u30a2\u30ca\u96ea",
        "Enchanted Tale of Beauty and the Beast": "\u7f8e\u5973\u3068\u91ce\u7363",
        "The Happy Ride with Baymax": "\u30d9\u30a4\u30de\u30c3\u30af\u30b9",
        "Monsters, Inc. Ride & Go Seek!": "\u30e2\u30f3\u30b9\u30bf\u30fc\u30ba\u30a4\u30f3\u30af",
        "Pooh's Hunny Hunt": "\u30d7\u30fc\u3055\u3093",
        "Splash Mountain": "\u30b9\u30d7\u30e9\u30c3\u30b7\u30e5",
    }
    value = replacements.get(str(name), str(name).replace("TM", "").replace("?", ""))
    return value.encode("ascii").decode("unicode_escape") if "\\u" in value else value


def _x_level_text(crowd_index):
    value = float(crowd_index)
    if value >= 8.5:
        return _u(r"\U0001f534 \u8d85\u6df7\u96d1")
    if value >= 6.5:
        return _u(r"\U0001f534 \u6df7\u96d1")
    if value >= 5:
        return _u(r"\U0001f7e0 \u6df7\u96d1")
    if value >= 3:
        return _u(r"\U0001f7e1 \u666e\u901a")
    return _u(r"\U0001f7e2 \u7a7a\u3044\u3066\u3044\u308b")


def _truncate_text(text, limit):
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def _u(text):
    try:
        return text.encode("ascii").decode("unicode_escape")
    except UnicodeEncodeError:
        return text


def _major_attraction_names(wait_prediction_df):
    if wait_prediction_df is None or len(wait_prediction_df) == 0 or "Attraction" not in wait_prediction_df.columns:
        return ""
    names = []
    for name in wait_prediction_df["Attraction"].dropna().tolist():
        short = _short_attraction_name(name)
        if short not in names:
            names.append(short)
        if len(names) >= 5:
            break
    return _u(r"\u3001").join(names)


def _x_pick_text(profile, include_particle=False):
    name = _short_attraction_name(profile.get("attraction", ""))
    time_label = profile.get("time_label") or f"{int(profile.get('hour', 0)):02d}:{int(profile.get('minute', 0)):02d}"
    wait = float(profile.get("wait", 0))
    if include_particle:
        return f"{name}は{time_label}に{wait:.0f}分予測"
    return f"{name}{time_label} {wait:.0f}分"


def _fit_x_post_text(line1, main_text, other_text):
    ending = "おすすめは午前に人気施設を1つ押さえ、夕方以降に下がる候補を拾う動き。"
    line2 = main_text
    if other_text:
        line2 += f"ほかの狙い目は{other_text}。"
    line2 += ending
    text = f"{line1}\n{line2}"

    if len(text) <= 280:
        return text

    if other_text and "、" in other_text:
        first_other = other_text.split("、")[0]
        line2 = f"{main_text}ほかの狙い目は{first_other}。{ending}"
        text = f"{line1}\n{line2}"
        if len(text) <= 280:
            return text

    line2 = f"{main_text}{ending}"
    text = f"{line1}\n{line2}"
    if len(text) <= 280:
        return text

    keep = max(0, 280 - len(line2) - 2)
    return f"{line1[:keep]}…\n{line2}"


def make_x_post_summary(
    park,
    target_date,
    crowd_index,
    wait_prediction_df,
    weather_source="",
    ticket_price=None,
    reasons=None,
):
    date_text = target_date.strftime("%m/%d") if hasattr(target_date, "strftime") else str(target_date)
    df = wait_prediction_df.copy() if wait_prediction_df is not None else pd.DataFrame()
    if len(df) > 0 and "Predicted Wait" in df.columns:
        df = df[df["Predicted Wait"].notna()].copy()

    level_text = _x_level_text(crowd_index)
    title = f"?{park} {date_text}??????????{format_crowd_index(crowd_index)}/10?{level_text}??"
    required_columns = {"Attraction", "Hour", "Predicted Wait"}
    if len(df) == 0 or not required_columns.issubset(df.columns):
        return (title + "??????????????????????????????")[:280]

    stats = get_prediction_crowd_stats(df)
    major_df = df[df["Attraction"].isin([name for park_data in PARK_SETTINGS.values() for name in park_data.get("rides", [])])].copy()
    if len(major_df) == 0:
        major_df = df.copy()
    major_avg = float(major_df["Predicted Wait"].mean()) if len(major_df) > 0 else 0

    hourly = df.groupby("Hour")["Predicted Wait"].mean()
    peak_hour = int(hourly.idxmax()) if len(hourly) > 0 else 0
    low_hour = int(hourly.idxmin()) if len(hourly) > 0 else 0

    profiles = _attraction_prediction_profiles(df, limit=3)
    if profiles:
        main = profiles[0]
        aim_text = f"????{_short_attraction_name(main['attraction'])}{main['hour']}?{main['wait']:.0f}??"
    else:
        aim_text = "?????????????????"

    text = (
        f"{title}??????{stats['avg_wait']:.0f}??5?????{major_avg:.0f}??"
        f"????{peak_hour}??????????{low_hour}???\n"
        f"{aim_text}?????????????1?????????????????????"
    )
    return text[:280]

def get_crowd_index_for_park(
    park,
    avg_wait,
    max_wait,
    top_quartile_wait,
    std_wait,
    open_count,
    closed_count,
    weather_score,
    feedback_error,
    demand_bonus,
):
    baseline = PARK_CROWD_BASELINES.get(park, PARK_CROWD_BASELINES["DisneySea"])
    avg_component = (avg_wait / baseline["avg_wait_normal"]) * 2.2 if baseline["avg_wait_normal"] else 0
    top_component = (top_quartile_wait / baseline["top_quartile_wait_normal"]) * 3.0 if baseline["top_quartile_wait_normal"] else 0
    max_component = (max_wait / baseline["max_wait_normal"]) * 1.5 if baseline["max_wait_normal"] else 0
    std_component = (std_wait / baseline["std_wait_normal"]) * 0.8 if baseline["std_wait_normal"] else 0
    closed_component = min(0.8, max(0, closed_count) * 0.06)
    open_component = -0.25 if open_count >= 25 else 0.15 if open_count and open_count < 12 else 0
    adjustment = weather_score * 0.18 + feedback_error * 0.10 + demand_bonus * 0.22
    raw = (avg_component + top_component + max_component + std_component + closed_component + open_component) * baseline["score_scale"]
    return round(min(10, max(1, raw + adjustment)), 1)


def get_crowd_index(avg_wait, max_wait, var_wait, dpa, weather_score, feedback_error, today_bonus):
    std_wait = np.sqrt(var_wait) if var_wait is not None and not pd.isna(var_wait) else 0
    top_quartile_wait = max(avg_wait, max_wait * 0.55)
    return get_crowd_index_for_park(
        "DisneySea",
        avg_wait,
        max_wait,
        top_quartile_wait,
        std_wait,
        20,
        0,
        weather_score,
        feedback_error,
        today_bonus,
    )


def get_level(crowd_10):
    if crowd_10 >= 8.5:
        return "?? ???", "#ff3b30"
    if crowd_10 >= 6.5:
        return "?? ??", "#ff453a"
    if crowd_10 >= 5.0:
        return "?? ????", "#ff9500"
    if crowd_10 >= 3.0:
        return "?? ??", "#ffcc00"
    return "?? ?????", "#34c759"

def make_action_advice(
    crowd_10,
    valid_all_df,
    valid_target_df,
    relative_rows,
    ticket_price,
    weather_text,
    rain_mm,
):
    advice = []

    if len(valid_all_df) == 0:
        advice.append("現在、営業中の有効な待ち時間データがありません。閉園後の可能性があります。")
        return advice

    if len(relative_rows) > 0:
        best = relative_rows[0]
        advice.append(
            f"今は **{best['name']}** が相対的におすすめです。現在{best['wait']}分で、同時間帯平均より低めです。"
        )
    else:
        sorted_df = valid_all_df.sort_values("Wait")
        best_row = sorted_df.iloc[0]
        advice.append(
            f"今すぐ軽く乗るなら **{best_row['Attraction']}** が候補です。現在{best_row['Wait']}分です。"
        )

    if crowd_10 >= 8:
        advice.append("今日はかなり混雑傾向です。人気アトラクションは早めに回るか、DPAの利用を検討してください。")
    elif crowd_10 >= 5:
        advice.append("今日は普通〜やや混雑です。待ち時間が伸びる前に人気アトラクションを優先するとよさそうです。")
    else:
        advice.append("今日は比較的動きやすい可能性があります。待ち時間の短いアトラクションをつなぐのがおすすめです。")

    if ticket_price is not None and ticket_price >= 9900:
        advice.append("チケット価格が高めなので、公式側も需要が高い日として見ている可能性があります。午後の混雑に注意です。")

    if rain_mm > 0 or weather_text == "雨":
        advice.append("雨の影響で屋外系は空きやすく、屋内系やレストランは混みやすい可能性があります。")

    if len(valid_target_df) > 0:
        target_sorted = valid_target_df.sort_values("Wait")
        target_best = target_sorted.iloc[0]
        advice.append(
            f"5大アトラクション内なら **{target_best['Attraction']}** が今の候補です。現在{target_best['Wait']}分です。"
        )

    return advice


def predict_dpa_risk(wait, crowd_10, ticket_price, bonus):
    score = 0

    if wait >= 180:
        score += 4
    elif wait >= 140:
        score += 3
    elif wait >= 100:
        score += 2
    elif wait >= 70:
        score += 1

    if crowd_10 >= 8:
        score += 2
    elif crowd_10 >= 6:
        score += 1

    if ticket_price is not None and ticket_price >= 9900:
        score += 1

    if bonus >= 6:
        score += 1

    if score >= 6:
        return "🔴 売切れリスク高", score
    elif score >= 3:
        return "🟠 売切れ注意", score
    else:
        return "🟢 低め", score
