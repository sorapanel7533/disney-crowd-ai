import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

JST = ZoneInfo("Asia/Tokyo")

GLOBAL_PREDICTION_NAME = "__ALL__"

PARK_SETTINGS = {
    "DisneySea": {
        "url": "https://queue-times.com/parks/275/queue_times.json",
        "db": "disneysea.db"
    },
    "Disneyland": {
        "url": "https://queue-times.com/parks/274/queue_times.json",
        "db": "disneyland.db"
    }
}


def now_jst():
    return datetime.now(JST)


def now_jst_str():
    return now_jst().strftime("%Y-%m-%d %H:%M:%S")


def connect_db(db_name):

    conn = sqlite3.connect(
        db_name,
        check_same_thread=False
    )

    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wait_times (
        datetime TEXT,
        attraction TEXT,
        wait_time INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions (
        created_at TEXT,
        attraction TEXT,
        target_hour INTEGER,
        predicted_wait REAL,
        actual_wait REAL,
        error REAL
    )
    """)

    conn.commit()

    return conn, cursor


def fetch_wait_times(settings):

    response = requests.get(
        settings["url"],
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )

    data = response.json()

    results = []

    for ride in data["rides"]:

        results.append({
            "Attraction": ride["name"],
            "Wait": ride["wait_time"],
            "Open": ride["is_open"]
        })

    df = pd.DataFrame(results)

    return df


def get_valid_open_df(df):

    return df[
        (df["Open"] == True)
        &
        (df["Wait"] > 0)
    ].copy()


def load_history(conn):

    history_df = pd.read_sql_query(
        "SELECT * FROM wait_times",
        conn
    )

    if len(history_df) > 0:

        history_df["datetime"] = pd.to_datetime(
            history_df["datetime"]
        )

        history_df["hour"] = history_df["datetime"].dt.hour

    return history_df


def load_prediction_history(conn):

    prediction_df = pd.read_sql_query(
        "SELECT * FROM predictions",
        conn
    )

    return prediction_df


def save_wait_times(cursor, conn, valid_df):

    now = now_jst_str()

    for _, row in valid_df.iterrows():

        cursor.execute("""
        INSERT INTO wait_times
        VALUES (?, ?, ?)
        """, (
            now,
            row["Attraction"],
            int(row["Wait"])
        ))

    conn.commit()


def save_prediction_rows(
    cursor,
    conn,
    pred_df,
    attraction_name
):

    now = now_jst_str()

    for _, row in pred_df.iterrows():

        cursor.execute("""
        INSERT INTO predictions
        VALUES (?, ?, ?, ?, NULL, NULL)
        """, (
            now,
            attraction_name,
            int(row["Hour"]),
            float(row["Predicted Wait"])
        ))

    conn.commit()


def update_prediction_feedback(
    cursor,
    conn,
    valid_df
):

    now_hour = now_jst().hour

    for _, row in valid_df.iterrows():

        attraction = row["Attraction"]
        actual_wait = float(row["Wait"])

        cursor.execute("""
        UPDATE predictions
        SET actual_wait = ?,
            error = ? - predicted_wait
        WHERE target_hour = ?
        AND attraction = ?
        AND actual_wait IS NULL
        """, (
            actual_wait,
            actual_wait,
            now_hour,
            attraction
        ))

    conn.commit()


def get_feedback_error(
    prediction_history,
    attraction_name
):

    if len(prediction_history) == 0:
        return 0

    one_df = prediction_history[
        prediction_history["attraction"]
        == attraction_name
    ]

    one_df = one_df[
        one_df["error"].notna()
    ]

    if len(one_df) == 0:
        return 0

    return one_df["error"].tail(30).mean()
