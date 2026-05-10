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
    get_next_feature_plan,
    get_attraction_status_summary,
    get_event_bonus,
    get_park_hours_bonus,
    get_prediction_accuracy_report,
    get_prediction_alerts,
    get_prediction_confidence,
    get_area_crowd_map,
    get_emptying_candidates,
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
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
