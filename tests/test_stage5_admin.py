from streamlit.testing.v1 import AppTest

from admin_access import APP_ATTEMPTS, APP_WINDOW, AttemptLimit


TEST_PIN = "2468"  # Isolated AppTest credential.
LOGIN = """
import streamlit as st
from admin_access import render_view_access
from admin import render_access
if render_view_access():
    st.write("protected")
    st.button("write", disabled=not render_access(st))
"""


def submit(at, pin):
    at.text_input[0].set_value(pin)
    next(button for button in at.button if button.label == "管理者画面を開く").click().run()


def test_view_access_fails_closed_and_logout_relocks():
    at = AppTest.from_string(LOGIN)
    at.query_params["admin"] = "granted"
    at.run()
    assert at.info and not at.button
    at.secrets["ADMIN_VIEW_PIN"] = TEST_PIN
    at.run()
    assert not at.exception
    submit(at, "1111")
    assert at.warning
    assert all(button.label != "write" for button in at.button)
    submit(at, TEST_PIN)
    assert not at.exception
    assert next(button for button in at.button if button.label == "write").disabled

    separate = AppTest.from_string(LOGIN)
    separate.secrets["ADMIN_VIEW_PIN"] = TEST_PIN
    separate.run()
    assert not any(button.label == "write" for button in separate.button)

    at.button(key="admin_view_logout").click().run()
    assert not at.exception
    assert not any(button.label == "write" for button in at.button)
    assert at.text_input[0].label == "閲覧用PIN"


def test_repeated_wrong_pins_lock_session():
    at = AppTest.from_string(LOGIN)
    at.secrets["ADMIN_VIEW_PIN"] = TEST_PIN
    at.run()
    for _ in range(5):
        submit(at, "1111")
    at.run()
    assert not at.text_input
    assert "上限" in at.warning[0].value


def test_shared_rate_limit_survives_new_sessions_and_expires():
    limiter = AttemptLimit()
    for _ in range(APP_ATTEMPTS):
        assert limiter.verify("1111", TEST_PIN, 0) is False
    assert limiter.verify(TEST_PIN, TEST_PIN, 1) is None
    assert limiter.verify(TEST_PIN, TEST_PIN, APP_WINDOW) is True
