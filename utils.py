import re
import sqlite3
import time
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
        "avg_wait_normal": 24,
        "top_quartile_wait_normal": 58,
        "max_wait_normal": 125,
        "std_wait_normal": 32,
        "park_bias": 1.0,
        "score_offset": -2.0,
        "demand_scale": 0.12,
    },
    "Disneyland": {
        "avg_wait_normal": 22,
