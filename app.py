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
    get_level,
    get_next_feature_plan,
    get_attraction_status_summary,
    get_event_bonus,
    get_park_hours_bonus,
    get_prediction_gap_summary,
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
    make_week_forecast as build_week_forecast,
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

st.markdown("""
<style>
.stApp {
    background-color: #0f1117;
}
.block-container {
    padding-top: 2rem;
}
h1, h2, h3, h4, p, label {
    color: #f5f7ff !important;
}
[data-testid="stMetric"] {
    background-color: #1c1f2b;
    border: 1px solid #3b4260;
    padding: 18px;
    border-radius: 18px;
}
[data-testid="stMetricLabel"] {
    color: #e2e8ff !important;
}
[data-testid="stMetricValue"] {
    color: #ffffff !important;
}
.title-card {
    background: linear-gradient(135deg, #143b6d, #1f6feb);
    padding: 28px;
    border-radius: 22px;
    margin-bottom: 18px;
    border: 1px solid #6aa4ff;
    box-shadow: 0 8px 28px rgba(0,0,0,0.5);
}
.title-card h1 {
    color: #ffffff !important;
    font-size: 42px;
    margin-bottom: 8px;
    text-shadow: 0 3px 12px rgba(0,0,0,0.7);
}
.title-card p {
    color: #eef5ff !important;
    font-size: 18px;
    margin: 0;
}
.card {
    background: linear-gradient(135deg,#1c1f2b,#252b3f);
    padding: 20px;
    border-radius: 18px;
    margin-bottom: 16px;
    border: 1px solid #3b4260;
}
.rank-card {
    background-color: #161a27;
    padding: 14px;
    border-radius: 14px;
    margin-bottom: 10px;
    border-left: 5px solid #4ade80;
    color: #ffffff;
}
.advice-card {
    background: linear-gradient(135deg,#10243d,#193b63);
    padding: 18px;
    border-radius: 16px;
    margin-bottom: 10px;
    border-left: 6px solid #60a5fa;
    color: #ffffff;
}
div[data-baseweb="select"] {
    background: linear-gradient(135deg, #182033, #202b44) !important;
    border-radius: 16px !important;
    border: 1px solid #4b63a0 !important;
    box-shadow: 0 4px 14px rgba(0,0,0,0.35);
}
div[data-baseweb="select"] span {
    color: white !important;
    font-weight: 600 !important;
}
div[data-baseweb="select"] svg {
    color: #93c5fd !important;
}
div[data-baseweb="popover"] {
    background-color: #1c1f2b !important;
    border-radius: 14px !important;
}
div[role="option"] {
    color: white !important;
    background-color: #1c1f2b !important;
}
div[role="option"]:hover {
    background-color: #2b3a5c !important;
}
thead tr th {
    color: white !important;
}
tbody tr td {
    color: white !important;
}
</style>
""", unsafe_allow_html=True)


def graph_ylim(values):
    if len(values) == 0:
        return 200

    max_value = max(values)

    if pd.isna(max_value):
        return 200

    if max_value > 200:
        return int(max_value + 50)

    return 200


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

    w1, w2, w3 = st.columns(3)

    with w1:
        st.metric("天気", weather_text)

    with w2:
        st.metric("気温", f"{temperature}℃")

    with w3:
        st.metric("降水量", f"{rain_mm}mm")

    st.subheader("📅 需要補正")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.metric(
            "チケット価格",
            "取得不可" if ticket_price is None else f"{ticket_price}円"
        )

    with c2:
        st.metric("取得元", ticket_map_source if ticket_price is not None else ticket_source)

    with c3:
        st.metric("補正値", today_bonus)

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
        st.metric("混雑指数", crowd_10)

    with m4:
        st.metric("5大予測補正", round(global_feedback_error, 1))

    st.caption(
        f"混雑指数は5大アトラクションのみを対象に、9:00〜20:59までのデータだけで算出しています。現在の参照元: {crowd_source}"
    )

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

    st.subheader("🤖 5大アトラクションの予想平均待ち時間")

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

        st.dataframe(
            major_display_df,
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

        with st.expander("アトラクションごとの予測を見る"):
            st.dataframe(
                wait_pred_df[
                    ["Attraction", "Hour", "Predicted Wait", "理由"]
                ],
                use_container_width=True
            )
    else:
        st.info("5大アトラクション平均予測には、9:00〜20:59の履歴データがもう少し必要です。")

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
        park
    )

    for _, row in week_df.iterrows():
        target_date = datetime.strptime(
            f"{datetime.now(JST).year}/{row['Date']}",
            "%Y/%m/%d"
        ).date()
        save_daily_crowd_prediction(
            cursor,
            conn,
            target_date,
            row["Crowd Index"]
        )

    st.dataframe(
        week_df,
        use_container_width=True
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

    st.subheader("🧠 予測誤差データの状態")

    error_only_df = prediction_history[
        prediction_history["error"].notna()
    ].copy() if len(prediction_history) > 0 else pd.DataFrame()

    if len(park_hours_df) > 0:
        st.subheader("営業時間データ")
        st.dataframe(
            park_hours_df.sort_values(
                "target_date",
                ascending=True
            ).head(100),
            use_container_width=True
        )

    if len(event_signals) > 0:
        st.subheader("イベント/休暇シグナル")
        st.dataframe(
            event_signals.sort_values(
                "target_date",
                ascending=True
            ).head(100),
            use_container_width=True
        )

    if len(attraction_status_snapshots) > 0:
        st.subheader("アトラクション営業状態履歴")
        st.dataframe(
            attraction_status_snapshots.sort_values(
                "observed_at",
                ascending=False
            ).head(100),
            use_container_width=True
        )

    if len(error_only_df) > 0:
        st.caption("予測時刻に実測待ち時間が取得できたものは、予測誤差として保存されています。")
        st.dataframe(
            error_only_df[
                error_only_df["attraction"] == GLOBAL_PREDICTION_NAME
            ].sort_values("created_at", ascending=False).head(10),
            use_container_width=True
        )
    else:
        st.info("予測誤差データはまだありません。理由の内訳を下に表示します。")

    st.dataframe(
        get_prediction_gap_summary(prediction_history),
        use_container_width=True
    )

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
            datetime.now(JST).date(),
            temperature,
            rain_mm,
            prediction_history,
            ticket_price,
            valid_target_df
        )

        pred_df = pred_all_df[
            pred_all_df["Attraction"] == selected_attraction
        ].copy()

        if len(pred_df) > 0:

            save_prediction_rows(
                cursor,
                conn,
                pred_df[["Hour", "Predicted Wait"]],
                selected_attraction
            )

            st.dataframe(
                pred_df[["Hour", "Predicted Wait", "理由"]],
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
                f"{selected_attraction} Prediction"
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

    if len(dpa_sellout_history) > 0:
        st.subheader("保存済みDPA売切れ履歴")
        st.dataframe(
            dpa_sellout_history.sort_values(
                "observed_at",
                ascending=False
            ).head(50),
            use_container_width=True
        )

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

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric("予測混雑指数", target_crowd)

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

    st.markdown(
        f"<div class='card'><h2 style='color:{color};'>{level}</h2></div>",
        unsafe_allow_html=True
    )

    st.write("価格取得元:", target_ticket_source)
    st.write("主な理由:", " / ".join(target_reasons))

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
            data_fetch_logs.sort_values(
                "fetched_at",
                ascending=False
            ).head(100),
            use_container_width=True
        )

    if len(weather_snapshots) > 0:
        st.subheader("天気スナップショット")
        st.dataframe(
            weather_snapshots.sort_values(
                "observed_at",
                ascending=False
            ).head(100),
            use_container_width=True
        )

    if len(ticket_price_snapshots) > 0:
        st.subheader("チケット価格スナップショット")
        st.dataframe(
            ticket_price_snapshots.sort_values(
                "observed_at",
                ascending=False
            ).head(100),
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
            error_only_df.sort_values(
                "created_at",
                ascending=False
            ).head(100),
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
            daily_prediction_history.sort_values(
                "created_at",
                ascending=False
            ).head(100),
            use_container_width=True
        )

    if len(dpa_sellout_history) > 0:
        st.subheader("DPA売切れ時刻履歴")
        st.dataframe(
            dpa_sellout_history.sort_values(
                "observed_at",
                ascending=False
            ).head(100),
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
