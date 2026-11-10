"""Default-deny authorization; credentials stay in server-side secrets only."""
import hashlib
import hmac
import os


def authorized(password, expected_digest):
    if not isinstance(expected_digest,str) or len(expected_digest)!=64 or not password:
        return False
    actual = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(actual,expected_digest.lower())


def expected_digest(secrets):
    digest = os.environ.get('DISNEY_ADMIN_PASSWORD_SHA256','')
    if not digest:
        try:
            digest = secrets.get('ADMIN_PASSWORD_SHA256','')
            if not digest:
                password = secrets.get('ADMIN_PASSWORD','')
                if isinstance(password,str) and len(password)>=12:
                    digest = hashlib.sha256(password.encode('utf-8')).hexdigest()
        except Exception:
            digest = ''
    return digest


def render_access(st):
    digest = expected_digest(st.secrets)
    if not digest:
        st.sidebar.caption('管理者認証が未設定のため、共有データの変更は無効です。')
        return False
    password = st.sidebar.text_input('管理者パスワード',type='password',key='admin_password')
    ok = authorized(password,digest)
    if password and not ok:
        st.sidebar.warning('管理者認証に失敗しました')
    return ok
