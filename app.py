import random
import time
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from sklearn.linear_model import LinearRegression

from utils import (
    GLOBAL_PREDICTION_NAME,
    PARK_SETTINGS,
    connect_db,
    fetch_castel_ticket_prices,
    fetch_wait_times,
    get_calendar_bonus,
    get_crowd_index,
    get_current_stats,
    get_dpa_score,
    get_feedback_error,
    get_level,
    get_ticket_price_from_castel,
    get_today_stats,
    get_valid_open_df,
    get_weather,
    get_weather_score,
    load_history,
    load_prediction_history,
    make_action_advice,
    now_jst,
    now_jst_str,
    predict_dpa_risk,
    save_prediction_rows,
    save_wait_times,
    update_prediction_feedback,
)

st.set_page_config(
    page_title="ディズニー混雑AI",
    page_icon="🏰",
    layout="wide"
)

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


def make_prediction(history_data, temperature, rain_mm, feedback, today_bonus):
    model_df = history_data[
        history_data["wait_time"] > 0
    ].copy()

    if len(model_df) <= 10:
        return pd.DataFrame()

    X = pd.DataFrame({
        "hour": model_df["hour"],
        "hour2": model_df["hour"] ** 2,
        "temperature": model_df["temperature"],
        "rain": model_df["rain"]
    })

    y = model_df["wait_time"]

    model = LinearRegression()
    model.fit(X, y)

    future_hours = list(range(9, 22))

    future = pd.DataFrame({
        "hour": future_hours
    })

    future["hour2"] = future["hour"] ** 2
    future["temperature"] = temperature
    future["rain"] = rain_mm

    pred = model.predict(future)
    pred = pred + feedback
    pred = pred + today_bonus * 5
    pred = np.clip(pred, 0, None)

    pred_df = pd.DataFrame({
        "Hour": future_hours,
        "Predicted Wait": pred
    })

    return pred_df


st.markdown(f"""
<div class="title-card">
<h1>🏰 ディズニー混雑AI</h1>
<p>東京ディズニーリゾート混雑分析システム</p>
<p>最終更新: {now_jst_str()}（日本時間）</p>
</div>
""", unsafe_allow_html=True)

park = st.selectbox(
    "🏰 パークを選択",
    ["DisneySea", "Disneyland"]
)

settings = PARK_SETTINGS[park]

display_mode = st.selectbox(
    "🧭 表示モードを選択",
    [
        "ダッシュボード",
        "全アトラクション",
        "アトラクション別予測",
        "DPA売切れ予測",
        "データ管理"
    ]
)

ticket_price_map, ticket_map_source = fetch_castel_ticket_prices()

ticket_price, ticket_source = get_ticket_price_from_castel(
    now_jst(),
    ticket_price_map
)

today_bonus, today_reasons = get_calendar_bonus(
    now_jst(),
    ticket_price
)

temperature, rain_mm, weather_text, hourly_weather, daily_weather = get_weather()

conn, cursor = connect_db(settings["db"])

try:
    all_df, target_df = fetch_wait_times(settings)

except Exception:
    st.error("待ち時間データ取得失敗")
    st.stop()

if all_df.empty:
    st.error("アトラクションのデータが取得できませんでした")
    st.stop()

valid_all_df = get_valid_open_df(all_df)
valid_target_df = get_valid_open_df(target_df)

history_df = load_history(conn)

current_avg_wait, current_max_wait, current_var_wait = get_current_stats(valid_all_df)

avg_wait, max_wait, var_wait, crowd_source = get_today_stats(
    history_df,
    valid_all_df
)

if len(valid_all_df) == 0:
    st.warning(
        "現在は営業中の有効な待ち時間データがありません。"
    )

prediction_history = load_prediction_history(conn)

global_feedback_error = get_feedback_error(
    prediction_history,
    GLOBAL_PREDICTION_NAME
)

update_prediction_feedback(
    cursor,
    conn,
    valid_all_df,
    current_avg_wait
)

prediction_history = load_prediction_history(conn)

global_feedback_error = get_feedback_error(
    prediction_history,
    GLOBAL_PREDICTION_NAME
)

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

    now_hour = now_jst().hour

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

# ==========================================
# 裏で全体予測・全アトラクション別予測を自動保存
# ==========================================

if len(history_df) > 20:

    global_pred_df = make_prediction(
        history_df,
        temperature,
        rain_mm,
        global_feedback_error,
        today_bonus
    )

    if len(global_pred_df) > 0:
        save_prediction_rows(
            cursor,
            conn,
            global_pred_df,
            GLOBAL_PREDICTION_NAME
        )

    for attraction_name in sorted(history_df["attraction"].unique()):

        one_history = history_df[
            history_df["attraction"] == attraction_name
        ].copy()

        if len(one_history) > 10:

            attraction_feedback_error = get_feedback_error(
                prediction_history,
                attraction_name
            )

            attraction_pred_df = make_prediction(
                one_history,
                temperature,
                rain_mm,
                attraction_feedback_error,
                today_bonus
            )

            if len(attraction_pred_df) > 0:
                save_prediction_rows(
                    cursor,
                    conn,
                    attraction_pred_df,
                    attraction_name
                )

prediction_history = load_prediction_history(conn)

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
        st.metric("取得元", ticket_source)

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
        st.metric("平均待ち時間", round(avg_wait, 1))

    with m2:
        st.metric("最大待ち時間", round(max_wait, 1))

    with m3:
        st.metric("混雑指数", crowd_10)

    with m4:
        st.metric("全体予測補正", round(global_feedback_error, 1))

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

    st.subheader("🤖 1時間ごとの全体AI予測")

    global_display_pred = make_prediction(
        history_df,
        temperature,
        rain_mm,
        global_feedback_error,
        today_bonus
    )

    if len(global_display_pred) > 0:
        st.dataframe(
            global_display_pred,
            use_container_width=True
        )

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.plot(
            global_display_pred["Hour"],
            global_display_pred["Predicted Wait"],
            marker="o"
        )

        ax.set_ylim(
            0,
            graph_ylim(global_display_pred["Predicted Wait"].tolist())
        )

        ax.set_ylabel("Predicted Wait")
        ax.set_title(f"{park} Overall Prediction")

        st.pyplot(fig)
    else:
        st.info("全体予測には履歴データがもう少し必要です。")

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
                    (hourly_avg["hour"] >= 8)
                    &
                    (hourly_avg["hour"] <= 22)
                ]

                ax.plot(
                    hourly_avg["hour"],
                    hourly_avg["wait_time"],
                    marker="o"
                )

                ax.set_xticks(range(8, 23))
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

        one_df = history_df[
            history_df["attraction"]
            == selected_attraction
        ].copy()

        pred_df = make_prediction(
            one_df,
            temperature,
            rain_mm,
            attraction_feedback_error,
            today_bonus
        )

        if len(pred_df) > 0:

            st.dataframe(
                pred_df,
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
                st.info("このアトラクションの誤差データはまだありません。")

        else:
            st.info("このアトラクションの履歴がまだ足りません。")

elif display_mode == "DPA売切れ予測":

    st.subheader("🎫 DPA売切れ予測")

    dpa_rows = []

    for _, row in valid_target_df.iterrows():

        risk_text, risk_score = predict_dpa_risk(
            row["Wait"],
            crowd_10,
            ticket_price,
            today_bonus
        )

        dpa_rows.append({
            "Attraction": row["Attraction"],
            "Wait": row["Wait"],
            "Risk": risk_text,
            "Risk Score": risk_score
        })

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

    else:
        st.info("DPA予測に使えるデータがありません。")

elif display_mode == "データ管理":

    st.subheader("🗂 データ管理")

    prediction_display = prediction_history.copy()

    if len(prediction_display) > 0:
        prediction_display["attraction"] = prediction_display["attraction"].replace(
            GLOBAL_PREDICTION_NAME,
            "全体予測"
        )

    error_only_df = prediction_display[
        prediction_display["error"].notna()
    ].copy() if len(prediction_display) > 0 else pd.DataFrame()

    m1, m2, m3 = st.columns(3)

    with m1:
        st.metric("保存データ数", len(history_df))

    with m2:
        st.metric("予測データ数", len(prediction_display))

    with m3:
        st.metric("予測誤差データ", len(error_only_df))

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

st.subheader("⚙ システム")

st.write("選択中:", park)
st.write("表示モード:", display_mode)
st.write("保存データ:", len(history_df))
st.write("予測データ:", len(prediction_history))
st.write("全体予測補正:", round(global_feedback_error, 1))
st.write("最終更新:", f"{now_jst_str()}（日本時間）")

refresh_seconds = 900

st.caption(
    f"🔄 {refresh_seconds // 60}分ごと自動更新"
)

time.sleep(refresh_seconds)

st.rerun()
