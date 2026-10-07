"""Tests for production email reset and the admin management API."""
import re

import pytest

from app import config as config_module


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={
            "email": "kwame@example.com",
            "password": "secret12345",
            "learner_name": "Kwame",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "kwame@example.com", "password": "secret12345"},
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture()
def smtp_settings(monkeypatch):
    """Pretend SMTP is configured (no real server; smtplib is faked per test)."""
    monkeypatch.setattr(config_module.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(config_module.settings, "smtp_port", 587)
    monkeypatch.setattr(config_module.settings, "smtp_username", "user")
    monkeypatch.setattr(config_module.settings, "smtp_password", "pass")
    monkeypatch.setattr(config_module.settings, "smtp_from", "Talyn <noreply@example.com>")
    monkeypatch.setattr(config_module.settings, "frontend_url", "https://app.example.com")
    return config_module.settings


class FakeSMTP:
    """Stand-in for smtplib.SMTP capturing sent messages."""

    last_instance = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.sent = []
        FakeSMTP.last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, username, password):
        self.username = username

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture()
def fake_smtp(monkeypatch):
    import smtplib

    # Patch through the email service, not the stdlib module: the service
    # holds its own reference, and monkeypatching smtplib directly would also
    # change behaviour for anything else that reaches for it.
    from app.services import email as email_service

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.last_instance = None
    return FakeSMTP


def _plain_text(message) -> str:
    """Body of the text/plain part.

    Messages are multipart (text + HTML); get_content() on the outer message
    raises on the multipart container, so walk to the part we mean.
    """
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode("utf-8")
        return ""
    return message.get_payload(decode=True).decode("utf-8")


# ── Email reset ───────────────────────────────────────────────────────────────

def test_reset_sends_email_not_token(client, auth_headers, smtp_settings, fake_smtp):
    r = client.post(
        "/v1/auth/password-reset/request", json={"email": "kwame@example.com"}
    )
    assert r.status_code == 200
    assert "reset_code" not in r.json()
    assert "reset_token" not in r.json()

    # Registration also sends a welcome, so look for the reset specifically.
    resets = [m for m in fake_smtp.last_instance.sent
              if "Reset your" in (m["Subject"] or "")]
    assert len(resets) == 1
    body = _plain_text(resets[0])
    assert resets[0]["To"] == "kwame@example.com"
    assert re.search(r"Your code: (\d{6})", body)
    assert "token=" not in body


def test_reset_email_failure_is_502(client, auth_headers, smtp_settings, monkeypatch):
    import smtplib

    def _boom(host, port, timeout=None):
        raise smtplib.SMTPException("relay denied")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    r = client.post(
        "/v1/auth/password-reset/request", json={"email": "kwame@example.com"}
    )
    assert r.status_code == 502


def test_reset_unconfigured_outside_dev_fails_closed(client, auth_headers, monkeypatch):
    monkeypatch.setattr(config_module.settings, "environment", "staging")
    monkeypatch.setattr(config_module.settings, "smtp_host", "")
    r = client.post(
        "/v1/auth/password-reset/request", json={"email": "kwame@example.com"}
    )
    assert r.status_code == 503
    assert "reset_token" not in r.json()


def test_emailed_token_completes_reset(client, auth_headers, smtp_settings, fake_smtp):
    client.post("/v1/auth/password-reset/request", json={"email": "kwame@example.com"})
    resets = [m for m in fake_smtp.last_instance.sent
              if "Reset your" in (m["Subject"] or "")]
    body = _plain_text(resets[0])
    code = re.search(r"Your code: (\d{6})", body).group(1)
    r = client.post(
        "/v1/auth/password-reset/confirm",
        json={"email": "kwame@example.com", "code": code,
              "new_password": "emailedok1"},
    )
    assert r.status_code == 200
    assert r.json()["next_step"] == "login"
    assert client.post(
        "/v1/auth/login",
        json={"email": "kwame@example.com", "password": "emailedok1"},
    ).status_code == 200


# ── Admin API ─────────────────────────────────────────────────────────────────

def test_admin_endpoints_forbid_non_admin(client, auth_headers):
    assert client.get("/v1/admin/users", headers=auth_headers).status_code == 403
    assert client.get("/v1/admin/audit-log", headers=auth_headers).status_code == 403
    assert client.patch(
        "/v1/admin/users/1", json={"is_admin": True}, headers=auth_headers
    ).status_code == 403
    assert client.get("/v1/admin/users").status_code == 401


def test_admin_list_search_and_promote(client, auth_headers, admin_headers):
    rows = client.get("/v1/admin/users", headers=admin_headers).json()
    emails = [u["email"] for u in rows]
    assert "kwame@example.com" in emails
    assert "admin@example.com" in emails

    rows = client.get("/v1/admin/users?q=kwame", headers=admin_headers).json()
    assert [u["email"] for u in rows] == ["kwame@example.com"]

    me = client.get("/v1/users/me", headers=auth_headers).json()
    r = client.patch(
        f"/v1/admin/users/{me['id']}", json={"is_admin": True}, headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["is_admin"] is True
    # promoted user can now use admin endpoints
    assert client.get("/v1/admin/users", headers=auth_headers).status_code == 200

    log = client.get("/v1/admin/audit-log", headers=admin_headers).json()
    assert len(log) == 1
    assert log[0]["action"] == "promote"
    assert log[0]["target_email"] == "kwame@example.com"
    assert log[0]["actor_email"] == "admin@example.com"


def test_admin_demote_and_self_demote_guard(client, auth_headers, admin_headers):
    me = client.get("/v1/users/me", headers=auth_headers).json()
    client.patch(
        f"/v1/admin/users/{me['id']}", json={"is_admin": True}, headers=admin_headers
    )
    r = client.patch(
        f"/v1/admin/users/{me['id']}", json={"is_admin": False}, headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
    # demoted user loses admin access
    assert client.get("/v1/admin/users", headers=auth_headers).status_code == 403

    log = client.get("/v1/admin/audit-log", headers=admin_headers).json()
    assert [e["action"] for e in log] == ["demote", "promote"]

    admin_me = client.get("/v1/users/me", headers=admin_headers).json()
    r = client.patch(
        f"/v1/admin/users/{admin_me['id']}", json={"is_admin": False},
        headers=admin_headers,
    )
    assert r.status_code == 422


def test_admin_unknown_user(client, admin_headers):
    assert client.patch(
        "/v1/admin/users/99999", json={"is_admin": True}, headers=admin_headers
    ).status_code == 404


def test_admin_audit_log_pagination(client, admin_headers):
    rows = client.get("/v1/admin/audit-log?limit=10&offset=0", headers=admin_headers).json()
    assert rows == []
