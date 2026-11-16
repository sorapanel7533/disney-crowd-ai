"""Server-side authorization for viewing Stage 5 management screens."""
from collections import deque
import hashlib
import hmac
import re
from threading import Lock
from time import monotonic

import streamlit as st

SESSION_ATTEMPTS = 5
SESSION_WINDOW = 300
APP_ATTEMPTS = 30
APP_WINDOW = 60
SESSION_DURATION = 1800


class AttemptLimit:
    def __init__(self):
        self.failures = deque()
        self.lock = Lock()

    def verify(self, candidate, expected, now):
        with self.lock:
            while self.failures and self.failures[0] <= now - APP_WINDOW:
                self.failures.popleft()
            if len(self.failures) >= APP_ATTEMPTS:
                return None
            if re.fullmatch(r"[0-9]{4}", candidate) and hmac.compare_digest(candidate, expected):
                return True
            self.failures.append(now)
            return False


@st.cache_resource
def app_attempt_limit():
    return AttemptLimit()


def configured_pin():
    try:
        value = st.secrets.get("ADMIN_VIEW_PIN", "")
    except (FileNotFoundError, KeyError):
        return ""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{4}", value) else ""


def forget_access():
    for key in ("_admin_view_access", "admin_password"):
        st.session_state.pop(key, None)


def render_view_access():
    expected = configured_pin()
    if not expected:
        forget_access()
        st.info("管理者画面の閲覧設定がまだ有効になっていません。")
        return False

    now = monotonic()
    proof = hashlib.sha256(expected.encode()).hexdigest()
    access = st.session_state.get("_admin_view_access")
    if access and access[1] > now and hmac.compare_digest(access[0], proof):
        st.button("ログアウト", key="admin_view_logout", on_click=forget_access)
        return True
    if access:
        forget_access()

    failures = [t for t in st.session_state.get("_admin_view_failures", []) if t > now - SESSION_WINDOW]
    st.session_state["_admin_view_failures"] = failures
    if len(failures) >= SESSION_ATTEMPTS:
        st.warning("入力回数の上限に達しました。5分ほど待ってからお試しください。")
        return False

    with st.form("admin_view_login", clear_on_submit=True):
        candidate = st.text_input("閲覧用PIN", type="password", max_chars=4)
        submitted = st.form_submit_button("管理者画面を開く")
    if submitted:
        accepted = app_attempt_limit().verify(candidate, expected, now)
        if accepted is True:
            st.session_state["_admin_view_access"] = (proof, now + SESSION_DURATION)
            st.session_state.pop("_admin_view_failures", None)
            st.rerun()
        elif accepted is None:
            st.warning("認証の試行が集中しています。1分ほど待ってからお試しください。")
        else:
            failures.append(now)
            st.warning("閲覧用PINが正しくありません。")
    return False
