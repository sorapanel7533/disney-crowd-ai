"""Shared display controls and graphs for the existing wait forecasts."""
from datetime import timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from reliability import JST, japanese_name, load_catalog


def retained_input(widget, label, key, **kwargs):
    """Keep input values when the widget is hidden by navigation."""
    widget_key = "_" + key
    if key in st.session_state and widget_key not in st.session_state:
        saved = st.session_state[key]
        if ("options" not in kwargs or saved in kwargs["options"]) and (
            "min_value" not in kwargs or saved >= kwargs["min_value"]
        ) and (
            "max_value" not in kwargs or saved <= kwargs["max_value"]
        ):
            st.session_state[widget_key] = saved
            kwargs.pop("value", None)
    value = widget(label, key=widget_key, **kwargs)
    st.session_state[key] = value
    return value


def date_conditions(park, today, temperature):
    prefix = f"date_forecast_{park}"
    target = retained_input(
        st.date_input, "予測する日付", prefix + "_date",
        value=today, min_value=today, max_value=today + timedelta(days=30),
    )
    weather = retained_input(st.selectbox, "天気", prefix + "_weather", options=["晴れ", "くもり", "雨"])
    if st.session_state.get(prefix + "_rain_weather") != weather:
        for key in (prefix + "_rain", "_" + prefix + "_rain"):
            st.session_state.pop(key, None)
        st.session_state[prefix + "_rain_weather"] = weather
    left, right = st.columns(2)
    with left:
        temp = retained_input(
            st.number_input, "気温", prefix + "_temperature", min_value=-5.0, max_value=40.0,
            value=float(np.clip(temperature, -5, 40)) if np.isfinite(temperature) else 20.0, step=0.5,
        )
    with right:
        rain = retained_input(
            st.number_input, "降水量(mm)", prefix + "_rain", min_value=0.0, max_value=80.0,
            value=1.0 if weather == "雨" else 0.0, step=0.5,
        )
    return target, temp, rain


def minutes(labels):
    parsed = pd.to_datetime(labels, format="%H:%M", errors="coerce")
    return parsed.dt.hour * 60 + parsed.dt.minute


def render_wait_chart(predictions, attraction, target, park, observed=None, show_actual=False):
    required = {"TimeLabel", "Predicted Wait"}
    if not required.issubset(predictions.columns):
        st.info("このアトラクションの予測データがまだ不足しています。")
        return False
    from measured_views import estimate_rows, training

    approved = training(st)
    series, _ = estimate_rows(st, predictions, park, "Predicted Wait", target=target, training_frame=approved)
    series = series.assign(chart_time=minutes(series["TimeLabel"])).dropna(subset=["chart_time"])
    series = series.sort_values("chart_time")
    predicted = pd.to_numeric(series["Predicted Wait"], errors="coerce").astype(float)
    predicted = predicted.where(np.isfinite(predicted))
    if not predicted.notna().any():
        st.info("このアトラクションの予測データがまだ不足しています。")
        return False

    fig, ax = plt.subplots(figsize=(7, 3.4), layout="constrained")
    try:
        ax.plot(series["chart_time"], predicted, label="予測待ち時間", color="#1471bd", linewidth=2.1, marker=".", markersize=3)
        if observed is not None and {"TimeLabel", "Actual Wait"}.issubset(observed):
            actual = pd.to_numeric(observed["Actual Wait"], errors="coerce").astype(float)
            if actual.notna().any():
                ax.plot(minutes(observed["TimeLabel"]), actual, "--o", label="表示待ち時間の観測", color="#aa4d13", markersize=3)
        if show_actual and not approved.empty:
            ride_id = next((int(key) for key, row in load_catalog().items()
                            if row["park"] == park and row["name"] == attraction), None)
            actual_rows = approved.loc[approved["park"].eq(park) & approved["ride_id"].eq(ride_id) & approved["date"].eq(target)]
            if not actual_rows.empty:
                started = pd.to_datetime(actual_rows["started_at"], utc=True).dt.tz_convert(JST)
                ax.scatter(started.dt.hour * 60 + started.dt.minute, actual_rows["actual_wait"],
                           label="承認済みの実測", color="#282828", zorder=4)
        estimate = pd.to_numeric(series["推定実測（分）"], errors="coerce").astype(float)
        if estimate.notna().any():
            ax.plot(series["chart_time"], estimate, "--o", label="推定実測", color="#15724c", markersize=3)

        left, right = ax.get_xlim()
        ticks = [int(x) for x in range(0, 1440, 120) if left <= x <= right]
        if not ticks:
            ticks = [int(series["chart_time"].iloc[0])]
        ax.set_xticks(ticks, [f"{t // 60:02d}:{t % 60:02d}" for t in ticks])
        ax.set_ylim(bottom=0)
        ax.set(xlabel="時刻（日本時間）", ylabel="待ち時間（分）", title=f"{japanese_name(attraction)}  {target:%m/%d}")
        ax.grid(axis="y", alpha=0.2)
        ax.legend(fontsize=9)
        st.pyplot(fig)
    finally:
        plt.close(fig)
    st.caption("承認済みの実測が20件・5日以上ある場合に推定実測を表示します。推定精度は検証中です。")
    return True
