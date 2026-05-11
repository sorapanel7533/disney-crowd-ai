import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from utils import (
    GLOBAL_PREDICTION_NAME,
    PARK_SETTINGS,
    auto_collect_prediction_context,
    auto_save_context_data,
    auto_fetch_dpa_if_needed,
    connect_db,
    clear_dpa_sellouts,
    fetch_ticket_prices,
    fetch_urtrip_dpa_sellouts,
    fetch_wait_times,
    get_calendar_bonus,
    get_crowd_index,
    get_current_stats,
    get_data_quality_report,
    get_dpa_score,
    get_feedback_error,
    get_forecast_weather_for_date,
    get_level,
    format_crowd_index,
    make_x_post_summary,
    get_next_feature_plan,
    get_attraction_status_summary,
    get_event_bonus,
    get_park_hours_bonus,
    get_prediction_accuracy_report,
    get_prediction_alerts,
    get_prediction_confidence,
    get_area_crowd_map,
    get_emptying_candidates,
    get_guest_action_plan,
    get_prediction_risk_diagnosis,
    get_show_wait_insights,
    load_show_schedules,
    load_show_wait_context,
    save_show_wait_context,
    get_historical_crowd_rank,
    get_prediction_gap_summary,
    get_wait_trend,
    get_ticket_price_from_castel,
    get_today_stats,
    get_valid_open_df,
    get_weather,
    get_weather_score,
    load_daily_crowd_predictions,
    load_data_fetch_logs,
    load_dpa_fetch_logs,
    load_dpa_sellouts,
    load_attraction_status_snapshots,
    load_event_signals,
    load_park_hours,
    load_ticket_price_snapshots,
    load_weather_snapshots,
    log_dpa_fetch,
    load_history,
    load_prediction_history,
    make_major_average_prediction,
    make_locked_week_forecast as build_week_forecast,
    make_action_advice,
    predict_crowd_index_for_date,
    predict_dpa_sellout_time,
    predict_wait_times_for_date,
    save_daily_crowd_prediction,
    save_dpa_sellout,
    save_dpa_sellout_rows,
    save_attraction_status_snapshots,
    predict_dpa_risk,
    save_prediction_rows,
    save_wait_times,
    update_daily_crowd_feedback,
    update_prediction_feedback,
)

JST = ZoneInfo("Asia/Tokyo")

OPEN_HOUR = 9
CROWD_END_HOUR = 21

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

CUSTOM_CSS = (
    "<style>\n"
    "html, body, [class*=\"css\"] {\n"
    "    font-family: -apple-system, BlinkMacSystemFont, \"SF Pro Display\", \"SF Pro Text\",\n"
    "        \"Hiragino Sans\", \"Yu Gothic\", \"Meiryo\", sans-serif !important;\n"
    "}\n"
    ".stApp {\n"
    "    background: #f5f5f7;\n"
    "    color: #1d1d1f;\n"
    "}\n"
    ".block-container {\n"
    "    max-width: 980px;\n"
    "    padding-top: 1.1rem;\n"
    "    padding-left: 1rem;\n"
    "    padding-right: 1rem;\n"
    "    padding-bottom: 5rem;\n"
    "}\n"
    "h1, h2, h3, h4, h5, h6, p, label, span, div {\n"
    "    color: #1d1d1f !important;\n"
    "    letter-spacing: 0 !important;\n"
    "}\n"
    "h1 {\n"
    "    font-size: 30px !important;\n"
    "    font-weight: 800 !important;\n"
    "}\n"
    "h2, h3 {\n"
    "    font-weight: 750 !important;\n"
    "    margin-top: 1.1rem !important;\n"
    "}\n"
    ".stMarkdown, .stDataFrame, .stPlotlyChart, .stPyplot, .stAlert, .stForm {\n"
    "    max-width: 100%;\n"
    "}\n"
    "[data-testid=\"stMetric\"] {\n"
    "    background: rgba(255, 255, 255, 0.92);\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    padding: 16px 15px;\n"
    "    border-radius: 22px;\n"
    "    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);\n"
    "    min-height: 104px;\n"
    "}\n"
    "[data-testid=\"stMetricLabel\"] {\n"
    "    color: #6e6e73 !important;\n"
    "    font-size: 13px !important;\n"
    "    font-weight: 650 !important;\n"
    "}\n"
    "[data-testid=\"stMetricValue\"] {\n"
    "    color: #111111 !important;\n"
    "    font-size: 27px !important;\n"
    "    font-weight: 800 !important;\n"
    "}\n"
    ".title-card {\n"
    "    background: rgba(255, 255, 255, 0.94);\n"
    "    padding: 22px;\n"
    "    border-radius: 28px;\n"
    "    margin-bottom: 16px;\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.10);\n"
    "}\n"
    ".title-card h1 {\n"
    "    color: #111111 !important;\n"
    "    font-size: 34px;\n"
    "    line-height: 1.12;\n"
    "    margin-bottom: 8px;\n"
    "    text-shadow: none;\n"
    "}\n"
    ".title-card p {\n"
    "    color: #6e6e73 !important;\n"
    "    font-size: 15px;\n"
    "    margin: 0;\n"
    "}\n"
    ".card {\n"
    "    background: rgba(255, 255, 255, 0.92);\n"
    "    padding: 18px;\n"
    "    border-radius: 22px;\n"
    "    margin-bottom: 16px;\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);\n"
    "}\n"
    ".rank-card {\n"
    "    background: rgba(255, 255, 255, 0.94);\n"
    "    padding: 15px;\n"
    "    border-radius: 20px;\n"
    "    margin-bottom: 10px;\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    border-left: 5px solid #34c759;\n"
    "    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.07);\n"
    "    color: #1d1d1f;\n"
    "}\n"
    ".advice-card {\n"
    "    background: rgba(255, 255, 255, 0.94);\n"
    "    padding: 16px;\n"
    "    border-radius: 20px;\n"
    "    margin-bottom: 10px;\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    border-left: 5px solid #007aff;\n"
    "    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.07);\n"
    "    color: #1d1d1f;\n"
    "}\n"
    ".compact-grid {\n"
    "    display: grid;\n"
    "    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));\n"
    "    gap: 8px;\n"
    "    margin: 4px 0 12px;\n"
    "}\n"
    ".compact-card {\n"
    "    background: rgba(255, 255, 255, 0.94);\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "    border-radius: 14px;\n"
    "    padding: 8px 10px;\n"
    "    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);\n"
    "    min-height: 54px;\n"
    "}\n"
    ".compact-label {\n"
    "    color: #6e6e73 !important;\n"
    "    font-size: 12px;\n"
    "    font-weight: 700;\n"
    "    line-height: 1.15;\n"
    "}\n"
    ".compact-value {\n"
    "    color: #1d1d1f !important;\n"
    "    font-size: 14px;\n"
    "    font-weight: 750;\n"
    "    line-height: 1.25;\n"
    "    margin-top: 3px;\n"
    "    word-break: break-word;\n"
    "}\n"
    "div[data-baseweb=\"select\"] {\n"
    "    background: rgba(255, 255, 255, 0.96) !important;\n"
    "    border-radius: 18px !important;\n"
    "    border: 1px solid rgba(60, 60, 67, 0.18) !important;\n"
    "    box-shadow: 0 5px 18px rgba(0, 0, 0, 0.07);\n"
    "    min-height: 46px;\n"
    "}\n"
    "div[data-baseweb=\"select\"] span {\n"
    "    color: #1d1d1f !important;\n"
    "    font-weight: 600 !important;\n"
    "}\n"
    "div[data-baseweb=\"select\"] svg {\n"
    "    color: #007aff !important;\n"
    "}\n"
    "div[data-baseweb=\"popover\"] {\n"
    "    background-color: #ffffff !important;\n"
    "    border-radius: 18px !important;\n"
    "    box-shadow: 0 16px 38px rgba(0, 0, 0, 0.16) !important;\n"
    "}\n"
    "div[role=\"option\"] {\n"
    "    color: #1d1d1f !important;\n"
    "    background-color: #ffffff !important;\n"
    "}\n"
    "div[role=\"option\"]:hover {\n"
    "    background-color: #f2f2f7 !important;\n"
    "}\n"
    "thead tr th {\n"
    "    color: #1d1d1f !important;\n"
    "}\n"
    "tbody tr td {\n"
    "    color: #1d1d1f !important;\n"
    "}\n"
    "[data-testid=\"stDataFrame\"],\n"
    "[data-testid=\"stTable\"],\n"
    "[data-testid=\"stAlert\"],\n"
    "[data-testid=\"stExpander\"],\n"
    "[data-testid=\"stForm\"] {\n"
    "    border-radius: 22px !important;\n"
    "    overflow: hidden;\n"
    "}\n"
    "button[kind=\"primary\"], button[kind=\"secondary\"], .stButton > button {\n"
    "    width: 100%;\n"
    "    min-height: 44px;\n"
    "    border-radius: 999px !important;\n"
    "    background: #007aff !important;\n"
    "    color: white !important;\n"
    "    border: none !important;\n"
    "    box-shadow: 0 6px 16px rgba(0, 122, 255, 0.24);\n"
    "    font-weight: 700 !important;\n"
    "}\n"
    ".stButton > button p,\n"
    ".stButton > button span {\n"
    "    color: white !important;\n"
    "}\n"
    "input, textarea {\n"
    "    border-radius: 16px !important;\n"
    "}\n"
    "[data-testid=\"stImage\"],\n"
    "[data-testid=\"stPyplot\"] {\n"
    "    background: #ffffff;\n"
    "    border-radius: 22px;\n"
    "    padding: 10px;\n"
    "    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);\n"
    "    border: 1px solid rgba(60, 60, 67, 0.12);\n"
    "}\n"
    "[data-testid=\"stPyplot\"] img,\n"
    "[data-testid=\"stImage\"] img {\n"
    "    width: 100% !important;\n"
    "    height: auto !important;\n"
    "}\n"
    ".stCaptionContainer, .stCaptionContainer p {\n"
    "    color: #6e6e73 !important;\n"
    "}\n"
    "@media (max-width: 760px) {\n"
    "    .block-container {\n"
    "        padding-left: 0.75rem;\n"
    "        padding-right: 0.75rem;\n"
    "        padding-top: 0.75rem;\n"
    "    }\n"
    "    [data-testid=\"stHorizontalBlock\"] {\n"
    "        flex-direction: column !important;\n"
    "        gap: 0.75rem !important;\n"
    "    }\n"
    "    [data-testid=\"column\"] {\n"
    "        width: 100% !important;\n"
    "        flex: 1 1 100% !important;\n"
    "        min-width: 100% !important;\n"
    "    }\n"
    "    .title-card {\n"
    "        padding: 18px;\n"
    "        border-radius: 24px;\n"
    "    }\n"
    "    .title-card h1 {\n"
    "        font-size: 28px;\n"
    "    }\n"
    "    [data-testid=\"stMetric\"] {\n"
    "        min-height: 92px;\n"
    "        padding: 14px;\n"
    "    }\n"
    "    [data-testid=\"stMetricValue\"] {\n"
    "        font-size: 24px !important;\n"
    "    }\n"
    "    h1 {\n"
    "        font-size: 27px !important;\n"
    "    }\n"
    "    h2, h3 {\n"
    "        font-size: 21px !important;\n"
    "    }\n"
    "    [data-testid=\"stDataFrame\"] {\n"
    "        font-size: 13px;\n"
    "    }\n"
    "}\n"
    "</style>\n"
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


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
            f'<div class="compact-label">{label}</div>'
            f'<div class="compact-value">{value}</div>'
            '</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def safe_sort_head(df, sort_column, n=100, ascending=False):
    if df is None or len(df) == 0:
        return pd.DataFrame()

    display_df = df.copy()
    if sort_column in display_df.columns:
        display_df = display_df.sort_values(sort_column, ascending=ascending)

    return display_df.head(n)
def filter_crowd_hours(df):
    if len(df) == 0 or "hour" not in df.columns:
        return df

    return df[
        (df["hour"] >= OPEN_HOUR)
        &
        (df["hour"] < CROWD_END_HOUR)
    ].copy()


now_display = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

st.markdown(f"""
<div class="title-card">
<h1>🏰 ディズニー混雑AI</h1>
<p>東京ディズニーリゾート混雑分析システム</p>
<p>最終更新: {now_display}（日本時間）</p>
</div>
""", unsafe_allow_html=True)

park = st.selectbox(
    "🏰 パークを選択",
    ["DisneySea", "Disneyland"],
    key="park"
)

settings = PARK_SETTINGS[park]

display_mode = st.selectbox(
    "🧭 表示モードを選択",
    [
        "ダッシュボード",
        "全アトラクション",
        "アトラクション別予測",
        "DPA売切れ予測",
        "日付指定予測",
        "データ管理"
    ],
    key="display_mode"
)

ticket_price_map, ticket_map_source = cached_fetch_ticket_prices()

ticket_price, ticket_source = get_ticket_price_from_castel(
    datetime.now(JST),
    ticket_price_map
)

today_bonus, today_reasons = get_calendar_bonus(
    datetime.now(JST),
    ticket_price
)
temperature, rain_mm, weather_text, hourly_weather, daily_weather = get_weather()

conn, cursor = connect_db(settings["db"])
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
data_fetch_logs = load_data_fetch_logs(conn)
weather_snapshots = load_weather_snapshots(conn)
ticket_price_snapshots = load_ticket_price_snapshots(conn)
park_hours_df = load_park_hours(conn)
event_signals = load_event_signals(conn)
show_schedules = load_show_schedules(conn)
show_wait_context = load_show_wait_context(conn)

try:
    all_df, target_df = fetch_wait_times(settings)

except Exception:
    st.error("待ち時間データ取得失敗")
    st.stop()

if all_df.empty:
    st.error("アトラクションのデータが取得できませんでした")
    st.stop()

save_attraction_status_snapshots(
    cursor,
    conn,
    park,
    all_df,
    settings["rides"]
)
attraction_status_snapshots = load_attraction_status_snapshots(conn)

valid_all_df = get_valid_open_df(all_df)
valid_target_df = get_valid_open_df(target_df)
today_show_schedules = show_schedules[
    show_schedules.get("target_date", "") == str(datetime.now(JST).date())
].copy() if len(show_schedules) > 0 else pd.DataFrame()
save_show_wait_context(cursor, conn, park, today_show_schedules, valid_target_df)
show_wait_context = load_show_wait_context(conn)

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

if len(valid_all_df) == 0:
    st.warning("現在は営業中の有効な待ち時間データがありません。")

prediction_history = load_prediction_history(conn)
daily_prediction_history = load_daily_crowd_predictions(conn)
dpa_sellout_history = load_dpa_sellouts(conn)
dpa_auto_fetch_result = auto_fetch_dpa_if_needed(
    cursor,
    conn,
    settings,
    park
)
dpa_sellout_history = load_dpa_sellouts(conn)
dpa_fetch_logs = load_dpa_fetch_logs(conn)

global_feedback_error = get_feedback_error(
    prediction_history,
    GLOBAL_PREDICTION_NAME
)

update_prediction_feedback(
    cursor,
    conn,
    valid_target_df,
    current_target_avg_wait
)

prediction_history = load_prediction_history(conn)

update_daily_crowd_feedback(
    cursor,
    conn,
    history_df,
    settings
)

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

dpa = get_dpa_score(avg_wait, max_wait)

weather_score = get_weather_score(
    weather_text,
    rain_mm,
    temperature
)

crowd_10 = get_crowd_index(
    avg_wait,
    max_wait,
    var_wait,
    dpa,
    weather_score,
    global_feedback_error,
    today_bonus
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
    valid_all_df,
    temperature,
    rain_mm
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

    if len(today_reasons) > 0:
        st.write("補正理由:", " / ".join(today_reasons))

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

    for advice in advice_list:

        st.markdown(
            f"""
            <div class="advice-card">
            {advice}
            </div>
            """,
            unsafe_allow_html=True
        )

    st.subheader("🎢 5大アトラクション")

    display_df = target_df.copy()

    display_df["Status"] = display_df["Open"].apply(
        lambda x: "🟢 OPEN" if x else "⚫ CLOSED"
    )

    st.dataframe(
        display_df[
            ["Attraction", "Wait", "Status"]
        ],
        use_container_width=True
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

    m1, m2, m3, m4 = st.columns(4)

    with m1:
        st.metric("5大平均待ち時間", round(avg_wait, 1))

    with m2:
        st.metric("5大最大待ち時間", round(max_wait, 1))

    with m3:
        st.metric("混雑指数", format_crowd_index(crowd_10))

    with m4:
        st.metric("5大予測補正", round(global_feedback_error, 1))

    st.caption(
        f"混雑指数は5大アトラクションのみを対象に、9:00〜20:59までのデータだけで算出しています。現在の参照元: {crowd_source}"
    )

    today_confidence = get_prediction_confidence(
        history_df,
        prediction_history,
        dpa_sellout_history,
        settings,
        datetime.now(JST).date(),
        "現在天気"
    )

    c_conf1, c_conf2 = st.columns(2)
    with c_conf1:
        st.metric("予測信頼度", f"{today_confidence['score']}%")
    with c_conf2:
        st.metric("信頼度レベル", today_confidence["label"])
    st.caption("信頼度の理由: " + " / ".join(today_confidence["notes"]))

    level, color = get_level(crowd_10)

    st.markdown(
        f"""
        <div class="card">
        <h1 style='color:{color};'>
        {level}
        </h1>
        </div>
        """,
        unsafe_allow_html=True
    )


    st.subheader("🎭 今日のショー/パレード")
    if len(today_show_schedules) > 0:
        st.dataframe(
            safe_sort_head(today_show_schedules, "show_time", 30, ascending=True)[
                ["show_time", "show_name", "category", "note"]
            ] if set(["show_time", "show_name", "category", "note"]).issubset(today_show_schedules.columns) else today_show_schedules,
            use_container_width=True
        )
    else:
        st.info("今日のショー/パレード時刻はまだ取得できていません。公式ページ取得に失敗した場合も、アプリはそのまま動きます。")

    st.subheader("🎯 ショー前後の待ち時間メモ")
    st.dataframe(
        get_show_wait_insights(show_wait_context),
        use_container_width=True
    )
    st.subheader("🤖 5大アトラクションの予想平均待ち時間")

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
        valid_target_df
    )

    major_pred_df = make_major_average_prediction(wait_pred_df)
    major_display_df = major_pred_df.rename(
        columns={"Predicted Wait": "5大予想平均待ち時間"}
    )

    if len(major_pred_df) > 0:
        save_prediction_rows(
            cursor,
            conn,
            major_pred_df,
            GLOBAL_PREDICTION_NAME
        )

        for attraction in settings["rides"]:
            one_pred_df = wait_pred_df[
                wait_pred_df["Attraction"] == attraction
            ][["Hour", "Predicted Wait"]]

            if len(one_pred_df) > 0:
                save_prediction_rows(
                    cursor,
                    conn,
                    one_pred_df,
                    attraction
                )


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
            valid_target_df if x_target_date == x_base_now.date() else None,
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
        st.subheader("\U0001f4dd 21\u6642\u6295\u7a3f\u7528 X\u6587\u9762 v22")
        st.code(x_post_text, language="text")
        st.text_area(
            "\u6295\u7a3f\u6587\u30b3\u30d4\u30fc\u7528\uff08v22\u30fb\u81ea\u52d5\u66f4\u65b0\uff09",
            value=x_post_text,
            height=150,
            key=f"x_post_text_v22_{park}_{x_target_date}_{format_crowd_index(x_crowd)}"
        )
        st.caption("v22: Streamlit\u306e\u53e4\u3044\u5165\u529b\u72b6\u614b\u3092\u907f\u3051\u308b\u305f\u3081\u3001\u4e0a\u306b\u6700\u65b0\u751f\u6210\u6587\u3092\u56fa\u5b9a\u8868\u793a\u3057\u3066\u3044\u307e\u3059\u3002\u5929\u6c17\u30fb\u4fa1\u683c\u30fb\u55b6\u696d\u6642\u9593\u306f\u5165\u308c\u307e\u305b\u3093\u3002")
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

        ax.plot(
            major_display_df["Hour"],
            major_display_df["5大予想平均待ち時間"],
            marker="o"
        )

        ax.set_ylim(
            0,
            graph_ylim(major_display_df["5大予想平均待ち時間"].tolist())
        )

        ax.set_ylabel("Predicted Wait")
        ax.set_title(f"{park} Major Attractions Average Prediction")

        st.pyplot(fig)

    else:
        st.info("5大アトラクション平均予測には、9:00〜20:59の履歴データがもう少し必要です。")


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

    st.subheader("📅 1週間混雑指数予測")

    week_df = build_week_forecast(
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

    fig_week, ax_week = plt.subplots(figsize=(10, 4))

    ax_week.plot(
        week_df["Date"],
        week_df["Crowd Index"],
        marker="o"
    )

    ax_week.set_ylim(0, 10)
    ax_week.set_ylabel("Crowd Index")
    ax_week.set_title(f"{park} 1 Week Crowd Forecast")

    st.pyplot(fig_week)
elif display_mode == "全アトラクション":

    st.subheader("🎡 全アトラクション待ち時間")

    all_display = all_df.copy()

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

    st.subheader("📈 アトラクション別 履歴グラフ")

    all_attraction_list = sorted(
        all_df["Attraction"].unique()
    )

    selected_history_attraction = st.selectbox(
        "🎢 グラフで見るアトラクションを選択",
        all_attraction_list
    )

    graph_mode = st.selectbox(
        "📊 グラフの種類を選択",
        [
            "保存データそのまま",
            "日付ごとの平均",
            "時間帯ごとの平均"
        ]
    )

    if len(history_df) > 0:

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

            st.subheader("予測の注意点")
            st.dataframe(
                get_prediction_alerts(
                    pred_df,
                    attraction_confidence
                ),
                use_container_width=True
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

    st.subheader("🎢 アトラクション別予測")

    if len(history_df) > 0:

        attraction_list = sorted(
            history_df["attraction"].unique()
        )

        selected_attraction = st.selectbox(
            "🎢 予測するアトラクションを選択",
            attraction_list
        )

        attraction_target_date = st.date_input(
            "予測する日付",
            value=datetime.now(JST).date() + timedelta(days=1),
            min_value=datetime.now(JST).date(),
            max_value=datetime.now(JST).date() + timedelta(days=7),
            key="attraction_prediction_date"
        )

        attraction_temperature, attraction_rain, attraction_weather_source = get_forecast_weather_for_date(
            daily_weather,
            attraction_target_date,
            temperature,
            rain_mm
        )

        st.caption(
            f"{attraction_target_date} の予測です。"
            f"天気: {attraction_weather_source} / "
            f"気温 {attraction_temperature:.1f}℃ / "
            f"降水量 {attraction_rain:.1f}mm"
        )

        attraction_feedback_error = get_feedback_error(
            prediction_history,
            selected_attraction
        )

        st.metric(
            "このアトラクションの予測補正",
            round(attraction_feedback_error, 1)
        )

        pred_all_df = predict_wait_times_for_date(
            history_df,
            settings,
            attraction_target_date,
            attraction_temperature,
            attraction_rain,
            prediction_history,
            ticket_price,
            valid_target_df if attraction_target_date == datetime.now(JST).date() else None
        )

        pred_df = pred_all_df[
            pred_all_df["Attraction"] == selected_attraction
        ].copy()

        if len(pred_df) > 0:
            attraction_confidence = get_prediction_confidence(
                history_df,
                prediction_history,
                dpa_sellout_history,
                settings,
                attraction_target_date,
                attraction_weather_source
            )

            c_att_conf1, c_att_conf2 = st.columns(2)
            with c_att_conf1:
                st.metric("予測信頼度", f"{attraction_confidence['score']}%")
            with c_att_conf2:
                st.metric("信頼度レベル", attraction_confidence["label"])
            st.caption("信頼度の理由: " + " / ".join(attraction_confidence["notes"]))

            save_prediction_rows(
                cursor,
                conn,
                pred_df[["Hour", "Predicted Wait"]],
                selected_attraction
            )

            trend = get_wait_trend(
                history_df,
                selected_attraction,
                recent_count=5
            )
            trend_border = "#ff3b30" if "急" in trend["label"] else "#007aff"
            st.markdown(
                f"""
                <div class="card" style="border-left: 6px solid {trend_border};">
                <h3>{trend['label']}</h3>
                <p>{trend['message']}</p>
                <p>直近{trend['recent_count']}件の変化: {trend['delta']}分</p>
                </div>
                """,
                unsafe_allow_html=True
            )

            st.subheader("予測の注意点")
            st.dataframe(
                get_prediction_alerts(
                    pred_df,
                    attraction_confidence
                ),
                use_container_width=True
            )


            fig, ax = plt.subplots(
                figsize=(10, 5)
            )

            ax.plot(
                pred_df["Hour"],
                pred_df["Predicted Wait"],
                marker="o"
            )

            ax.set_ylim(
                0,
                graph_ylim(pred_df["Predicted Wait"].tolist())
            )

            ax.set_ylabel("Predicted Wait")

            ax.set_title(
                f"{selected_attraction} Prediction {attraction_target_date}"
            )

            st.pyplot(fig)

            if len(prediction_history) > 0:
                st.subheader("🧠 このアトラクションの予測誤差履歴")

                one_pred_history = prediction_history[
                    (prediction_history["attraction"] == selected_attraction)
                    &
                    (prediction_history["error"].notna())
                ].copy()

                if len(one_pred_history) > 0:
                    st.dataframe(
                        one_pred_history.tail(30),
                        use_container_width=True
                    )
                else:
                    st.info("このアトラクションの誤差データはまだありません。未来の予測、対象時刻の実測未取得、休止・閉園・待ち時間0分などの場合は誤差を作れません。")

        else:
            st.info("このアトラクションの履歴がまだ足りません。")

    else:
        st.info("履歴データがまだありません。")

elif display_mode == "DPA売切れ予測":

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
        valid_target_df if target_date == datetime.now(JST).date() else None,
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

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric("予測混雑指数", format_crowd_index(target_crowd))

    with m2:
        st.metric(
            "5大予想平均待ち時間",
            round(target_wait_df["Predicted Wait"].mean(), 1) if len(target_wait_df) > 0 else 0
        )

    with m3:
        st.metric(
            "チケット価格",
            "未取得" if target_ticket_price is None else f"{target_ticket_price}円"
        )

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

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric("保存データ数", total_count)

    with m2:
        st.metric(
            "予測データ数",
            len(prediction_history)
        )

    with m3:
        st.metric(
            "予測誤差データ",
            len(error_only_df)
        )

    m4, m5 = st.columns(2)

    with m4:
        st.metric(
            "日別混雑指数予測",
            len(daily_prediction_history)
        )

    with m5:
        st.metric(
            "DPA売切れ履歴",
            len(dpa_sellout_history)
        )

    st.metric("DPA取得ログ", len(dpa_fetch_logs))

    st.caption("表形式の詳細データは、必要な時だけ開けるように折りたたみにまとめています。")

    with st.expander("ダッシュボードから移動した詳細表", expanded=False):
        st.markdown("#### 5大アトラクションの予想平均待ち時間")
        try:
            management_wait_pred_df = predict_wait_times_for_date(
                history_df,
                settings,
                datetime.now(JST).date(),
                temperature,
                rain_mm,
                prediction_history,
                ticket_price,
                valid_target_df
            )
            management_major_df = make_major_average_prediction(
                management_wait_pred_df
            )
            if len(management_major_df) > 0:
                st.dataframe(
                    management_major_df.rename(
                        columns={
                            "Predicted Wait": "5大アトラクション平均待ち時間"
                        }
                    ),
                    use_container_width=True
                )
            else:
                st.info("表示できる5大アトラクション予測データがまだありません。")
        except Exception as exc:
            st.warning(f"5大アトラクション予測表を作成できませんでした: {exc}")

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

st.subheader("⚙ システム")

st.write("選択中:", park)

st.write("表示モード:", display_mode)

st.write("保存データ:", len(history_df))

st.write("予測データ:", len(prediction_history))

st.write("5大予測補正:", round(global_feedback_error, 1))

st.write(
    "最終更新:",
    datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S") + "（日本時間）"
)

refresh_seconds = 900

st.caption(
    f"🔄 {refresh_seconds // 60}分ごと自動更新"
)

if os.environ.get("STREAMLIT_DISABLE_AUTO_REFRESH") != "1":
    time.sleep(refresh_seconds)
    st.rerun()
