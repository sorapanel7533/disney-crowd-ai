import time

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from sklearn.linear_model import LinearRegression

from utils import (
    GLOBAL_PREDICTION_NAME,
    PARK_SETTINGS,
    connect_db,
    fetch_wait_times,
    get_feedback_error,
    get_valid_open_df,
    load_history,
    load_prediction_history,
    now_jst_str,
    save_prediction_rows,
    save_wait_times,
    update_prediction_feedback
)

st.set_page_config(
    page_title="ディズニー混雑AI",
    page_icon="🏰",
    layout="wide"
)

st.title("🏰 ディズニー混雑AI")

st.write(
    f"最終更新: {now_jst_str()}（日本時間）"
)

park = st.selectbox(
    "パーク選択",
    ["DisneySea", "Disneyland"]
)

settings = PARK_SETTINGS[park]

conn, cursor = connect_db(
    settings["db"]
)

df = fetch_wait_times(settings)

valid_df = get_valid_open_df(df)

save_wait_times(
    cursor,
    conn,
    valid_df
)

history_df = load_history(conn)

prediction_history = load_prediction_history(conn)

update_prediction_feedback(
    cursor,
    conn,
    valid_df
)

st.subheader("現在の待ち時間")

st.dataframe(
    valid_df[
        ["Attraction", "Wait"]
    ],
    use_container_width=True
)

st.subheader("アトラクション別予測")

if len(history_df) > 20:

    attraction_list = sorted(
        history_df["attraction"].unique()
    )

    selected_attraction = st.selectbox(
        "アトラクション選択",
        attraction_list
    )

    one_df = history_df[
        history_df["attraction"]
        == selected_attraction
    ].copy()

    feedback_error = get_feedback_error(
        prediction_history,
        selected_attraction
    )

    st.metric(
        "予測補正",
        round(feedback_error, 1)
    )

    X = pd.DataFrame({
        "hour": one_df["hour"]
    })

    y = one_df["wait_time"]

    model = LinearRegression()

    model.fit(X, y)

    future_hours = list(range(9, 22))

    future = pd.DataFrame({
        "hour": future_hours
    })

    pred = model.predict(future)

    pred = pred + feedback_error

    pred_df = pd.DataFrame({
        "Hour": future_hours,
        "Predicted Wait": pred
    })

    save_prediction_rows(
        cursor,
        conn,
        pred_df,
        selected_attraction
    )

    st.dataframe(
        pred_df,
        use_container_width=True
    )

    fig, ax = plt.subplots()

    ax.plot(
        pred_df["Hour"],
        pred_df["Predicted Wait"],
        marker="o"
    )

    ax.set_ylabel("Predicted Wait")

    ax.set_title(
        selected_attraction
    )

    st.pyplot(fig)

else:

    st.info(
        "履歴データ不足"
    )

st.subheader("予測誤差データ")

if len(prediction_history) > 0:

    st.dataframe(
        prediction_history.tail(50),
        use_container_width=True
    )

else:

    st.info(
        "まだ予測データなし"
    )

refresh_seconds = 900

st.caption(
    "15分ごと自動更新"
)

time.sleep(refresh_seconds)

st.rerun()
