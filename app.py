import os
import time
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from utils import *

build_week_forecast = make_locked_week_forecast

JST = ZoneInfo("Asia/Tokyo")

OPEN_HOUR = 9
CROWD_END_HOUR = 21
AUTO_REFRESH_SECONDS = 900

st.set_page_config(
    page_title="ディズニー混雑AI",
    page_icon="🏰",
    layout="wide"
)


@st.cache_data(ttl=3600)
def cached_fetch_ticket_prices():
    return fetch_ticket_prices()


@st.cache_data(ttl=900)
def cached_fetch_urtrip_dpa_sellouts(park_name):
    return fetch_urtrip_dpa_sellouts(PARK_SETTINGS[park_name])

CUSTOM_CSS = """
<style>
:root {
    --ios-bg: #f2f2f7;
    --ios-card: rgba(255, 255, 255, 0.96);
    --ios-card-solid: #ffffff;
    --ios-text: #1d1d1f;
    --ios-subtext: #6e6e73;
    --ios-blue: #007aff;
    --ios-green: #34c759;
    --ios-yellow: #ffcc00;
    --ios-orange: #ff9500;
    --ios-red: #ff3b30;
    --ios-border: rgba(60, 60, 67, 0.13);
    --ios-shadow: 0 8px 26px rgba(0, 0, 0, 0.065);
}
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Hiragino Sans", "Yu Gothic", "Meiryo", sans-serif !important;
}
.stApp {
    background: var(--ios-bg);
    color: var(--ios-text);
}
.block-container {
    max-width: 880px;
    padding: 18px 14px 80px;
}
h1, h2, h3, h4, h5, h6, p, label, span, div {
    letter-spacing: 0 !important;
}
h1, h2, h3, h4, h5, h6 {
    color: var(--ios-text) !important;
    font-weight: 800 !important;
}
h2, h3 {
    font-size: 22px !important;
    margin: 22px 0 10px !important;
}
p, label, span, div {
    color: var(--ios-text);
}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p, .caption, .ios-subtitle, .ios-meta, .ios-label {
    color: var(--ios-subtext) !important;
}
.ios-title-card {
    background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(255,255,255,0.92));
    border: 1px solid var(--ios-border);
    border-radius: 28px;
    box-shadow: var(--ios-shadow);
    padding: 22px 20px;
    margin: 4px 0 12px;
}
.ios-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}
.ios-title-card h1 {
    margin: 0;
    font-size: 31px !important;
    line-height: 1.08;
}
