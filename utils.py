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
