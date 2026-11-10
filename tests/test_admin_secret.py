import hashlib
import pytest
import admin


def test_owner_can_configure_server_secret_without_exposing_it(monkeypatch):
    monkeypatch.delenv('DISNEY_ADMIN_PASSWORD_SHA256',raising=False)
    secret='isolated-owner-password'
    digest=admin.expected_digest({'ADMIN_PASSWORD':secret})
    assert digest==hashlib.sha256(secret.encode()).hexdigest()
    assert admin.authorized(secret,digest)
    assert not admin.authorized('incorrect password',digest)


@pytest.mark.parametrize('value',['', 'short', None, 123, ['not a password']])
def test_missing_or_short_secret_fails_closed(monkeypatch,value):
    monkeypatch.delenv('DISNEY_ADMIN_PASSWORD_SHA256',raising=False)
    digest=admin.expected_digest({'ADMIN_PASSWORD':value})
    assert not digest and not admin.authorized('anything',digest)


def test_existing_digest_and_environment_keep_priority(monkeypatch):
    monkeypatch.delenv('DISNEY_ADMIN_PASSWORD_SHA256',raising=False)
    old=hashlib.sha256(b'existing-password').hexdigest()
    assert admin.expected_digest({'ADMIN_PASSWORD_SHA256':old,'ADMIN_PASSWORD':'replacement-password'})==old
    monkeypatch.setenv('DISNEY_ADMIN_PASSWORD_SHA256',old)
    assert admin.expected_digest({'ADMIN_PASSWORD_SHA256':'different','ADMIN_PASSWORD':'replacement-password'})==old


def test_unavailable_secret_provider_fails_closed(monkeypatch):
    monkeypatch.delenv('DISNEY_ADMIN_PASSWORD_SHA256',raising=False)
    class Missing:
        def get(self,*args):raise RuntimeError('unavailable')
    assert admin.expected_digest(Missing())==''
