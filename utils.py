import re
import sqlite3
from datetime import datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import jpholiday
import numpy as np
import pandas as pd
import requests


JST = ZoneInfo("Asia/Tokyo")

PARK_SETTINGS = {
    "DisneySea": {
        "url": "https://queue-times.com/parks/275/queue_times.json",
        "db": "disneysea.db",
        "rides": [
            "Journey to the Center of the Earth",
            "Tower of Terror",
            "Anna and Elsa's Frozen Journey",
            "Soaring: Fantastic Flight",
            "Toy Story Mania!"
        ]
    },
    "Disneyland": {
        "url": "https://queue-times.com/parks/274/queue_times.json",
        "db": "disneyland.db",
        "rides": [
            "Enchanted Tale of Beauty and the Beast",
            "The Happy Ride with Baymax",
            "Monsters, Inc. Ride & Go Seek!",
            "Pooh's Hunny Hunt",
            "Splash Mountain"
        ]
    }
}

CASTEL_TICKET_URL = "https://castel.jp/p/7339"
GLOBAL_PREDICTION_NAME = "__ALL__"


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
            timeout=10
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


def get_ticket_price_from_castel(target_date, price_map):
    d = target_date.date() if isinstance(target_date, datetime) else target_date

    if d in price_map:
        return price_map[d], "Castelから取得"

    return None, "価格未掲載"


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

    if jpholiday.is_holiday(d):
        bonus += 2
        reasons.append("祝日")

    tomorrow = d + timedelta(days=1)

    if jpholiday.is_holiday(tomorrow):
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
    now_hour = datetime.now(JST).hour

    if len(valid_open_df) == 0:
        return

    actual_now = float(avg_wait)

    cursor.execute("""
    UPDATE predictions
    SET actual_wait = ?,
        error = ? - predicted_wait
    WHERE target_hour = ?
    AND actual_wait IS NULL
    AND attraction = ?
    """, (
        actual_now,
        actual_now,
        now_hour,
        GLOBAL_PREDICTION_NAME
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
        """, (
            actual_wait,
            actual_wait,
            now_hour,
            attraction
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
        source = "今日の開園後〜現在までの全アトラクション平均"
    elif len(valid_open_df) > 0:
        avg_wait = valid_open_df["Wait"].mean()
        max_wait = valid_open_df["Wait"].max()
        var_wait = valid_open_df["Wait"].var()
        source = "現在の営業中全アトラクションデータ"
    else:
        avg_wait = 0
        max_wait = 0
        var_wait = 0
        source = "有効データなし"

    if pd.isna(var_wait):
        var_wait = 0

    return avg_wait, max_wait, var_wait, source


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
