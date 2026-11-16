"""Forecast caches keyed by data, local time, all inputs and model version."""
from dataclasses import fields
from datetime import datetime
from hashlib import sha256

import pandas as pd
import streamlit as st

import reliability
import utils


def frame_key(df):
    if df is None:
        return None
    # Hash all rows, preserving their order. Streamlit samples larger DataFrames.
    digest = sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    digest.update(str(tuple(df.columns)).encode())
    return digest.hexdigest()


def issue_slot():
    return pd.Timestamp(datetime.now(reliability.JST)).floor("15min").isoformat()


@st.cache_data(max_entries=16, show_spinner=False, ttl=900)
def _model(data_key, park, slot, model, _history):
    model_history = utils.prepare_forecast_model(_history, park)
    # Cache plain data so the saved value survives Streamlit's module reload.
    return tuple(getattr(model_history, field.name) for field in fields(model_history))


def model(data_key, park, history):
    values = _model(data_key, park, issue_slot(), reliability.MODEL_VERSION, history)
    return utils.ForecastModelHistory(*values)


@st.cache_data(max_entries=60, show_spinner=False, ttl=900)
def wait(data_key, slot, model, settings, day, temperature, rain, ticket_price, live_key,
         names, _history, _errors, _live, _prepared):
    return utils.predict_wait_times_for_date(
        _history, settings, day, temperature, rain, _errors, ticket_price, _live,
        attraction_list=names, prepared_model=_prepared,
    )


@st.cache_data(max_entries=30, show_spinner=False, ttl=900)
def crowd(data_key, slot, model, settings, day, temperature, rain, ticket_price, live_key,
          _history, _errors, _daily, _live, _events, _hours, _prepared, context_key):
    return utils.predict_crowd_index_for_date(
        _history, settings, day, temperature, rain, _errors, _daily, ticket_price,
        _live, _events, _hours, settings["park"], prepared_model=_prepared,
    )


@st.cache_data(max_entries=12, show_spinner=False, ttl=900)
def week(data_key, slot, model, settings, day, temperature, rain, prices, weather_key,
         _history, _errors, _daily, _events, _hours, _weather, _prepared, _connection,
         persist_missing, context_key):
    return utils.make_locked_week_forecast(
        _history, settings, day, temperature, rain, _errors, _daily, prices,
        event_signals=_events, park_hours_df=_hours, park=settings["park"],
        daily_weather=_weather, cursor=_connection.cursor(), conn=_connection,
        persist_missing=persist_missing, prepared_model=_prepared, summary_only=True,
    )
