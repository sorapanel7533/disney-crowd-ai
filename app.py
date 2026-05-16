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
.ios-subtitle {
    margin-top: 7px;
    font-size: 14px;
    line-height: 1.45;
}
.ios-pill {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    background: rgba(0, 122, 255, 0.10);
    color: var(--ios-blue) !important;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 750;
    white-space: nowrap;
}
.ios-section, .ios-card, .card, .advice-card, .rank-card {
    background: var(--ios-card);
    border: 1px solid var(--ios-border);
    border-radius: 24px;
    box-shadow: var(--ios-shadow);
    padding: 16px;
    margin: 10px 0 14px;
}
.ios-list-card {
    background: var(--ios-card-solid);
    border: 1px solid var(--ios-border);
    border-radius: 22px;
    overflow: hidden;
    box-shadow: 0 5px 18px rgba(0, 0, 0, 0.045);
    margin: 10px 0 14px;
}
.ios-list-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 54px;
    padding: 12px 14px;
    border-bottom: 1px solid rgba(60, 60, 67, 0.10);
}
.ios-list-row:last-child { border-bottom: none; }
.ios-list-title { font-size: 14px; font-weight: 750; color: var(--ios-text) !important; }
.ios-list-detail { font-size: 13px; color: var(--ios-subtext) !important; margin-top: 2px; }
.ios-list-value { font-size: 15px; font-weight: 800; white-space: nowrap; color: var(--ios-text) !important; }
.ios-card-grid, .compact-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
    margin: 8px 0 14px;
}
.compact-card, .compact-metric {
    background: var(--ios-card-solid);
    border: 1px solid var(--ios-border);
    border-radius: 18px;
    box-shadow: 0 5px 16px rgba(0, 0, 0, 0.045);
    padding: 11px 12px;
    min-height: 64px;
}
.compact-label, .ios-label {
    color: var(--ios-subtext) !important;
    font-size: 12px;
    font-weight: 750;
    line-height: 1.2;
}
.compact-value, .ios-value {
    color: var(--ios-text) !important;
    font-size: 16px;
    font-weight: 850;
    line-height: 1.24;
    margin-top: 4px;
    word-break: break-word;
}
.hero-crowd-card {
    background: radial-gradient(circle at top right, rgba(0,122,255,0.16), transparent 34%), var(--ios-card-solid);
    border: 1px solid var(--ios-border);
    border-radius: 28px;
    box-shadow: var(--ios-shadow);
    padding: 18px;
    margin: 12px 0 16px;
}
.hero-crowd-grid {
    display: grid;
    grid-template-columns: 1.2fr 1fr;
    gap: 14px;
    align-items: center;
}
.hero-crowd-index {
    font-size: 46px;
    font-weight: 900;
    line-height: 1;
    color: var(--ios-text) !important;
}
.hero-crowd-label {
    display: inline-flex;
    margin-top: 8px;
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 13px;
    font-weight: 800;
    background: rgba(0,122,255,0.10);
    color: var(--ios-blue) !important;
}
.ios-status-open, .ios-status-closed {
    border-radius: 999px;
    padding: 5px 9px;
    font-size: 12px;
    font-weight: 800;
    white-space: nowrap;
}
.ios-status-open { background: rgba(52,199,89,0.14); color: #168a38 !important; }
.ios-status-closed { background: rgba(142,142,147,0.15); color: #636366 !important; }
.ios-wait-badge {
    border-radius: 999px;
    padding: 6px 10px;
    background: rgba(0,122,255,0.10);
    color: var(--ios-blue) !important;
    font-weight: 850;
    white-space: nowrap;
}
.ios-segment-shell {
    background: rgba(118, 118, 128, 0.13);
    border: 1px solid rgba(60,60,67,0.08);
    border-radius: 999px;
    padding: 4px;
    margin: 8px 0 14px;
    box-shadow: inset 0 1px 2px rgba(0,0,0,0.04);
}
.ios-segment-status {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    margin-bottom: 6px;
}
.ios-segment-item {
    border-radius: 999px;
    text-align: center;
    padding: 9px 10px;
    font-size: 14px;
    font-weight: 850;
    background: #fff;
    color: var(--ios-text) !important;
    border: 1px solid rgba(60,60,67,0.08);
}
.ios-segment-item.active {
    background: var(--ios-blue);
    color: #fff !important;
    border-color: var(--ios-blue);
    box-shadow: 0 5px 14px rgba(0,122,255,0.24);
}
div[data-testid="stButton"] > button, .stButton > button {
    width: 100%;
    min-height: 44px;
    border-radius: 999px !important;
    font-weight: 800 !important;
    border: 1px solid rgba(60,60,67,0.12) !important;
    box-shadow: 0 5px 15px rgba(0,0,0,0.06) !important;
}
button[kind="primary"] {
    background: var(--ios-blue) !important;
    color: #fff !important;
}
button[kind="secondary"] {
    background: #fff !important;
    color: var(--ios-text) !important;
}
button[kind="primary"] p, button[kind="primary"] span { color: #fff !important; }
button[kind="secondary"] p, button[kind="secondary"] span { color: var(--ios-text) !important; }
div[data-baseweb="select"], input, textarea, [data-baseweb="input"] {
    border-radius: 18px !important;
}
div[data-baseweb="select"] {
    background: #fff !important;
    border: 1px solid var(--ios-border) !important;
    box-shadow: 0 5px 16px rgba(0,0,0,0.045);
    min-height: 46px;
}
[data-testid="stMetric"] {
    background: var(--ios-card-solid);
    border: 1px solid var(--ios-border);
    border-radius: 22px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.05);
    padding: 14px 13px;
    min-height: 92px;
}
[data-testid="stMetricLabel"] { color: var(--ios-subtext) !important; font-size: 12px !important; font-weight: 750 !important; }
[data-testid="stMetricValue"] { color: var(--ios-text) !important; font-size: 26px !important; font-weight: 900 !important; }
[data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stAlert"], [data-testid="stExpander"], [data-testid="stForm"], [data-testid="stPyplot"] {
    border-radius: 22px !important;
    overflow: hidden;
}
[data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stPyplot"] {
    background: #fff;
    border: 1px solid var(--ios-border);
    box-shadow: 0 6px 20px rgba(0,0,0,0.05);
    padding: 8px;
}
thead tr th, tbody tr td { color: var(--ios-text) !important; }
hr { border-color: rgba(60,60,67,0.12) !important; }
@media (max-width: 760px) {
    .block-container { padding: 12px 10px 74px; }
    [data-testid="stHorizontalBlock"] { gap: 0.55rem !important; }
    [data-testid="column"] { min-width: 0 !important; }
    .ios-title-card { border-radius: 24px; padding: 18px; }
    .ios-title-card h1 { font-size: 27px !important; }
    .hero-crowd-grid { grid-template-columns: 1fr; }
    .hero-crowd-index { font-size: 42px; }
    .ios-list-row { padding: 11px 12px; }
    .ios-card-grid, .compact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .compact-card, .compact-metric { min-height: 58px; padding: 9px 10px; }
    .compact-value, .ios-value { font-size: 14px; }
    h2, h3 { font-size: 20px !important; margin-top: 18px !important; }
}
@media (max-width: 420px) {
    .ios-card-grid, .compact-grid { grid-template-columns: 1fr; }
    .ios-segment-item { font-size: 13px; padding: 8px; }
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def clean_text(value, fallback='取得できませんでした'):
    if isinstance(value, str):
        return safe_display_text(value, fallback)
    return value


def clean_records(records):
    if isinstance(records, list):
        return [clean_records(x) for x in records]
    if isinstance(records, dict):
        return {clean_text(k, str(k)): clean_records(v) for k, v in records.items()}
    if isinstance(records, str):
        return clean_text(records)
    return records


def clean_dataframe(df):
    if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
        return df
    out = df.copy()
    out.columns = [safe_display_text(c, str(c)) for c in out.columns]
    for col in out.select_dtypes(include=["object"]).columns:
        out[col] = out[col].apply(lambda x: safe_display_text(x, "") if isinstance(x, str) else x)
    return out




def graph_ylim(values):
    if len(values) == 0:
        return 200

    max_value = max(values)

    if pd.isna(max_value):
        return 200

    if max_value > 200:
        return int(max_value + 50)

    return 200



def compact_card_grid(items):
    html = ['<div class="compact-grid">']
    for label, value in items:
        html.append(
            '<div class="compact-card">'
            f'<div class="compact-label">{escape(str(label))}</div>'
            f'<div class="compact-value">{escape(str(value))}</div>'
            '</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def ios_list_card(rows):
    html = ['<div class="ios-list-card">']
    for row in rows:
        title = escape(str(row.get("title", "")))
        detail = escape(str(row.get("detail", "")))
        value = row.get("value", "")
        value_html = str(value) if row.get("unsafe_value") else escape(str(value))
        html.append(
            '<div class="ios-list-row">'
            '<div>'
            f'<div class="ios-list-title">{title}</div>'
            f'<div class="ios-list-detail">{detail}</div>'
            '</div>'
            f'<div class="ios-list-value">{value_html}</div>'
            '</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_crowd_hero(crowd_index, level_text, avg_wait, max_wait_value, confidence_score):
    value = format_crowd_index(crowd_index)
    st.markdown(
        f"""
        <div class="hero-crowd-card">
          <div class="hero-crowd-grid">
            <div>
              <div class="ios-label">現在の混雑指数</div>
              <div class="hero-crowd-index">{escape(value)}<span style="font-size:18px;font-weight:800;color:#6e6e73;"> / 10</span></div>
              <div class="hero-crowd-label">{escape(str(level_text))}</div>
            </div>
            <div class="ios-card-grid" style="margin:0;">
              <div class="compact-metric"><div class="compact-label">人気主要アトラクション平均</div><div class="compact-value">{avg_wait:.1f}分</div></div>
              <div class="compact-metric"><div class="compact-label">人気主要アトラクション最大</div><div class="compact-value">{max_wait_value:.1f}分</div></div>
              <div class="compact-metric"><div class="compact-label">予測信頼度</div><div class="compact-value">{confidence_score}%</div></div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def crowd_level_label(crowd_index):
    value = float(crowd_index)
    if value >= 8.5:
        return "🔥 超混雑"
    if value >= 6.5:
        return "🔴 混雑"
    if value >= 5.0:
        return "🟠 やや混雑"
    if value >= 3.0:
        return "🟡 普通"
    return "🟢 空いている"


def safe_sort_head(df, sort_column, n=100, ascending=False):
    if df is None or len(df) == 0:
        return pd.DataFrame()

    display_df = df.copy()
    if sort_column in display_df.columns:
        display_df = display_df.sort_values(sort_column, ascending=ascending)

    return display_df.head(n)


def render_segmented_choice(label, options, key):
    clean_options = [str(x) for x in options if str(x).strip()]
    if not clean_options:
        return None
    if key not in st.session_state or st.session_state[key] not in clean_options:
        st.session_state[key] = clean_options[0]
    if hasattr(st, "segmented_control"):
        value = st.segmented_control(
            label,
            clean_options,
            default=st.session_state[key],
            key=f"{key}_segmented",
        )
    else:
        value = st.radio(
            label,
            clean_options,
            index=clean_options.index(st.session_state[key]),
            horizontal=True,
            key=f"{key}_radio",
        )
    if value != st.session_state[key]:
        st.session_state[key] = value
        st.rerun()
    return value


def filter_crowd_hours(df):
    if len(df) == 0 or "hour" not in df.columns:
        return df

    return df[
        (df["hour"] >= OPEN_HOUR)
        &
        (df["hour"] < CROWD_END_HOUR)
    ].copy()


now_display = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

if "park" not in st.session_state:
    st.session_state["park"] = "DisneySea"

st.markdown(f"""
<div class="ios-title-card">
  <div class="ios-title-row">
    <div>
      <h1>ディズニー混雑AI</h1>
      <div class="ios-subtitle">東京ディズニーリゾート混雑分析システム</div>
    </div>
    <div class="ios-pill">JST</div>
  </div>
  <div class="ios-subtitle">最終更新: {now_display}</div>
</div>
""", unsafe_allow_html=True)

park_options = ["DisneySea", "Disneyland"]
if hasattr(st, "segmented_control"):
    park = st.segmented_control(
        "パーク",
        park_options,
        default=st.session_state["park"],
        key="park_segmented"
    )
else:
    park = st.radio(
        "パーク",
        park_options,
        index=park_options.index(st.session_state["park"]),
        horizontal=True,
        key="park_radio"
    )
if park != st.session_state["park"]:
    st.session_state["park"] = park
    st.rerun()

settings = PARK_SETTINGS[park]

display_mode_options = [
    "ダッシュボード",
    "全アトラクション",
    "アトラクション別予測",
    "DPA/PP予測",
    "日付指定予測",
    "回り方プランナー",
    "データ管理",
]
if "display_mode_value" not in st.session_state:
    st.session_state["display_mode_value"] = "ダッシュボード"
if st.session_state["display_mode_value"] not in display_mode_options:
    st.session_state["display_mode_value"] = "ダッシュボード"

if hasattr(st, "segmented_control"):
    display_mode = st.segmented_control(
        "🧭 表示モード",
        display_mode_options,
        default=st.session_state["display_mode_value"],
        key="display_mode_segmented",
    )
else:
    display_mode = st.radio(
        "🧭 表示モード",
        display_mode_options,
        index=display_mode_options.index(st.session_state["display_mode_value"]),
        horizontal=True,
        key="display_mode_radio",
    )
if display_mode != st.session_state["display_mode_value"]:
    st.session_state["display_mode_value"] = display_mode
    st.rerun()


if "auto_refresh_enabled" not in st.session_state:
    st.session_state["auto_refresh_enabled"] = True

auto_refresh_enabled = st.sidebar.toggle(
    '自動更新（15分ごと）',
    value=st.session_state["auto_refresh_enabled"],
    key="auto_refresh_toggle",
)
st.session_state["auto_refresh_enabled"] = auto_refresh_enabled
last_update_text = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
next_update_text = (datetime.now(JST) + timedelta(seconds=AUTO_REFRESH_SECONDS)).strftime("%H:%Mごろ")
st.sidebar.caption(f'最終更新: {last_update_text}')
st.sidebar.caption(f'次回自動更新: {next_update_text}')
if auto_refresh_enabled and display_mode != 'データ管理':
    st.markdown(
        f"<meta http-equiv='refresh' content='{AUTO_REFRESH_SECONDS}'>",
        unsafe_allow_html=True,
    )
elif display_mode == 'データ管理':
    st.sidebar.caption('データ管理画面では重い取り込み作業を守るため自動更新を停止します。')
manual_data_refresh = st.sidebar.button('今すぐ更新')
if manual_data_refresh:
    st.session_state["run_heavy_data_refresh"] = True
    st.rerun()

conn, cursor = connect_db(settings["db"])
run_heavy_data_refresh = (
    display_mode == "データ管理"
    or bool(st.session_state.pop("run_heavy_data_refresh", False))
)

data_fetch_logs = load_data_fetch_logs(conn)
weather_snapshots = load_weather_snapshots(conn)
ticket_price_snapshots = load_ticket_price_snapshots(conn)
park_hours_df = load_park_hours(conn)
event_signals = load_event_signals(conn)
show_schedules = load_show_schedules(conn)
show_wait_context = load_show_wait_context(conn)
historical_import_logs = load_historical_import_logs(conn)

ticket_price_map = {}
ticket_map_source = "初期表示では外部取得を行いません"
if run_heavy_data_refresh:
    try:
        ticket_price_map, ticket_map_source = cached_fetch_ticket_prices()
    except Exception as exc:
        st.warning(f"チケット価格の取得に失敗しました。推定価格で表示します: {exc}")

ticket_price, ticket_source = get_ticket_price_from_castel(
    datetime.now(JST),
    ticket_price_map
)
if not run_heavy_data_refresh and len(ticket_price_snapshots) > 0 and "price" in ticket_price_snapshots.columns:
    today_ticket_rows = ticket_price_snapshots.copy()
    if "target_date" in today_ticket_rows.columns:
        today_ticket_rows = today_ticket_rows[today_ticket_rows["target_date"].astype(str) == str(datetime.now(JST).date())]
    if "park" in today_ticket_rows.columns:
        today_ticket_rows = today_ticket_rows[today_ticket_rows["park"].astype(str) == park]
    if len(today_ticket_rows) > 0:
        latest_ticket_row = today_ticket_rows.sort_values("observed_at").tail(1).iloc[0]
        if not pd.isna(latest_ticket_row.get("price")):
            ticket_price = int(latest_ticket_row.get("price"))
            ticket_source = "保存済みチケット価格"

today_bonus, today_reasons = get_calendar_bonus(
    datetime.now(JST),
    ticket_price
)

temperature = 0
rain_mm = 0
weather_text = "未取得"
hourly_weather = pd.DataFrame()
daily_weather = pd.DataFrame()
if not run_heavy_data_refresh and len(weather_snapshots) > 0:
    today_weather_rows = weather_snapshots.copy()
    if "target_date" in today_weather_rows.columns:
        today_weather_rows = today_weather_rows[today_weather_rows["target_date"].astype(str) == str(datetime.now(JST).date())]
    if "park" in today_weather_rows.columns:
        today_weather_rows = today_weather_rows[today_weather_rows["park"].astype(str) == park]
    if len(today_weather_rows) > 0:
        latest_weather_row = today_weather_rows.sort_values("observed_at").tail(1).iloc[0]
        temperature = float(latest_weather_row.get("temperature", 0) or 0)
        rain_mm = float(latest_weather_row.get("rain", 0) or 0)
        weather_text = safe_display_text(latest_weather_row.get("weather_text", "保存済み"), "保存済み")
if run_heavy_data_refresh:
    try:
        temperature, rain_mm, weather_text, hourly_weather, daily_weather = get_weather()
    except Exception as exc:
        st.warning(f"天気の取得に失敗しました。保存済みまたは初期値で表示します: {exc}")

auto_context_results = []
if run_heavy_data_refresh:
    with st.spinner("最新データを取得・保存しています..."):
        try:
            auto_context_results = auto_save_context_data(
                cursor,
                conn,
                park,
                ticket_price_map,
                ticket_map_source,
                temperature,
                rain_mm,
                weather_text
            )
            auto_context_results.extend(
                auto_collect_prediction_context(
                    cursor,
                    conn,
                    park
                )
            )
        except Exception as exc:
            st.warning(f"自動データ取得を完了できませんでした: {exc}")
        data_fetch_logs = load_data_fetch_logs(conn)
        weather_snapshots = load_weather_snapshots(conn)
        ticket_price_snapshots = load_ticket_price_snapshots(conn)
        park_hours_df = load_park_hours(conn)
        event_signals = load_event_signals(conn)
        show_schedules = load_show_schedules(conn)
        show_wait_context = load_show_wait_context(conn)
        historical_import_logs = load_historical_import_logs(conn)

all_df = pd.DataFrame(columns=["Attraction", "Wait", "Open"])
target_df = pd.DataFrame(columns=["Attraction", "Wait", "Open"])
if run_heavy_data_refresh:
    try:
        all_df, target_df = fetch_wait_times(settings)
    except Exception as exc:
        st.warning(f"現在の待ち時間データを取得できませんでした。保存済みデータを使って表示を続けます: {exc}")

if all_df.empty:
    st.info("初期表示では外部の待ち時間取得を行いません。保存済み履歴と予測を使って表示しています。最新化する場合は「今すぐ更新」を押してください。")

if run_heavy_data_refresh and len(all_df) > 0:
    try:
        save_attraction_status_snapshots(
            cursor,
            conn,
            park,
            all_df,
            settings["rides"]
        )
    except Exception as exc:
        st.warning(f"アトラクション状態の保存に失敗しました: {exc}")
attraction_status_snapshots = load_attraction_status_snapshots(conn)

valid_all_df = get_valid_open_df(all_df)
valid_target_df = get_valid_open_df(target_df)
today_show_schedules = show_schedules[
    show_schedules.get("target_date", "") == str(datetime.now(JST).date())
].copy() if len(show_schedules) > 0 else pd.DataFrame()
if run_heavy_data_refresh:
    try:
        save_show_wait_context(cursor, conn, park, today_show_schedules, valid_target_df)
        show_wait_context = load_show_wait_context(conn)
    except Exception as exc:
        st.warning(f"ショー前後の待ち時間メモ保存に失敗しました: {exc}")

history_df = load_history(conn)

current_avg_wait, current_max_wait, current_var_wait = get_current_stats(valid_all_df)
current_target_avg_wait, current_target_max_wait, current_target_var_wait = get_current_stats(valid_target_df)

target_history_df = history_df[
    history_df["attraction"].isin(settings["rides"])
].copy() if len(history_df) > 0 else history_df

target_history_df = filter_crowd_hours(target_history_df)

avg_wait, max_wait, var_wait, crowd_source = get_today_stats(
    target_history_df,
    valid_target_df
)
all_crowd_stats = get_all_attraction_crowd_stats(
    valid_all_df,
    history_df,
    datetime.now(JST).date()
)
major_crowd_stats = get_major_attraction_crowd_stats(
    valid_target_df,
    target_history_df,
    datetime.now(JST).date()
)

if len(valid_all_df) == 0:
    st.warning("現在は営業中の有効な待ち時間データがありません。")

prediction_history = load_prediction_history(conn)
daily_prediction_history = load_daily_crowd_predictions(conn)
dpa_sellout_history = load_dpa_sellouts(conn)
dpa_auto_fetch_result = "自動取得は通常画面では停止中です。データ管理または手動更新で実行します。"
if run_heavy_data_refresh:
    try:
        dpa_auto_fetch_result = auto_fetch_dpa_if_needed(
            cursor,
            conn,
            settings,
            park
        )
    except Exception as exc:
        dpa_auto_fetch_result = f"DPA自動取得に失敗しました: {exc}"
        st.warning(dpa_auto_fetch_result)
dpa_sellout_history = load_dpa_sellouts(conn)
dpa_fetch_logs = load_dpa_fetch_logs(conn)

global_feedback_error = get_feedback_error(
    prediction_history,
    GLOBAL_PREDICTION_NAME
)

if run_heavy_data_refresh:
    try:
        update_prediction_feedback(
            cursor,
            conn,
            valid_target_df,
            current_target_avg_wait
        )
    except Exception as exc:
        st.warning(f"予測誤差補正の更新に失敗しました: {exc}")

prediction_history = load_prediction_history(conn)

if run_heavy_data_refresh:
    try:
        update_daily_crowd_feedback(
            cursor,
            conn,
            history_df,
            settings,
            park
        )
    except Exception as exc:
        st.warning(f"日別混雑指数の誤差更新に失敗しました: {exc}")

daily_prediction_history = load_daily_crowd_predictions(conn)

global_feedback_error = get_feedback_error(
    prediction_history,
    GLOBAL_PREDICTION_NAME
)

event_bonus, event_reasons = get_event_bonus(
    event_signals,
    datetime.now(JST).date(),
    park
)
hours_bonus, hours_reasons = get_park_hours_bonus(
    park_hours_df,
    datetime.now(JST).date()
)
today_bonus += event_bonus + hours_bonus
today_reasons.extend(event_reasons)
today_reasons.extend(hours_reasons)

weather_score = get_weather_score(
    weather_text,
    rain_mm,
    temperature
)

park_open_now, today_open_hour, today_close_hour, park_hours_source = is_park_open_now(
    park_hours_df,
    park,
    datetime.now(JST)
)

dpa_score_today = get_dpa_score(
    major_crowd_stats["major_avg_wait"],
    major_crowd_stats["major_max_wait"]
)
crowd_10, crowd_debug = get_crowd_index_from_major_attractions(
    park,
    major_crowd_stats["major_avg_wait"],
    major_crowd_stats["major_max_wait"],
    major_crowd_stats["major_std_wait"],
    major_crowd_stats["major_count"],
    dpa_score_today,
    today_bonus,
    weather_score,
    global_feedback_error,
    return_debug=True
)

relative_rows = []

if len(valid_all_df) > 0 and len(history_df) > 20:

    now_hour = datetime.now(JST).hour

    hist_same_hour = history_df[
        (history_df["hour"] == now_hour)
        &
        (history_df["wait_time"] > 0)
    ]

    historical_avg = hist_same_hour.groupby(
        "attraction"
    )["wait_time"].mean()

    for _, row in valid_all_df.iterrows():

        name = row["Attraction"]
        wait = row["Wait"]

        if name in historical_avg.index and historical_avg[name] > 0:

            past_avg = historical_avg[name]
            ratio = wait / past_avg

            relative_rows.append({
                "name": name,
                "wait": wait,
                "avg": round(past_avg, 1),
                "ratio": round(ratio, 2)
            })

    relative_rows = sorted(
        relative_rows,
        key=lambda x: x["ratio"]
    )

save_wait_times(
    cursor,
    conn,
    all_df,
    temperature,
    rain_mm,
    park
)

history_df = load_history(conn)

if display_mode == "ダッシュボード":

    st.subheader("🌤 現在の天気")

    compact_card_grid([
        ("天気", weather_text),
        ("気温", f"{temperature}℃"),
        ("降水量", f"{rain_mm}mm"),
    ])

    st.subheader("📅 需要補正")

    compact_card_grid([
        ("チケット価格", "取得不可" if ticket_price is None else f"{ticket_price}円"),
        ("取得元", ticket_map_source if ticket_price is not None else ticket_source),
        ("補正値", today_bonus),
    ])

    st.subheader("🧭 今日のおすすめ行動")

    advice_list = make_action_advice(
        crowd_10,
        valid_all_df,
        valid_target_df,
        relative_rows,
        ticket_price,
        weather_text,
        rain_mm
    )

    for advice in advice_list[:3]:

        st.markdown(
            f"""
            <div class="advice-card">
            {advice}
            </div>
            """,
            unsafe_allow_html=True
        )

    st.subheader("🎢 人気主要アトラクション")

    display_df = target_df.copy()

    display_df["Status"] = display_df["Open"].apply(
        lambda x: "🟢 OPEN" if x else "⚫ CLOSED"
    )

    attraction_rows = []
    for _, row in display_df.iterrows():
        is_open = bool(row.get("Open", False))
        status_html = (
            '<span class="ios-status-open">OPEN</span>'
            if is_open
            else '<span class="ios-status-closed">CLOSED</span>'
        )
        attraction_rows.append({
            "title": row.get("Attraction", ""),
            "detail": "人気主要アトラクション",
            "value": f'<span class="ios-wait-badge">{row.get("Wait", 0)}分</span> {status_html}',
            "unsafe_value": True,
        })
    ios_list_card(attraction_rows)
    st.caption(
        "人気主要5施設とは、各パークで待ち時間が伸びやすい代表的なアトラクションです。"
        "DisneySea: センター / タワテラ / アナ雪 / ソアリン / トイマニ。"
        "Disneyland: 美女と野獣 / ベイマックス / モンスターズインク / プーさん / スプラッシュ。"
    )

    st.subheader("🟢 相対的に空いている")

    if len(relative_rows) > 0:

        for r in relative_rows[:5]:

            st.markdown(
                f"""
                <div class="rank-card">
                <b>{r['name']}</b><br>
                現在 {r['wait']}分<br>
                同時間帯平均 {r['avg']}分<br>
                相対値 {r['ratio']}
                </div>
                """,
                unsafe_allow_html=True
            )

    today_confidence = get_prediction_confidence(
        history_df,
        prediction_history,
        dpa_sellout_history,
        settings,
        datetime.now(JST).date(),
        "現在天気"
    )

    if park_open_now:
        render_crowd_hero(
            crowd_10,
            crowd_level_label(crowd_10),
            major_crowd_stats["major_avg_wait"],
            major_crowd_stats["major_max_wait"],
            today_confidence["score"]
        )
    else:
        st.markdown(
            f"""
            <div class="hero-crowd-card">
              <div class="ios-label">現在の混雑指数</div>
              <div class="hero-crowd-index" style="font-size:38px;">閉園中</div>
              <div class="hero-crowd-label">本日の営業は終了しました</div>
              <div class="ios-subtitle" style="margin-top:10px;">
                次に表示できるのは営業時間中です。予測・1週間予測・回り方プランナーは利用できます。
              </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    compact_card_grid([
        ("人気主要アトラクション平均", "閉園中" if not park_open_now else f"{avg_wait:.1f}分"),
        ("人気主要アトラクション最大待ち時間", "閉園中" if not park_open_now else f"{max_wait:.1f}分"),
        ("全体最大待ち時間", "閉園中" if not park_open_now else f"{all_crowd_stats['max_wait']:.1f}分"),
        ("営業中アトラクション数", all_crowd_stats["open_count"]),
        ("営業時間", f"{today_open_hour:.0f}:00〜{today_close_hour:.0f}:00"),
    ])

    st.caption(
        f"混雑指数は、履歴が安定している人気主要5施設の9:00〜20:59データを基準に算出しています。営業時間判定: {park_hours_source}"
    )
    with st.expander("信頼度・補正理由を見る", expanded=False):
        st.write("信頼度:", f"{today_confidence['score']}% / {today_confidence['label']}")
        st.write("信頼度の理由:", " / ".join(today_confidence["notes"]))
        if len(today_reasons) > 0:
            st.write("需要補正理由:", " / ".join(today_reasons))


    st.subheader("🎭 今日のショー/パレード")
    if len(today_show_schedules) > 0:
        st.dataframe(
            safe_sort_head(today_show_schedules, "show_time", 30, ascending=True)[
                ["show_time", "show_name", "category", "note"]
            ] if set(["show_time", "show_name", "category", "note"]).issubset(today_show_schedules.columns) else today_show_schedules,
            use_container_width=True
        )
    else:
        st.info("公式ショー時刻を取得できませんでした。推定時刻や仮のショー名は表示しません。")

    st.subheader("🎯 ショー前後の待ち時間メモ")
    st.dataframe(
        get_show_wait_insights(show_wait_context),
        use_container_width=True
    )
    st.subheader("🤖 人気主要アトラクションの予想平均待ち時間")

    st.subheader("今後空き始めそうな候補")
    st.dataframe(
        get_emptying_candidates(
            history_df,
            valid_all_df,
            settings,
            limit=5
        ),
        use_container_width=True
    )

    st.subheader("今日の過去比較ランキング")
    historical_rank = get_historical_crowd_rank(
        history_df,
        settings
    )
    st.markdown(
        f"""
        <div class="card">
        <h3>{historical_rank['level']}</h3>
        <p>{historical_rank['message']}</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.subheader("エリア別 混雑マップ")
    area_map_df = get_area_crowd_map(
        all_df,
        park
    )
    area_cols = st.columns(2)
    for i, row in area_map_df.iterrows():
        with area_cols[i % 2]:
            st.markdown(
                f"""
                <div class="card">
                <h3>{row['エリア']}</h3>
                <p>{row['混雑レベル']}</p>
                <p>平均待ち時間: {row['平均待ち時間']}分</p>
                <p>営業中施設数: {row['営業中施設数']}</p>
                </div>
                """,
                unsafe_allow_html=True
            )

    wait_pred_df = predict_wait_times_for_date(
        history_df,
        settings,
        datetime.now(JST).date(),
        temperature,
        rain_mm,
        prediction_history,
        ticket_price,
        valid_all_df
    )

    major_pred_df = make_major_average_prediction(wait_pred_df, settings["rides"])
    major_display_df = major_pred_df.rename(
        columns={"Predicted Wait": "人気主要アトラクション予想平均待ち時間"}
    )
    if len(major_display_df) > 0 and "Hour" in major_display_df.columns:
        major_display_df = major_display_df[
            pd.to_numeric(major_display_df["Hour"], errors="coerce").between(OPEN_HOUR, CROWD_END_HOUR - 1)
        ].copy()

    if len(major_display_df) > 0:
        if run_heavy_data_refresh:
            try:
                save_prediction_rows(
                    cursor,
                    conn,
                    major_pred_df,
                    GLOBAL_PREDICTION_NAME
                )

                for attraction in settings["rides"]:
                    one_pred_df = wait_pred_df[
                        wait_pred_df["Attraction"] == attraction
                    ][["Time", "TimeLabel", "Hour", "Minute", "Predicted Wait"]]

                    if len(one_pred_df) > 0:
                        save_prediction_rows(
                            cursor,
                            conn,
                            one_pred_df,
                            attraction
                        )
            except Exception as exc:
                st.warning(f"予測データの保存に失敗しました: {exc}")


        x_base_now = datetime.now(JST)
        x_target_date = (
            x_base_now.date() + timedelta(days=1)
            if x_base_now.hour >= 21
            else x_base_now.date()
        )
        x_ticket_price, _ = get_ticket_price_from_castel(
            x_target_date,
            ticket_price_map
        )
        x_temperature, x_rain, x_weather_source = get_forecast_weather_for_date(
            daily_weather,
            x_target_date,
            temperature,
            rain_mm
        )
        x_crowd, x_wait_df, x_reasons = predict_crowd_index_for_date(
            history_df,
            settings,
            x_target_date,
            x_temperature,
            x_rain,
            prediction_history,
            daily_prediction_history,
            x_ticket_price,
            valid_all_df if x_target_date == x_base_now.date() else None,
            event_signals,
            park_hours_df,
            park
        )
        x_post_text = make_x_post_summary(
            park,
            x_target_date,
            x_crowd,
            x_wait_df,
            x_weather_source,
            x_ticket_price,
            x_reasons
        )
        st.subheader("📝 21時投稿用 X文面")
        st.code(x_post_text, language="text")
        st.text_area(
            "投稿文コピー用",
            value=x_post_text,
            height=150,
            key=f"x_post_text_v29_{park}_{x_target_date}_{format_crowd_index(x_crowd)}_{sum(ord(ch) for ch in x_post_text)}"
        )
        st.caption("上に最新の生成文を固定表示しています。本文に天気・価格・営業時間は入れません。")
        st.subheader("予測の注意点")
        st.dataframe(
            get_prediction_alerts(
                wait_pred_df,
                today_confidence
            ),
            use_container_width=True
        )

        st.caption(
            "朝一の一時的な待ち時間は低い重みで反映し、履歴中央値・曜日・月・時間帯の形・予測誤差を優先しています。"
        )

        fig, ax = plt.subplots(figsize=(10, 5))
        actual_major_df = get_actual_wait_series_for_today(history_df, major_rides=settings["rides"])
        ax.plot(
            major_display_df["TimeLabel"] if "TimeLabel" in major_display_df.columns else major_display_df["Hour"],
            major_display_df["人気主要アトラクション予想平均待ち時間"],
            linewidth=2.4,
            label="予測"
        )
        if len(actual_major_df) > 0:
            ax.plot(
                actual_major_df["TimeLabel"],
                actual_major_df["Actual Wait"],
                linewidth=2.2,
                linestyle="--",
                marker="o",
                label="実測"
            )
        else:
            st.caption("実測データ蓄積中")
        if "TimeLabel" in major_display_df.columns:
            tick_df = major_display_df[major_display_df["Minute"].fillna(0).astype(int) == 0]
            ax.set_xticks(tick_df["TimeLabel"].tolist())
            ax.tick_params(axis="x", rotation=45)

        y_source = major_display_df["人気主要アトラクション予想平均待ち時間"].tolist()
        if len(actual_major_df) > 0:
            y_source += actual_major_df["Actual Wait"].tolist()
        ax.set_ylim(
            0,
            graph_ylim(y_source)
        )

        ax.set_ylabel("待ち時間（分）")
        ax.set_title(f"{park} 人気主要アトラクション平均待ち時間")
        ax.legend()

        st.pyplot(fig)
        plt.close(fig)
        best_row = major_display_df.sort_values("人気主要アトラクション予想平均待ち時間").iloc[0]
        peak_row = major_display_df.sort_values("人気主要アトラクション予想平均待ち時間", ascending=False).iloc[0]
        compact_card_grid([
            ("人気主要施設の狙い目", f"{best_row.get('TimeLabel', best_row.get('Hour'))} 約{best_row['人気主要アトラクション予想平均待ち時間']:.0f}分"),
            ("人気主要施設のピーク", f"{peak_row.get('TimeLabel', peak_row.get('Hour'))} 約{peak_row['人気主要アトラクション予想平均待ち時間']:.0f}分"),
        ])

    else:
        st.info("人気主要アトラクション平均予測には、9:00〜20:59の履歴データがもう少し必要です。")


    st.subheader("🧭 今日のおすすめ行動プラン")
    st.dataframe(
        get_guest_action_plan(
            wait_pred_df,
            crowd_10,
            ticket_price
        ),
        use_container_width=True
    )

    st.subheader("🩺 予測リスク診断")
    st.dataframe(
        get_prediction_risk_diagnosis(
            history_df,
            prediction_history,
            valid_target_df,
            settings,
            ticket_price,
            weather_text
        ),
        use_container_width=True
    )

    st.subheader('📅 1週間混雑指数予測')

    try:
        week_df = build_week_forecast(
            history_df,
            settings,
            datetime.now(JST).date(),
            temperature,
            rain_mm,
            prediction_history,
            daily_prediction_history,
            ticket_price_map,
            valid_all_df,
            event_signals,
            park_hours_df,
            park,
            daily_weather,
            cursor=cursor,
            conn=conn,
            use_live_current_data=False,
        )
    except Exception as exc:
        week_df = pd.DataFrame()
        st.warning(f"1週間予測の作成に失敗しました: {exc}")

    if len(week_df) > 0 and {"Date", "Crowd Index"}.issubset(week_df.columns):
        fig_week, ax_week = plt.subplots(figsize=(10, 4))
        week_plot_df = week_df.copy()
        week_plot_df["Crowd Index"] = pd.to_numeric(week_plot_df["Crowd Index"], errors="coerce")
        ax_week.plot(week_plot_df["Date"].astype(str), week_plot_df["Crowd Index"], marker="o", linewidth=2.4, label='混雑指数')
        for _, row in week_df.iterrows():
            try:
                value = float(row.get("Crowd Index", 0))
                level_label = get_level(value)[0] if isinstance(get_level(value), tuple) else str(get_level(value))
                ax_week.annotate(level_label.split(" ", 1)[0], (str(row.get("Date", "")), value), textcoords="offset points", xytext=(0, 8), ha="center")
            except Exception:
                pass
        ax_week.set_ylim(0, 10)
        ax_week.set_ylabel("混雑指数")
        ax_week.set_title(f"{park} 1週間混雑指数予測")
        ax_week.grid(alpha=0.18)
        ax_week.legend()
        ax_week.tick_params(axis="x", rotation=35)
        plt.tight_layout()
        st.pyplot(fig_week)
        plt.close(fig_week)
        st.dataframe(clean_dataframe(week_df), use_container_width=True)
    else:
        st.info('1週間予測データがまだありません。履歴データ、天気、価格、営業時間データの不足により作成できない可能性があります。')


elif display_mode == "全アトラクション":

    st.subheader("🎡 全アトラクション待ち時間")

    all_display = all_df.copy()
    if len(all_display) == 0 or "Attraction" not in all_display.columns:
        st.info("現在の全アトラクション待ち時間は取得できませんでした。保存済みデータがある場合はデータ管理画面で確認できます。")
        all_attraction_list = []
    else:
        all_display["Status"] = all_display["Open"].apply(
            lambda x: "🟢 OPEN" if x else "⚫ CLOSED"
        )

        all_display = all_display.sort_values(
            ["Open", "Wait"],
            ascending=[False, True]
        )

        st.dataframe(
            all_display[
                ["Attraction", "Wait", "Status"]
            ],
            use_container_width=True
        )

        all_attraction_list = sorted(
            all_df["Attraction"].dropna().unique()
        )

    st.subheader("📈 アトラクション別 履歴グラフ")

    if all_attraction_list:
        selected_history_attraction = st.selectbox(
            "🎢 グラフで見るアトラクションを選択",
            all_attraction_list
        )
    else:
        selected_history_attraction = None
        st.info("グラフ表示に使えるアトラクション一覧がまだありません。")

    graph_mode = st.selectbox(
        "📊 グラフの種類を選択",
        [
            "保存データそのまま",
            "日付ごとの平均",
            "時間帯ごとの平均"
        ]
    )

    if len(history_df) > 0 and selected_history_attraction:

        one_history = history_df[
            history_df["attraction"]
            == selected_history_attraction
        ].copy()

        one_history = one_history[
            one_history["wait_time"] > 0
        ]

        one_history = one_history.sort_values(
            "datetime"
        )

        if len(one_history) > 0:

            st.write(
                f"表示中: {selected_history_attraction}"
            )

            fig, ax = plt.subplots(
                figsize=(12, 5)
            )

            if graph_mode == "保存データそのまま":

                ax.plot(
                    one_history["datetime"],
                    one_history["wait_time"],
                    marker="o"
                )

                ax.set_xlabel("Time")
                ax.set_title(
                    f"{selected_history_attraction} Wait Time History"
                )

                plt.xticks(
                    rotation=30,
                    ha="right"
                )

                y_values = one_history["wait_time"].tolist()

            elif graph_mode == "日付ごとの平均":

                one_history["date"] = one_history["datetime"].dt.date

                daily_avg = one_history.groupby(
                    "date"
                )["wait_time"].mean().reset_index()

                daily_avg["date_label"] = pd.to_datetime(
                    daily_avg["date"]
                ).dt.strftime("%m/%d")

                ax.plot(
                    daily_avg["date_label"],
                    daily_avg["wait_time"],
                    marker="o"
                )

                ax.set_xlabel("Date")
                ax.set_title(
                    f"{selected_history_attraction} Daily Average Wait"
                )

                plt.xticks(
                    rotation=0,
                    ha="center"
                )

                y_values = daily_avg["wait_time"].tolist()

            else:

                hourly_avg = one_history.groupby(
                    "hour"
                )["wait_time"].mean().reset_index()

                hourly_avg = hourly_avg[
                    (hourly_avg["hour"] >= OPEN_HOUR)
                    &
                    (hourly_avg["hour"] < CROWD_END_HOUR)
                ]

                ax.plot(
                    hourly_avg["hour"],
                    hourly_avg["wait_time"],
                    marker="o"
                )

                ax.set_xticks(range(OPEN_HOUR, CROWD_END_HOUR))
                ax.set_xlabel("Hour")
                ax.set_title(
                    f"{selected_history_attraction} Average Wait by Hour"
                )

                y_values = hourly_avg["wait_time"].tolist()

            ax.set_ylim(0, graph_ylim(y_values))
            ax.set_ylabel("Wait Time")

            plt.tight_layout()

            st.pyplot(fig)

            st.subheader("📋 保存履歴")

            history_table = one_history[
                [
                    "datetime",
                    "wait_time",
                    "temperature",
                    "rain"
                ]
            ].tail(100)

            history_table.columns = [
                "保存時間",
                "待ち時間",
                "気温",
                "降水量"
            ]

            st.dataframe(
                history_table,
                use_container_width=True
            )

            st.subheader("📊 統計")

            s1, s2, s3 = st.columns(3)

            with s1:
                st.metric(
                    "平均待ち時間",
                    round(
                        one_history["wait_time"].mean(),
                        1
                    )
                )

            with s2:
                st.metric(
                    "最大待ち時間",
                    int(
                        one_history["wait_time"].max()
                    )
                )

            with s3:
                st.metric(
                    "保存数",
                    len(one_history)
                )

        else:
            st.info(
                "このアトラクションの有効な履歴データがまだありません。"
            )

    else:
        st.info(
            "履歴データがまだありません。営業中に起動すると保存されます。"
        )

elif display_mode == "アトラクション別予測":

    st.subheader('アトラクション別予測')

    source_attractions = []
    if len(history_df) > 0 and "attraction" in history_df.columns:
        source_attractions += history_df["attraction"].dropna().astype(str).tolist()
    if len(all_df) > 0 and "Name" in all_df.columns:
        source_attractions += all_df["Name"].dropna().astype(str).tolist()
    if len(all_df) > 0 and "Attraction" in all_df.columns:
        source_attractions += all_df["Attraction"].dropna().astype(str).tolist()
    source_attractions += settings.get("rides", [])
    attraction_list = sorted({name for name in source_attractions if str(name).strip()})

    if not attraction_list:
        st.info('アトラクション履歴がまだありません。営業中に待ち時間を取得すると表示されます。')
    else:
        area_map = get_attractions_by_theme_port(park, attraction_list)
        area_options = [area for area, names in area_map.items() if len(names) > 0]
        if not area_options:
            area_options = ['その他']
            area_map = {'その他': attraction_list}
        selected_area = render_segmented_choice('テーマポート/エリア', area_options, "attraction_theme_area")
        area_attractions = area_map.get(selected_area, attraction_list) or attraction_list
        if len(area_attractions) <= 8:
            selected_attraction = render_segmented_choice('アトラクション', area_attractions, "selected_attraction_button")
        else:
            selected_attraction = st.selectbox('このアトラクションの予測補正', area_attractions, key="selected_attraction_select")

        attraction_target_date = st.date_input('予測する日付', value=datetime.now(JST).date(), min_value=datetime.now(JST).date(), max_value=datetime.now(JST).date() + timedelta(days=7), key="attraction_prediction_date")
        attraction_temperature, attraction_rain, attraction_weather_source = get_forecast_weather_for_date(daily_weather, attraction_target_date, temperature, rain_mm)
        st.caption(f"{attraction_target_date} 天気予報: {safe_display_text(attraction_weather_source, '取得できません')} / 気温 {attraction_temperature:.1f}℃ / 雨量 {attraction_rain:.1f}mm")
        attraction_feedback_error = get_feedback_error(prediction_history, selected_attraction)
        st.metric('このアトラクションの予測補正', round(attraction_feedback_error, 1))

        pred_all_df = predict_wait_times_for_date(history_df, settings, attraction_target_date, attraction_temperature, attraction_rain, prediction_history, ticket_price, valid_all_df if attraction_target_date == datetime.now(JST).date() else None, all_attractions=attraction_list)
        pred_df = pred_all_df[pred_all_df["Attraction"] == selected_attraction].copy() if len(pred_all_df) > 0 and "Attraction" in pred_all_df.columns else pd.DataFrame()

        if len(pred_df) == 0:
            st.info('このアトラクションの予測データがまだ不足しています。')
        else:
            used_fallback = pred_df.get('状態', pd.Series(dtype=str)).astype(str).str.contains('状態').any() if '状態' in pred_df.columns else False
            attraction_confidence = get_attraction_prediction_confidence(history_df, prediction_history, selected_attraction, pred_df, used_fallback)
            c_att_conf1, c_att_conf2 = st.columns(2)
            with c_att_conf1:
                st.metric('予測信頼度', f"{attraction_confidence['score']}%")
            with c_att_conf2:
                st.metric('信頼度レベル', attraction_confidence["label"])
            st.caption('信頼度の理由: ' + " / ".join([safe_display_text(x, "") for x in attraction_confidence["notes"]]))
            if run_heavy_data_refresh:
                try:
                    save_prediction_rows(cursor, conn, pred_df[["Time", "TimeLabel", "Hour", "Minute", "Predicted Wait", "Attraction"]], selected_attraction)
                except Exception as exc:
                    st.warning(f"このアトラクションの予測データ保存に失敗しました: {exc}")

            trend = get_wait_trend(history_df, selected_attraction, recent_count=5)
            trend_border = "#ff3b30" if abs(float(trend.get("delta", 0) or 0)) >= 20 else "#007aff"
            st.markdown(f"""<div class="card" style="border-left: 6px solid {trend_border};"><h3>{safe_display_text(trend.get('label', '推移'))}</h3><p>{safe_display_text(trend.get('message', '履歴がまだ不足しています'))}</p><p>直近{trend.get('recent_count', 0)}回の変化: {trend.get('delta', 0)}分</p></div>""", unsafe_allow_html=True)
            st.subheader('信頼度レベル')
            alerts_df = get_prediction_alerts(pred_df, attraction_confidence)
            st.dataframe(clean_dataframe(alerts_df), use_container_width=True) if len(alerts_df) > 0 else st.info('大きな注意点はまだありません。')

            fig, ax = plt.subplots(figsize=(10, 5))
            x_values = pred_df["TimeLabel"] if "TimeLabel" in pred_df.columns else pred_df["Hour"]
            ax.plot(x_values, pred_df["Predicted Wait"], linewidth=2.4, label='予測')
            actual_attraction_df = pd.DataFrame()
            if attraction_target_date == datetime.now(JST).date():
                actual_attraction_df = get_actual_wait_series_for_today(history_df, attraction=selected_attraction)
                if len(actual_attraction_df) > 0:
                    ax.plot(actual_attraction_df["TimeLabel"], actual_attraction_df["Actual Wait"], linewidth=2.2, linestyle="--", marker="o", label='実測')
                else:
                    st.caption('今日の実測データはまだありません。')
            else:
                st.caption('未来日のため実測線は表示しません。')
            if "TimeLabel" in pred_df.columns and "Minute" in pred_df.columns:
                tick_df = pred_df[pred_df["Minute"].fillna(0).astype(int) == 0]
                ax.set_xticks(tick_df["TimeLabel"].tolist())
                ax.tick_params(axis="x", rotation=45)
            y_values = pred_df["Predicted Wait"].tolist()
            if len(actual_attraction_df) > 0:
                y_values += actual_attraction_df["Actual Wait"].tolist()
            ax.set_ylim(0, graph_ylim(y_values))
            ax.set_ylabel("待ち時間（分）")
            ax.set_title(f"{selected_attraction} 予測 {attraction_target_date}")
            ax.legend()
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
            best_row = pred_df.sort_values("Predicted Wait").iloc[0]
            peak_row = pred_df.sort_values("Predicted Wait", ascending=False).iloc[0]
            compact_card_grid([("最短予測", f"{best_row.get('TimeLabel', best_row.get('Hour'))} 約{best_row['Predicted Wait']:.0f}分"), ("ピーク予測", f"{peak_row.get('TimeLabel', peak_row.get('Hour'))} 約{peak_row['Predicted Wait']:.0f}分")])
            st.subheader("このアトラクションの予測誤差履歴")
            if len(prediction_history) > 0 and {"attraction", "error"}.issubset(prediction_history.columns):
                one_pred_history = prediction_history[(prediction_history["attraction"] == selected_attraction) & (prediction_history["error"].notna())].copy()
                st.dataframe(clean_dataframe(one_pred_history.tail(30)), use_container_width=True) if len(one_pred_history) > 0 else st.info('このアトラクションの予測データがまだ不足しています。')
            else:
                st.info('このアトラクションの予測誤差履歴')


elif display_mode == "回り方プランナー":

    st.subheader("🗺 回り方プランナー")
    st.caption("予測待ち時間を使って、選んだアトラクションの回る順番を提案します。")

    planner_date = st.date_input(
        "対象日",
        value=datetime.now(JST).date(),
        min_value=datetime.now(JST).date(),
        max_value=datetime.now(JST).date() + timedelta(days=7),
        key="route_planner_date"
    )
    time_labels = [slot["TimeLabel"] for slot in build_time_slots(9, 21, 15)]
    start_label = st.selectbox("開始時刻", time_labels[:-4], index=0, key="route_start")
    end_options = [label for label in time_labels if label > start_label] + ["21:00"]
    end_label = st.selectbox("終了時刻", end_options, index=min(len(end_options) - 1, 8), key="route_end")

    live_choices = set(all_df["Attraction"].dropna().astype(str).tolist()) if "Attraction" in all_df.columns else set()
    history_choices = set(history_df["attraction"].dropna().astype(str).tolist()) if len(history_df) > 0 and "attraction" in history_df.columns else set()
    attraction_choices = sorted(live_choices | history_choices | set(settings.get("rides", [])))
    selected_route_attractions = st.multiselect(
        "行きたいアトラクション",
        attraction_choices,
        default=attraction_choices[:3] if len(attraction_choices) >= 3 else attraction_choices,
        key="route_attractions"
    )

    if st.button("プランを作成", type="primary", use_container_width=True):
        planner_temperature, planner_rain, _ = get_forecast_weather_for_date(
            daily_weather,
            planner_date,
            temperature,
            rain_mm
        )
        planner_pred_df = predict_wait_times_for_date(
            history_df,
            settings,
            planner_date,
            planner_temperature,
            planner_rain,
            prediction_history,
            ticket_price,
            valid_all_df if planner_date == datetime.now(JST).date() else None
        )
        route_df, route_meta = build_optimal_route_plan(
            planner_pred_df,
            selected_route_attractions,
            start_label,
            end_label
        )
        compact_card_grid([
            ("合計予測待ち時間", f"{route_meta['total_wait']:.0f}分"),
            ("予想終了時刻", route_meta["end_time"]),
            ("回れた数", f"{route_meta.get('completed_count', len(route_df))}/{route_meta.get('selected_count', len(selected_route_attractions))}"),
            ("判断理由", route_meta["message"]),
        ])
        st.markdown(format_route_plan_cards(route_df), unsafe_allow_html=True)
        if route_meta.get("skipped"):
            st.warning("時間内に回れない候補: " + " / ".join(route_meta["skipped"]))
        if route_meta.get("filled_attractions"):
            st.info("予測データ不足のため平均値で補完: " + " / ".join(route_meta["filled_attractions"]))
        if route_meta.get("warnings"):
            with st.expander("注意点", expanded=False):
                for warning in route_meta["warnings"]:
                    st.write(warning)

elif display_mode == "DPA/PP予測":

    st.subheader("🎫 DPA売切れ予測")

    st.subheader("外部サイトからDPA売切れ時刻を取得")

    st.caption(
        "urtripのDPA欄から、今日の発行状況と過去の発行終了確認時刻を読み込みます。保存時は同じ日付・アトラクション・取得元の古い行を置き換えます。"
    )

    if dpa_auto_fetch_result["status"] == "success":
        st.success(
            f"DPA自動取得: {dpa_auto_fetch_result['saved_count']}件保存しました。"
            f"{dpa_auto_fetch_result['message']}"
        )
    elif dpa_auto_fetch_result["status"] == "skipped":
        st.info(
            f"DPA自動取得: 今日はすでに確認済みです。"
            f"{dpa_auto_fetch_result['message']}"
        )
    else:
        st.warning(
            f"DPA自動取得に失敗しました: {dpa_auto_fetch_result['message']}"
        )

    if len(dpa_fetch_logs) > 0:
        latest_dpa_fetch = dpa_fetch_logs.sort_values(
            "fetched_at",
            ascending=False
        ).iloc[0]
        st.caption(
            "最新のDPA取得: "
            f"{latest_dpa_fetch['fetched_at']} / "
            f"{latest_dpa_fetch.get('status', '')} / "
            f"{latest_dpa_fetch.get('saved_count', 0)}件"
        )

    col_fetch, col_clear = st.columns(2)

    with col_fetch:
        if st.button("urtripから取得して保存"):
            cached_fetch_urtrip_dpa_sellouts.clear()
            scraped_df, scrape_message = cached_fetch_urtrip_dpa_sellouts(park)

            if len(scraped_df) > 0:
                saved_count = save_dpa_sellout_rows(
                    cursor,
                    conn,
                    scraped_df,
                    "urtrip"
                )
                dpa_sellout_history = load_dpa_sellouts(conn)
                log_dpa_fetch(
                    cursor,
                    conn,
                    park,
                    "urtrip",
                    "manual_success",
                    scrape_message,
                    saved_count
                )
                dpa_fetch_logs = load_dpa_fetch_logs(conn)
                st.success(f"{scrape_message} / 保存 {saved_count}件")
                st.dataframe(
                    scraped_df,
                    use_container_width=True
                )
            else:
                log_dpa_fetch(
                    cursor,
                    conn,
                    park,
                    "urtrip",
                    "manual_failed",
                    scrape_message,
                    0
                )
                dpa_fetch_logs = load_dpa_fetch_logs(conn)
                st.warning(scrape_message)

    with col_clear:
        if st.button("DPA売切れ履歴を全削除"):
            clear_dpa_sellouts(cursor, conn)
            dpa_sellout_history = load_dpa_sellouts(conn)
            st.success("DPA売切れ履歴を削除しました。")

    dpa_rows = []

    for _, row in valid_target_df.iterrows():

        dpa_rows.append(predict_dpa_sellout_time(
            row["Attraction"],
            row["Wait"],
            crowd_10,
            ticket_price,
            today_bonus,
            dpa_sellout_history
        ))

    dpa_df = pd.DataFrame(dpa_rows)

    if len(dpa_df) > 0:

        dpa_df = dpa_df.sort_values(
            "Risk Score",
            ascending=False
        )

        st.dataframe(
            dpa_df,
            use_container_width=True
        )

        st.caption("DPAの実売切れデータは公式APIから直接取れていないため、下の欄で分かった売切れ時刻を保存すると次回以降の時刻予測に使います。")

    else:
        st.info("DPA予測に使えるデータがありません。")

    st.subheader("DPA売切れ時刻の保存")

    with st.form("dpa_sellout_form"):
        sellout_attraction = st.selectbox(
            "売切れを確認したアトラクション",
            settings["rides"]
        )
        sellout_hour = st.slider(
            "売切れ時刻",
            min_value=9.5,
            max_value=20.5,
            value=14.0,
            step=0.25
        )
        submitted = st.form_submit_button("保存")

        if submitted:
            save_dpa_sellout(
                cursor,
                conn,
                sellout_attraction,
                sellout_hour,
                "manual"
            )
            st.success("DPA売切れ時刻を保存しました。")

elif display_mode == "日付指定予測":

    st.subheader("📆 日付・天気から予測")

    target_date = st.date_input(
        "予測する日付",
        value=datetime.now(JST).date(),
        min_value=datetime.now(JST).date(),
        max_value=datetime.now(JST).date() + timedelta(days=30)
    )

    input_weather = st.selectbox(
        "天気",
        ["晴れ", "くもり", "雨"]
    )

    c1, c2 = st.columns(2)

    with c1:
        input_temperature = st.number_input(
            "気温",
            min_value=-5.0,
            max_value=40.0,
            value=float(temperature),
            step=0.5
        )

    with c2:
        input_rain = st.number_input(
            "降水量(mm)",
            min_value=0.0,
            max_value=80.0,
            value=1.0 if input_weather == "雨" else 0.0,
            step=0.5
        )

    target_ticket_price, target_ticket_source = get_ticket_price_from_castel(
        target_date,
        ticket_price_map
    )

    target_crowd, target_wait_df, target_reasons = predict_crowd_index_for_date(
        history_df,
        settings,
        target_date,
        input_temperature,
        input_rain,
        prediction_history,
        daily_prediction_history,
        target_ticket_price,
        valid_all_df if target_date == datetime.now(JST).date() else None,
        event_signals,
        park_hours_df,
        park
    )

    level, color = get_level(target_crowd)
    target_confidence = get_prediction_confidence(
        history_df,
        prediction_history,
        dpa_sellout_history,
        settings,
        target_date,
        "手入力/日付指定"
    )

    target_stats = get_prediction_crowd_stats(target_wait_df)
    target_major_df = target_wait_df[target_wait_df["Attraction"].isin(settings["rides"])] if len(target_wait_df) > 0 else pd.DataFrame()
    target_major_avg = target_major_df["Predicted Wait"].mean() if len(target_major_df) > 0 else 0
    compact_card_grid([
        ("予測混雑指数", f"{format_crowd_index(target_crowd)}/10"),
        ("全体平均予測", f"{target_stats['avg_wait']:.1f}分"),
        ("上位25%平均", f"{target_stats['top_quartile_wait']:.1f}分"),
        ("人気主要アトラクション平均予測", f"{target_major_avg:.1f}分"),
        ("チケット価格", "未取得" if target_ticket_price is None else f"{target_ticket_price}円"),
    ])

    st.metric("予測信頼度", f"{target_confidence['score']}%")
    st.caption("信頼度の理由: " + " / ".join(target_confidence["notes"]))

    st.markdown(
        f"<div class='card'><h2 style='color:{color};'>{level}</h2></div>",
        unsafe_allow_html=True
    )

    st.write("価格取得元:", target_ticket_source)
    st.write("主な理由:", " / ".join(target_reasons))


    st.subheader("🧭 この日のおすすめ行動プラン")
    st.dataframe(
        get_guest_action_plan(
            target_wait_df,
            target_crowd,
            target_ticket_price
        ),
        use_container_width=True
    )

    st.subheader("🩺 この日の予測リスク診断")
    st.dataframe(
        get_prediction_risk_diagnosis(
            history_df,
            prediction_history,
            valid_target_df if target_date == datetime.now(JST).date() else pd.DataFrame(),
            settings,
            target_ticket_price,
            "手入力/日付指定"
        ),
        use_container_width=True
    )
    st.subheader("予測データの健全性")
    st.dataframe(
        get_data_quality_report(
            history_df,
            prediction_history,
            dpa_sellout_history,
            settings
        ),
        use_container_width=True
    )

    st.subheader("時間帯別・アトラクション別待ち時間予測")

    st.dataframe(
        target_wait_df,
        use_container_width=True
    )

    st.subheader("予測の注意点")
    st.dataframe(
        get_prediction_alerts(
            target_wait_df,
            target_confidence
        ),
        use_container_width=True
    )

    dpa_rows = []

    latest_by_attraction = target_wait_df.groupby("Attraction")["Predicted Wait"].max().reset_index()

    for _, row in latest_by_attraction.iterrows():
        dpa_rows.append(predict_dpa_sellout_time(
            row["Attraction"],
            row["Predicted Wait"],
            target_crowd,
            target_ticket_price,
            get_calendar_bonus(target_date, target_ticket_price)[0],
            dpa_sellout_history
        ))

    st.subheader("DPA売切れ予測")

    st.dataframe(
        pd.DataFrame(dpa_rows).sort_values(
            "Risk Score",
            ascending=False
        ),
        use_container_width=True
    )

elif display_mode == "データ管理":

    st.subheader("🗂 データ管理")

    total_count = len(history_df)

    error_only_df = prediction_history[
        prediction_history["error"].notna()
    ].copy() if len(prediction_history) > 0 else pd.DataFrame()

    compact_card_grid([
        ("保存データ数", total_count),
        ("予測データ数", len(prediction_history)),
        ("予測誤差データ", len(error_only_df)),
        ("日別混雑指数予測", len(daily_prediction_history)),
        ("DPA売切れ履歴", len(dpa_sellout_history)),
        ("DPA取得ログ", len(dpa_fetch_logs)),
    ])

    st.caption("表形式の詳細データは、必要な時だけ開けるように折りたたみにまとめています。")
    st.caption("日別混雑指数の誤差は、1日終了後の9:00〜20:59の人気主要5施設実測混雑指数と比較します。")

    today_date = datetime.now(JST).date()
    today_history_df = history_df[history_df["date"] == today_date].copy() if len(history_df) > 0 and "date" in history_df.columns else pd.DataFrame()
    live_total_count = int(len(all_df)) if len(all_df) > 0 else 0
    live_valid_count = int(len(valid_all_df)) if len(valid_all_df) > 0 else 0
    history_total_attractions = int(today_history_df["attraction"].nunique()) if len(today_history_df) > 0 and "attraction" in today_history_df.columns else 0
    history_open_attractions = int(today_history_df[today_history_df.get("is_open", 1) == 1]["attraction"].nunique()) if len(today_history_df) > 0 and "is_open" in today_history_df.columns else history_total_attractions
    history_positive_attractions = int(today_history_df[today_history_df["wait_time"] > 0]["attraction"].nunique()) if len(today_history_df) > 0 and "wait_time" in today_history_df.columns else 0
    compact_card_grid([
        ("現在取得できた全施設", live_total_count),
        ("現在の有効待ち時間施設", live_valid_count),
        ("今日保存済み全施設", history_total_attractions),
        ("今日保存済み営業中施設", history_open_attractions),
        ("今日保存済み待ち時間あり施設", history_positive_attractions),
    ])
    with st.expander("全施設履歴の保存状況", expanded=False):
        st.caption("v31以降は、待ち時間0分や休止中も含めてAPIから取得できた全施設を wait_times に保存します。学習や混雑指数では、営業時間内かつ有効な待ち時間だけを使います。")
        if len(today_history_df) > 0:
            history_check_df = today_history_df.groupby("attraction").agg(
                保存件数=("wait_time", "count"),
                最大待ち時間=("wait_time", "max"),
                営業中記録=("is_open", "sum") if "is_open" in today_history_df.columns else ("wait_time", "count"),
            ).reset_index().sort_values("保存件数", ascending=False)
            st.dataframe(history_check_df, use_container_width=True)
        else:
            st.info("今日の履歴はまだ保存されていません。アプリ起動後の取得タイミングから保存されます。")


    st.subheader("過去待ち時間データ取り込み")
    st.caption("ディズニーリアルの過去ページから、取得できる範囲の待ち時間履歴を取り込みます。画像表だけの日は数値を保存せず、ログに理由を残します。")
    import_range = st.selectbox(
        "取り込み期間",
        ["過去30日", "過去90日", "過去180日", "過去1年"],
        index=0,
        key="historical_import_range"
    )
    range_days_map = {"過去30日": 30, "過去90日": 90, "過去180日": 180, "過去1年": 365}
    import_days = range_days_map.get(import_range, 30)
    max_days = st.number_input(
        "今回処理する最大日数",
        min_value=1,
        max_value=365,
        value=min(30, import_days),
        step=1,
        key="historical_import_max_days"
    )
    hist_start_date = datetime.now(JST).date() - timedelta(days=import_days)
    hist_end_date = datetime.now(JST).date()
    if st.button("過去データを取り込む", type="primary", use_container_width=True):
        with st.spinner("ディズニーリアルから過去待ち時間を取り込み中です。アクセスしすぎないよう、少しずつ処理します。"):
            result = import_disneyreal_history(
                cursor,
                conn,
                park,
                hist_start_date,
                hist_end_date,
                max_days=int(max_days)
            )
        historical_import_logs = load_historical_import_logs(conn)
        history_df = load_history(conn)
        st.success(
            f"処理日数 {result['processed_days']}日 / スキップ {result['skipped_days']}日 / 保存 {result['saved_count']}件"
        )
        if result.get("results"):
            st.dataframe(pd.DataFrame(result["results"]), use_container_width=True)

    if len(historical_import_logs) > 0:
        park_import_logs = historical_import_logs[historical_import_logs.get("park", "") == park].copy()
        imported_days = int(park_import_logs[park_import_logs.get("status", "") == "success"]["target_date"].nunique()) if len(park_import_logs) > 0 else 0
        imported_rows = int(park_import_logs["saved_count"].sum()) if len(park_import_logs) > 0 and "saved_count" in park_import_logs.columns else 0
        compact_card_grid([
            ('信頼度レベル', imported_days),
            ('保存件数', imported_rows),
            ('保存件数', '状態' if len(park_import_logs) > 0 else '状態'),
        ])
        if len(park_import_logs) > 0 and "method" in park_import_logs.columns:
            method_summary = (
                park_import_logs.fillna({"method": "unknown"})
                .groupby("method", as_index=False)
                .agg(days=("target_date", "nunique"), saved_count=("saved_count", "sum"))
                .rename(columns={"method": "取得方法", "days": "日数", "saved_count": "保存件数"})
            )
            method_values = park_import_logs["method"].fillna("").astype(str)
            compact_card_grid([
                ("DisneyReal成功日数", int(method_values.str.startswith("disneyreal_").sum())),
                ("画像OCR成功日数", int((method_values == "disneyreal_image_ocr").sum())),
                ("代替サイト成功日数", int(method_values.str.startswith("alternative_").sum())),
            ])
            with st.expander('取得方法別の件数', expanded=False):
                st.dataframe(method_summary, use_container_width=True)
        with st.expander('過去データ取り込みログ', expanded=False):
            st.dataframe(safe_sort_head(clean_dataframe(park_import_logs), "imported_at", 100, ascending=False), use_container_width=True)
    else:
        st.info('過去待ち時間データの取り込みログはまだありません。')

    with st.expander('アトラクション別の履歴件数と予測誤差', expanded=False):
        if len(history_df) > 0 and "attraction" in history_df.columns:
            history_count_df = history_df.groupby("attraction").agg(
                history_count=("wait_time", "count"),
                history_days=("date", "nunique"),
            ).reset_index().rename(columns={"attraction": 'アトラクション'})
            if "source" in history_df.columns:
                disneyreal_counts = (
                    history_df[history_df["source"].astype(str).str.contains("disneyreal", case=False, na=False)]
                    .groupby("attraction")["wait_time"].count()
                    .reset_index()
                    .rename(columns={"attraction": 'アトラクション', "wait_time": 'ディズニーリアル由来件数'})
                )
                history_count_df = history_count_df.merge(disneyreal_counts, on='アトラクション', how="left")
                history_count_df['ディズニーリアル由来件数'] = history_count_df['ディズニーリアル由来件数'].fillna(0).astype(int)
            history_count_df = history_count_df.rename(columns={"history_count": '保存件数', "history_days": '保存件数'})
            history_count_df['状態'] = history_count_df['保存件数'].apply(lambda x: '予測信頼度' if x < 30 else '保存件数')
            st.dataframe(history_count_df.sort_values('保存件数', ascending=False).head(150), use_container_width=True)
        else:
            st.info('このアトラクションの予測補正')

        if len(prediction_history) > 0 and {"attraction", "error"}.issubset(prediction_history.columns):
            err_df = prediction_history[prediction_history["error"].notna()].copy()
            if len(err_df) > 0:
                err_summary = err_df.groupby("attraction").agg(
                    error_count=("error", "count"),
                    mean_error=("error", "mean"),
                    mean_abs_error=("error", lambda s: s.abs().mean()),
                ).reset_index().rename(columns={
                    "attraction": 'アトラクション',
                    "error_count": '保存件数',
                    "mean_error": '保存件数',
                    "mean_abs_error": '信頼度レベル',
                })
                st.dataframe(err_summary.sort_values('保存件数', ascending=False).head(150), use_container_width=True)
            else:
                st.info('予測誤差はまだ蓄積中です。')

    with st.expander('アトラクション別の履歴件数と予測誤差', expanded=False):
        if len(history_df) > 0 and "attraction" in history_df.columns:
            history_count_df = history_df.groupby("attraction").agg(
                history_count=("wait_time", "count"),
                history_days=("date", "nunique"),
            ).reset_index().rename(columns={"attraction": 'アトラクション'})
            if "source" in history_df.columns:
                disneyreal_counts = (
                    history_df[history_df["source"].astype(str).str.contains("disneyreal", case=False, na=False)]
                    .groupby("attraction")["wait_time"].count()
                    .reset_index()
                    .rename(columns={"attraction": 'アトラクション', "wait_time": 'ディズニーリアル由来件数'})
                )
                history_count_df = history_count_df.merge(disneyreal_counts, on='アトラクション', how="left")
                history_count_df['ディズニーリアル由来件数'] = history_count_df['ディズニーリアル由来件数'].fillna(0).astype(int)
            history_count_df = history_count_df.rename(columns={"history_count": '保存件数', "history_days": '保存件数'})
            history_count_df['状態'] = history_count_df['保存件数'].apply(lambda x: '予測信頼度' if x < 30 else '保存件数')
            st.dataframe(history_count_df.sort_values('保存件数', ascending=False).head(150), use_container_width=True)
        else:
            st.info('このアトラクションの予測補正')

        if len(prediction_history) > 0 and {"attraction", "error"}.issubset(prediction_history.columns):
            err_df = prediction_history[prediction_history["error"].notna()].copy()
            if len(err_df) > 0:
                err_summary = err_df.groupby("attraction").agg(
                    error_count=("error", "count"),
                    mean_error=("error", "mean"),
                    mean_abs_error=("error", lambda s: s.abs().mean()),
                ).reset_index().rename(columns={
                    "attraction": 'アトラクション',
                    "error_count": '保存件数',
                    "mean_error": '保存件数',
                    "mean_abs_error": '信頼度レベル',
                })
                st.dataframe(err_summary.sort_values('保存件数', ascending=False).head(150), use_container_width=True)
            else:
                st.info('予測誤差はまだ蓄積中です。')


    with st.expander("混雑指数デバッグ", expanded=False):
        debug_df = pd.DataFrame([{
            "パーク": crowd_debug.get("park"),
            "混雑指数の計算対象": "人気主要5施設",
            "人気主要アトラクション平均": crowd_debug.get("avg_wait"),
            "人気主要上位25%平均": crowd_debug.get("top_quartile_wait"),
            "人気主要最大待ち時間": crowd_debug.get("max_wait"),
            "人気主要標準偏差": crowd_debug.get("std_wait"),
            "人気主要営業中数": crowd_debug.get("open_count"),
            "人気主要休止数": crowd_debug.get("closed_count"),
            "avg_ratio": crowd_debug.get("avg_ratio"),
            "top_ratio": crowd_debug.get("top_ratio"),
            "max_ratio": crowd_debug.get("max_ratio"),
            "std_ratio": crowd_debug.get("std_ratio"),
            "avg_ratio_clip後": crowd_debug.get("avg_ratio_clipped"),
            "top_ratio_clip後": crowd_debug.get("top_ratio_clipped"),
            "max_ratio_clip後": crowd_debug.get("max_ratio_clipped"),
            "std_ratio_clip後": crowd_debug.get("std_ratio_clipped"),
            "score_offset": crowd_debug.get("score_offset"),
            "park_bias": crowd_debug.get("park_bias"),
            "demand_bonus": crowd_debug.get("demand_bonus"),
            "需要補正値": crowd_debug.get("demand_adjustment"),
            "天気補正値": crowd_debug.get("weather_adjustment"),
            "誤差補正値": crowd_debug.get("feedback_adjustment"),
            "補正前スコア": crowd_debug.get("base_score"),
            "補正後スコア": crowd_debug.get("corrected_score"),
            "最終混雑指数": crowd_debug.get("final_crowd_index"),
            "使用基準": crowd_debug.get("baseline"),
            "全体平均対象数": all_crowd_stats.get("attraction_count"),
            "全体平均対象リスト": " / ".join(all_crowd_stats.get("attraction_names", [])[:80]),
            "人気主要平均対象数": int(len(valid_target_df)) if len(valid_target_df) > 0 else 0,
            "人気主要平均対象リスト": " / ".join(settings.get("rides", [])),
        }])
        st.dataframe(debug_df, use_container_width=True)
        for warn in crowd_debug.get("warnings", []):
            st.warning(warn)
        st.info("現在は、全アトラクション履歴が不足しているため、混雑指数だけ人気主要5施設ベースに戻しています。全体平均は参考値として残しています。")

    with st.expander("ダッシュボードから移動した詳細表", expanded=False):
        st.markdown("#### 人気主要アトラクションの予想平均待ち時間")
        try:
            management_wait_pred_df = predict_wait_times_for_date(
                history_df,
                settings,
                datetime.now(JST).date(),
                temperature,
                rain_mm,
                prediction_history,
                ticket_price,
                valid_all_df
            )
            management_major_df = make_major_average_prediction(
                management_wait_pred_df,
                settings["rides"]
            )
            if len(management_major_df) > 0:
                st.dataframe(
                    management_major_df.rename(
                        columns={
                            "Predicted Wait": "人気主要アトラクション平均待ち時間"
                        }
                    ),
                    use_container_width=True
                )
            else:
                st.info("表示できる人気主要アトラクション予測データがまだありません。")
        except Exception as exc:
            st.warning(f"人気主要アトラクション予測表を作成できませんでした: {exc}")

        st.markdown("#### 1週間混雑指数予測")
        try:
            management_week_df = build_week_forecast(
                history_df,
                settings,
                datetime.now(JST).date(),
                temperature,
                rain_mm,
                prediction_history,
                daily_prediction_history,
                ticket_price_map,
                valid_target_df,
                event_signals,
                park_hours_df,
                park,
                daily_weather,
                cursor=cursor,
                conn=conn,
                use_live_current_data=False
            )
            if len(management_week_df) > 0:
                st.dataframe(management_week_df, use_container_width=True)
            else:
                st.info("1週間予測データがまだありません。")
        except Exception as exc:
            st.warning(f"1週間予測表を作成できませんでした: {exc}")

        st.markdown("#### 営業時間データ")
        if len(park_hours_df) > 0:
            st.dataframe(
                safe_sort_head(park_hours_df, "date", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("営業時間データはまだ保存されていません。")

        st.markdown("#### イベント・休暇シグナル")
        if len(event_signals) > 0:
            st.dataframe(
                safe_sort_head(event_signals, "date", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("イベント・休暇シグナルはまだ保存されていません。")

        st.markdown("#### アトラクション営業状態履歴")
        if len(attraction_status_snapshots) > 0:
            st.dataframe(
                safe_sort_head(attraction_status_snapshots, "observed_at", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("アトラクション営業状態履歴はまだ保存されていません。")


        st.markdown("#### ショー/パレード時刻履歴")
        if len(show_schedules) > 0:
            st.dataframe(
                safe_sort_head(show_schedules, "observed_at", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("ショー/パレード時刻履歴はまだ保存されていません。")

        st.markdown("#### ショー前後の待ち時間関係データ")
        if len(show_wait_context) > 0:
            st.dataframe(
                safe_sort_head(show_wait_context, "observed_at", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("ショー前後の待ち時間関係データはまだ保存されていません。")
        st.markdown("#### 保存済みDPA売切れ履歴")
        if len(dpa_sellout_history) > 0:
            st.dataframe(
                safe_sort_head(dpa_sellout_history, "observed_at", 100, ascending=False),
                use_container_width=True
            )
        else:
            st.info("DPA売切れ履歴はまだ保存されていません。")

    if len(history_df) > 0:

        st.subheader("アトラクション別保存データ")

        attraction_summary = history_df.groupby(
            "attraction"
        )["wait_time"].agg(
            ["count", "mean", "max"]
        ).reset_index()

        attraction_summary.columns = [
            "Attraction",
            "保存数",
            "平均待ち時間",
            "最大待ち時間"
        ]

        st.dataframe(
            attraction_summary,
            use_container_width=True
        )

    st.subheader("予測データの健全性")

    st.dataframe(
        get_data_quality_report(
            history_df,
            prediction_history,
            dpa_sellout_history,
            settings
        ),
        use_container_width=True
    )

    st.subheader("次に増やすべき機能と必要データ")
    st.subheader("予測精度レポート")
    st.dataframe(
        get_prediction_accuracy_report(
            prediction_history
        ),
        use_container_width=True
    )

    st.dataframe(
        get_next_feature_plan(),
        use_container_width=True
    )

    st.subheader("営業状態サマリー")
    st.dataframe(
        get_attraction_status_summary(
            attraction_status_snapshots,
            settings
        ),
        use_container_width=True
    )

    st.subheader("自動データ取得")
    st.write("今日の取得状態:", " / ".join(auto_context_results))

    if len(data_fetch_logs) > 0:
        st.dataframe(
            safe_sort_head(data_fetch_logs, "fetched_at", 100, ascending=False),
            use_container_width=True
        )

    if len(weather_snapshots) > 0:
        st.subheader("天気スナップショット")
        st.dataframe(
            safe_sort_head(weather_snapshots, "observed_at", 100, ascending=False),
            use_container_width=True
        )

    if len(ticket_price_snapshots) > 0:
        st.subheader("チケット価格スナップショット")
        st.dataframe(
            safe_sort_head(ticket_price_snapshots, "observed_at", 100, ascending=False),
            use_container_width=True
        )

    if len(error_only_df) > 0:

        st.subheader("アトラクション別 予測誤差")

        error_summary = error_only_df.groupby(
            "attraction"
        )["error"].agg(
            ["count", "mean", "max", "min"]
        ).reset_index()

        error_summary.columns = [
            "Attraction",
            "誤差データ数",
            "平均誤差",
            "最大誤差",
            "最小誤差"
        ]

        st.dataframe(
            error_summary,
            use_container_width=True
        )

        st.subheader("最近の予測誤差データ")

        st.dataframe(
            safe_sort_head(error_only_df, "created_at", 100, ascending=False),
            use_container_width=True
        )

    st.subheader("予測誤差がない理由")

    st.dataframe(
        get_prediction_gap_summary(prediction_history),
        use_container_width=True
    )

    if len(daily_prediction_history) > 0:
        st.subheader("日別混雑指数の予測誤差")
        st.dataframe(
            safe_sort_head(daily_prediction_history, "created_at", 100, ascending=False),
            use_container_width=True
        )

if display_mode == "データ管理":
    with st.expander("システム情報", expanded=False):
        st.write("選択中:", park)
        st.write("表示モード:", display_mode)
        st.write("保存データ:", len(history_df))
        st.write("予測データ:", len(prediction_history))
        st.write("予測補正:", round(global_feedback_error, 1))
        st.write(
            "最終更新:",
            datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S") + "（日本時間）"
        )

st.caption('必要なときだけ手動更新できます。過去データ取り込みやOCRはボタンを押した時だけ実行します。')

if st.button('取得方法別の件数', key="manual_refresh_bottom", use_container_width=True):
    st.rerun()
