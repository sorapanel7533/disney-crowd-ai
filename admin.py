"""Default-deny authorization. Store only a SHA-256 password digest in secrets."""
import hashlib
import hmac
import os


def authorized(password, expected_digest):
    if not isinstance(expected_digest,str) or len(expected_digest)!=64 or not password:
        return False
    actual = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(actual,expected_digest.lower())


def render_access(st):
    digest = os.environ.get('DISNEY_ADMIN_PASSWORD_SHA256','')
    if not digest:
        try:
            digest = st.secrets.get('ADMIN_PASSWORD_SHA256','')
        except Exception:
            digest = ''
    if not digest:
        st.sidebar.caption('管理者認証が未設定のため、共有データの変更は無効です。')
        return False
    password = st.sidebar.text_input('管理者パスワード',type='password',key='admin_password')
    ok = authorized(password,digest)
    if password and not ok:
        st.sidebar.warning('管理者認証に失敗しました')
    return ok
