"""Regression + reliability tests: audit-safe deletion, published counts,
rate limiting, observability."""
import pytest

from app import config as config_module
from app.core import rate_limit


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={"email": "amina2@example.com", "password": "secret12345",
              "learner_name": "Amina"},
    )
    r = client.post("/v1/auth/login", json={"email": "amina2@example.com",
                                            "password": "secret12345"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

def test_delete_target_keeps_audit_trail(client, auth_headers, admin_headers):
    me = client.get("/v1/users/me", headers=auth_headers).json()
    client.patch(f"/v1/admin/users/{me['id']}", json={"is_admin": True},
                 headers=admin_headers)
    assert client.delete("/v1/users/me", headers=auth_headers).status_code == 200

    log = client.get("/v1/admin/audit-log", headers=admin_headers).json()
    assert len(log) == 1
    assert log[0]["action"] == "promote"
    assert log[0]["target_user_id"] is None
    assert log[0]["target_email"] == "?"
    assert log[0]["actor_email"] == "admin@example.com"


def test_delete_actor_keeps_audit_trail(client, auth_headers, admin_headers, db_session):
    from sqlalchemy import select
    from app.models import User

    me = client.get("/v1/users/me", headers=auth_headers).json()
    client.patch(f"/v1/admin/users/{me['id']}", json={"is_admin": True},
                 headers=admin_headers)
    # admin deletes their own account (actor of the audit row)
    admin_me = client.get("/v1/users/me", headers=admin_headers).json()
    admin_user = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert admin_user.id == admin_me["id"]
    assert client.delete("/v1/users/me", headers=admin_headers).status_code == 200

    # promote a fresh admin directly to read the surviving trail
    client.post("/v1/auth/register", json={"email": "boss@example.com",
                                           "password": "secret12345",
                                           "learner_name": "Boss"})
    boss = db_session.scalar(select(User).where(User.email == "boss@example.com"))
    boss.is_admin = True
    db_session.commit()
    r = client.post("/v1/auth/login", json={"email": "boss@example.com",
                                            "password": "secret12345"})
    boss_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    log = client.get("/v1/admin/audit-log", headers=boss_headers).json()
    assert len(log) == 1
    assert log[0]["actor_user_id"] is None
    assert log[0]["actor_email"] == "?"


# ── Published-only lesson counts ─────────────────────────────────────────────

def test_lesson_count_excludes_unpublished(client, admin_headers):
    cid = client.post(
        "/v1/courses",
        json={
            "title": "Mixed",
            "difficulty_level": "beginner",
            "lessons": [
                {"order": 1, "title": "Vis", "topic": "T", "is_published": True},
                {"order": 2, "title": "Hid", "topic": "T", "is_published": False},
            ],
        },
        headers=admin_headers,
    ).json()["id"]
    rows = client.get("/v1/courses?mine=true", headers=admin_headers).json()
    assert [c for c in rows if c["id"] == cid][0]["lesson_count"] == 1
    assert len(client.get(f"/v1/courses/{cid}/lessons").json()) == 1


# ── Rate limiting ─────────────────────────────────────────────────────────────

@pytest.fixture()
def limiter_on(monkeypatch):
    monkeypatch.setattr(config_module.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(config_module.settings, "rate_limit_auth_per_minute", 3)
    monkeypatch.setattr(config_module.settings, "rate_limit_per_minute", 5)
    rate_limit.reset_rate_limit_store()
    yield
    rate_limit.reset_rate_limit_store()


def test_auth_bucket_limits_logins(client, limiter_on):
    for _ in range(3):
        client.post("/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "whatever12"})
    r = client.post("/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "whatever12"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"


def test_buckets_are_independent(client, limiter_on):
    for _ in range(3):
        client.post("/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "whatever12"})
    # default bucket untouched: public reads still fine
    assert client.get("/v1/courses").status_code == 200
    for _ in range(4):
        assert client.get("/v1/courses").status_code == 200
    assert client.get("/v1/courses").status_code == 429


# ── Observability ─────────────────────────────────────────────────────────────

def test_request_id_echoed(client):
    r = client.get("/health")
    assert r.headers["X-Request-ID"]
    first = r.headers["X-Request-ID"]
    assert client.get("/health").headers["X-Request-ID"] != first
    r = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"


def test_health_reports_version_and_env(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == "0.2.0"
    assert body["environment"] == "dev"
