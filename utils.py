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
        cursor.execute("""
        INSERT INTO predictions
        (
            created_at,
            target_hour,
            predicted_wait,
            actual_wait,
            error,
            attraction
        )
        VALUES (?, ?, ?, NULL, NULL, ?)
        """, (
            created_at,
            int(row["Hour"]),
            float(row["Predicted Wait"]),
            attraction_name
        ))

    conn.commit()


def update_prediction_feedback(cursor, conn, valid_open_df, avg_wait):
    now = datetime.now(JST)
    now_hour = now.hour

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
    AND actual_wait IS NULL
    AND attraction = ?
    AND created_at LIKE ?
    """, (
        actual_now,
        actual_now,
        now_hour,
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
        AND actual_wait IS NULL
        AND attraction = ?
        AND created_at LIKE ?
        """, (
            actual_wait,
            actual_wait,
            now_hour,
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


def update_daily_crowd_feedback(cursor, conn, history_df, settings):
    if len(history_df) == 0:
        return

    today = datetime.now(JST).date()
    target_history = history_df[
        history_df["attraction"].isin(settings["rides"])
    ].copy()
    target_history = filter_crowd_history(target_history)

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

        if target_day > today:
            continue

        day_df = target_history[
            (target_history["date"] == target_day)
            & (target_history["wait_time"] > 0)
        ].copy()

        if len(day_df) == 0:
            continue

        avg_wait = day_df["wait_time"].mean()
        max_wait = day_df["wait_time"].max()
        var_wait = day_df["wait_time"].var()

        if pd.isna(var_wait):
            var_wait = 0

        actual_index = get_crowd_index(
            avg_wait,
            max_wait,
            var_wait,
            get_dpa_score(avg_wait, max_wait),
            0,
            0,
            0
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
            "季節/休暇シグナルを生成",
            saved_count,
            today
        )
        results.append(f"イベントシグナルを{saved_count}件保存")
    else:
        results.append("イベントシグナルは確認済み")

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


def _prepare_model_history(history_df, rides):
    if len(history_df) == 0:
        return history_df

    model_df = history_df[
        history_df["attraction"].isin(rides)
        & (history_df["wait_time"] > 0)
    ].copy()
    model_df = filter_crowd_history(model_df)

    if len(model_df) > 0:
        model_df["weekday"] = model_df["datetime"].dt.weekday
        model_df["month"] = model_df["datetime"].dt.month

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
    model_df = _prepare_model_history(history_df, settings["rides"])
    today = datetime.now(JST).date()
    now_hour = datetime.now(JST).hour

    target_bonus, target_reasons = get_calendar_bonus(target_date, ticket_price)
    rows = []

    if len(model_df) > 0:
        hour_all = model_df.groupby("hour")["wait_time"].median()
        attraction_all = model_df.groupby("attraction")["wait_time"].median()
        attraction_hour = model_df.groupby(["attraction", "hour"])["wait_time"].median()
        same_weekday = model_df[model_df["weekday"] == target_date.weekday()]
        same_month = model_df[model_df["month"] == target_date.month]
        overall = model_df["wait_time"].median()
    else:
        hour_all = pd.Series(dtype=float)
        attraction_all = pd.Series(dtype=float)
        attraction_hour = pd.Series(dtype=float)
        same_weekday = pd.DataFrame()
        same_month = pd.DataFrame()
        overall = 75

    current_map = {}
    if current_target_df is not None and len(current_target_df) > 0:
        current_map = dict(zip(current_target_df["Attraction"], current_target_df["Wait"]))

    for attraction in settings["rides"]:
        attraction_feedback = get_feedback_error(prediction_history, attraction)

        for hour in range(OPEN_HOUR, CROWD_END_HOUR):
            specific = attraction_hour.get((attraction, hour), None) if len(attraction_hour) > 0 else None
            ride_base = attraction_all.get(attraction, None) if len(attraction_all) > 0 else None
            hour_base = hour_all.get(hour, None) if len(hour_all) > 0 else None

            weekday_value = None
            if len(same_weekday) > 0:
                weekday_rows = same_weekday[
                    (same_weekday["attraction"] == attraction)
                    & (same_weekday["hour"] == hour)
                ]
                if len(weekday_rows) > 0:
                    weekday_value = weekday_rows["wait_time"].median()

            month_value = None
            if len(same_month) > 0:
                month_rows = same_month[
                    (same_month["attraction"] == attraction)
                    & (same_month["hour"] == hour)
                ]
                if len(month_rows) > 0:
                    month_value = month_rows["wait_time"].median()

            profile_value = None
            if ride_base is not None and not pd.isna(ride_base):
                profile_value = ride_base * HOUR_PROFILE.get(hour, 1.0)

            base_wait = _weighted_average([
                (specific, 0.34),
                (weekday_value, 0.20),
                (month_value, 0.12),
                (profile_value, 0.18),
                (ride_base, 0.10),
                (hour_base, 0.04),
                (overall, 0.02),
            ])

            current_reason = ""
            if target_date == today and attraction in current_map:
                distance = abs(hour - now_hour)
                if now_hour < 11:
                    current_weight = 0.08
                elif now_hour < 13:
                    current_weight = 0.16
                else:
                    current_weight = 0.26

                current_weight = current_weight / (distance + 1)
                if hour < now_hour:
                    current_weight *= 0.35

                base_wait = _weighted_average([
                    (base_wait, 1 - current_weight),
                    (current_map[attraction], current_weight),
                ])
                current_reason = "現在値は朝の一時的な偏りを避けるため低めの重みで反映"

            adjustment = target_bonus * 2.5

            if rain_mm > 0:
                adjustment -= min(8, rain_mm * 2)

            if temperature >= 30:
                adjustment -= 4
            elif temperature <= 5:
                adjustment -= 3

            predicted = max(0, base_wait + adjustment + attraction_feedback)

            rows.append({
                "Hour": hour,
                "Attraction": attraction,
                "Predicted Wait": round(predicted, 1),
                "理由": " / ".join([
                    "履歴中央値",
                    "曜日・月・時間帯プロファイル",
                    "予測誤差補正",
                    current_reason,
                ] + target_reasons).strip(" / ")
            })

    return pd.DataFrame(rows)


def make_major_average_prediction(wait_prediction_df):
    if len(wait_prediction_df) == 0:
        return pd.DataFrame(columns=["Hour", "Predicted Wait"])

    avg_df = wait_prediction_df.groupby("Hour")["Predicted Wait"].mean().reset_index()
    return avg_df.rename(columns={"Predicted Wait": "Predicted Wait"})


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

    if len(wait_df) == 0:
        avg_wait = 0
        max_wait = 0
        var_wait = 0
    else:
        avg_wait = wait_df["Predicted Wait"].mean()
        max_wait = wait_df["Predicted Wait"].max()
        var_wait = wait_df["Predicted Wait"].var()

    if pd.isna(var_wait):
        var_wait = 0

    target_bonus, reasons = get_calendar_bonus(target_date, ticket_price)
    event_bonus, event_reasons = get_event_bonus(event_signals if event_signals is not None else pd.DataFrame(), target_date, park)
    hours_bonus, hours_reasons = get_park_hours_bonus(park_hours_df if park_hours_df is not None else pd.DataFrame(), target_date)
    target_bonus += event_bonus + hours_bonus
    reasons.extend(event_reasons)
    reasons.extend(hours_reasons)
    weather_score = get_weather_score(
        "雨" if rain_mm > 0 else "晴れ",
        rain_mm,
        temperature
    )
    feedback_error = get_feedback_error(prediction_history, GLOBAL_PREDICTION_NAME)
    daily_feedback = get_daily_crowd_feedback_error(daily_prediction_history)

    crowd_index = get_crowd_index(
        avg_wait,
        max_wait,
        var_wait,
        get_dpa_score(avg_wait, max_wait),
        weather_score,
        feedback_error,
        target_bonus
    )

    crowd_index = int(min(10, max(1, round(crowd_index + daily_feedback))))

    reasons = reasons + [
        f"5大予想平均 {avg_wait:.1f}分",
        f"過去の混雑指数誤差補正 {daily_feedback:+.1f}",
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
):
    rows = []

    for i in range(7):
        d = start_date + timedelta(days=i)
        ticket_price, ticket_source = get_ticket_price_from_castel(d, ticket_price_map)
        forecast_temperature, forecast_rain, weather_source = get_forecast_weather_for_date(
            daily_weather,
            d,
            temperature,
            rain_mm
        )
        crowd_index, wait_df, reasons = predict_crowd_index_for_date(
            history_df,
            settings,
            d,
            forecast_temperature,
            forecast_rain,
            prediction_history,
            daily_prediction_history,
            ticket_price,
            current_target_df if d == datetime.now(JST).date() else None,
            event_signals,
            park_hours_df,
            park,
        )

        rows.append({
            "Date": d.strftime("%m/%d"),
            "Crowd Index": crowd_index,
            "予報気温": round(forecast_temperature, 1),
            "予報降水量": round(forecast_rain, 1),
            "天気取得元": weather_source,
            "5大平均待ち時間": round(wait_df["Predicted Wait"].mean(), 1) if len(wait_df) > 0 else 0,
            "チケット価格": "未取得" if ticket_price is None else ticket_price,
            "主な理由": " / ".join(reasons[:4]),
            "価格取得元": ticket_source,
        })

    return pd.DataFrame(rows)


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
            &
            (history_df["wait_time"] > 0)
        ].copy()
    else:
        today_df = pd.DataFrame()

    if len(today_df) > 0:
        avg_wait = today_df["wait_time"].mean()
        max_wait = today_df["wait_time"].max()
        var_wait = today_df["wait_time"].var()
        source = "今日9:00以降〜現在までの5大アトラクション平均"
    elif len(valid_open_df) > 0:
        avg_wait = valid_open_df["Wait"].mean()
        max_wait = valid_open_df["Wait"].max()
        var_wait = valid_open_df["Wait"].var()
        source = "現在の営業中5大アトラクションデータ"
    else:
        avg_wait = 0
        max_wait = 0
        var_wait = 0
        source = "有効データなし"

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


def get_crowd_index(avg_wait, max_wait, var_wait, dpa, weather_score, feedback_error, today_bonus):
    crowd_score = (
        avg_wait * 0.45
        + max_wait * 0.35
        + np.sqrt(var_wait) * 0.1
        + dpa * 20
        + weather_score * 5
        + feedback_error
        + today_bonus * 8
    )

    return int(min(10, max(1, round(crowd_score / 25))))


def get_level(crowd_10):
    if crowd_10 >= 9:
        return "🔴 超混雑", "red"
    elif crowd_10 >= 6:
        return "🟠 混雑", "orange"
    elif crowd_10 >= 3:
        return "🟡 普通", "gold"
    else:
        return "🟢 空いている", "#4ade80"


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
