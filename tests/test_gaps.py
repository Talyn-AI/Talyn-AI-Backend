"""Tests for auth hardening, admin gating, XP awards, badges, conversation,
mission-id backfill, pagination, and delete endpoints."""
import pytest


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={
            "email": "zainab@example.com",
            "password": "secret12345",
            "learner_name": "Zainab",
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "zainab@example.com", "password": "secret12345"},
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture()
def tokens(client, auth_headers):
    r = client.post(
        "/v1/auth/login",
        json={"email": "zainab@example.com", "password": "secret12345"},
    )
    return r.json()


# ── Token refresh ─────────────────────────────────────────────────────────────

def test_login_returns_refresh_token(tokens):
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_refresh_flow(client, tokens):
    r = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] != tokens["access_token"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/v1/users/me", headers=headers).status_code == 200


def test_refresh_rejects_access_token_and_garbage(client, tokens):
    assert client.post(
        "/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
    ).status_code == 401
    assert client.post(
        "/v1/auth/refresh", json={"refresh_token": "garbage"}
    ).status_code == 401


def test_access_token_rejected_as_refresh(client, tokens):
    # refresh tokens must not authenticate API calls
    headers = {"Authorization": f"Bearer {tokens['refresh_token']}"}
    assert client.get("/v1/users/me", headers=headers).status_code == 401


# ── Password change + reset ───────────────────────────────────────────────────

def test_change_password(client, auth_headers):
    r = client.patch(
        "/v1/users/me/password",
        json={"current_password": "secret12345", "new_password": "newsecret99"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    ok = client.post(
        "/v1/auth/login",
        json={"email": "zainab@example.com", "password": "newsecret99"},
    )
    assert ok.status_code == 200
    old = client.post(
        "/v1/auth/login",
        json={"email": "zainab@example.com", "password": "secret12345"},
    )
    assert old.status_code == 401


def test_change_password_wrong_current(client, auth_headers):
    r = client.patch(
        "/v1/users/me/password",
        json={"current_password": "wrongpass1", "new_password": "newsecret99"},
        headers=auth_headers,
    )
    assert r.status_code == 401


def test_password_reset_flow(client, auth_headers):
    r = client.post(
        "/v1/auth/password-reset/request", json={"email": "zainab@example.com"}
    )
    assert r.status_code == 200
    token = r.json()["reset_token"]
    r = client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "resetpass1"},
    )
    assert r.status_code == 200
    assert client.post(
        "/v1/auth/login",
        json={"email": "zainab@example.com", "password": "resetpass1"},
    ).status_code == 200


def test_password_reset_unknown_email_same_shape(client):
    r = client.post(
        "/v1/auth/password-reset/request", json={"email": "ghost@example.com"}
    )
    assert r.status_code == 200
    assert r.json() == {"message": "If the email exists, a reset link was sent"}
    assert "reset_token" not in r.json()


def test_password_reset_bad_token(client):
    r = client.post(
        "/v1/auth/password-reset/confirm",
        json={"token": "bogus", "new_password": "resetpass1"},
    )
    assert r.status_code == 401


# ── Account deletion ──────────────────────────────────────────────────────────

def test_delete_account(client, auth_headers):
    client.post("/v1/me/conversation", json={"role": "user", "content": "hi"}, headers=auth_headers)
    r = client.delete("/v1/users/me", headers=auth_headers)
    assert r.status_code == 200
    # token no longer resolves; email is free again
    assert client.get("/v1/users/me", headers=auth_headers).status_code == 401
    assert client.post(
        "/v1/auth/register",
        json={
            "email": "zainab@example.com",
            "password": "secret12345",
            "learner_name": "Zainab",
        },
    ).status_code == 201


# ── Admin-gated course management ─────────────────────────────────────────────

COURSE = {"title": "C", "description": "d", "difficulty_level": "beginner", "lessons": []}


def test_create_course_requires_admin(client, auth_headers, admin_headers):
    assert client.post("/v1/courses", json=COURSE).status_code == 401
    assert client.post("/v1/courses", json=COURSE, headers=auth_headers).status_code == 403
    r = client.post("/v1/courses", json=COURSE, headers=admin_headers)
    assert r.status_code == 201


def test_delete_course_admin_only(client, auth_headers, admin_headers):
    cid = client.post("/v1/courses", json={**COURSE, "lessons": [
        {"order": 1, "title": "L", "topic": "T", "estimated_minutes": 5},
    ]}, headers=admin_headers).json()["id"]
    client.post(f"/v1/me/enroll/{cid}", headers=auth_headers)
    assert client.delete(f"/v1/courses/{cid}").status_code == 401
    assert client.delete(f"/v1/courses/{cid}", headers=auth_headers).status_code == 403
    r = client.delete(f"/v1/courses/{cid}", headers=admin_headers)
    assert r.status_code == 200
    assert client.get(f"/v1/courses/{cid}").status_code == 404
    assert client.get(f"/v1/courses/{cid}/lessons").status_code == 404
    assert client.delete("/v1/courses/99999", headers=admin_headers).status_code == 404


def test_make_admin_script_marks_user(client, auth_headers):
    import os
    import subprocess

    from conftest import TEST_DATABASE_URL

    me = client.get("/v1/users/me", headers=auth_headers).json()
    assert me["is_admin"] is False
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    proc = subprocess.run(
        ["python", "scripts/make_admin.py", "zainab@example.com"],
        capture_output=True,
        text=True,
        cwd=".",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert client.get("/v1/users/me", headers=auth_headers).json()["is_admin"] is True


# ── Generic XP awards ─────────────────────────────────────────────────────────

def test_manual_xp_awards(client, auth_headers):
    for activity, amount in (("revision", 15), ("streak", 20)):
        r = client.post(
            "/v1/me/xp/award",
            json={"activity": activity, "note": "test"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["amount"] == amount
    assert client.get("/v1/me/xp", headers=auth_headers).json()["xp_total"] == 35


def test_manual_xp_rejects_dedicated_sources(client, auth_headers):
    for activity in ("lesson", "quiz", "mission", "bogus-activity"):
        r = client.post(
            "/v1/me/xp/award", json={"activity": activity}, headers=auth_headers
        )
        assert r.status_code == 422, activity
    assert client.post("/v1/me/xp/award", json={"activity": "revision"}).status_code == 401


# ── Badges + completed mission ids ────────────────────────────────────────────

def test_badges_and_completed_ids(client, auth_headers):
    assert client.get("/v1/me/badges", headers=auth_headers).json() == []
    # A learner can no longer author a mission, so this goes through the
    # creator catalogue first.
    client.post("/v1/auth/register", json={
        "email": "gaps-mission-author@example.com", "password": "password123",
        "learner_name": "Author", "is_creator": True,
    })
    creator = {"Authorization": "Bearer " + client.post("/v1/auth/login", json={
        "email": "gaps-mission-author@example.com", "password": "password123",
    }).json()["access_token"]}
    tid = client.post("/v1/creator/missions", json={
        "title": "M", "reward_xp": 50, "badge": "Star",
        "steps": [{"order": 1, "title": "S"}],
    }, headers=creator).json()["id"]
    client.patch(f"/v1/creator/missions/{tid}", json={"published": True},
                 headers=creator)

    mid = client.post("/v1/me/missions", json={"template_id": tid},
                      headers=auth_headers).json()["id"]
    step = client.get(f"/v1/me/missions/{mid}", headers=auth_headers).json()["steps"][0]
    client.post(f"/v1/me/missions/{mid}/steps/{step['id']}/complete", headers=auth_headers)

    badges = client.get("/v1/me/badges", headers=auth_headers).json()
    assert len(badges) == 1
    assert badges[0]["name"] == "Star"
    ctx = client.get("/v1/me/context", headers=auth_headers).json()
    assert ctx["missions"]["completed_mission_ids"] == [str(mid)]
    assert [b["name"] for b in ctx["badges"]] == ["Star"]


# ── Conversation history ──────────────────────────────────────────────────────

def test_conversation_roundtrip_and_context(client, auth_headers):
    assert client.post(
        "/v1/me/conversation", json={"role": "user", "content": "Explain contrast"}
    ).status_code == 401
    assert client.post(
        "/v1/me/conversation", json={"role": "alien", "content": "x"}, headers=auth_headers
    ).status_code == 422

    client.post(
        "/v1/me/conversation", json={"role": "user", "content": "Explain contrast"},
        headers=auth_headers,
    )
    client.post(
        "/v1/me/conversation", json={"role": "assistant", "content": "Contrast is..."},
        headers=auth_headers,
    )
    rows = client.get("/v1/me/conversation", headers=auth_headers).json()
    assert [m["role"] for m in rows] == ["user", "assistant"]

    ctx = client.get("/v1/me/context", headers=auth_headers).json()
    assert ctx["conversation_history"] == [
        {"role": "user", "content": "Explain contrast"},
        {"role": "assistant", "content": "Contrast is..."},
    ]

    r = client.delete("/v1/me/conversation", headers=auth_headers)
    assert "2" in r.json()["message"]
    assert client.get("/v1/me/conversation", headers=auth_headers).json() == []
    assert client.get("/v1/me/context", headers=auth_headers).json()["conversation_history"] == []


def test_conversation_pagination(client, auth_headers):
    for i in range(5):
        client.post(
            "/v1/me/conversation", json={"role": "user", "content": f"m{i}"},
            headers=auth_headers,
        )
    rows = client.get("/v1/me/conversation?limit=2&offset=1", headers=auth_headers).json()
    assert [m["content"] for m in rows] == ["m1", "m2"]


# ── Pagination elsewhere ──────────────────────────────────────────────────────

def test_course_list_pagination(client, admin_headers):
    for i in range(3):
        client.post("/v1/courses", json={**COURSE, "title": f"C{i}"}, headers=admin_headers)
    assert len(client.get("/v1/courses?mine=true&limit=2", headers=admin_headers).json()) == 2
    assert len(client.get("/v1/courses?mine=true&limit=2&offset=2", headers=admin_headers).json()) == 1


# ── Mission delete ────────────────────────────────────────────────────────────

def test_delete_mission(client, auth_headers):
    # Learners cannot author missions any more; adopting is the only route in.
    r = client.post("/v1/me/missions", json={"title": "Temp"},
                    headers=auth_headers)
    assert r.status_code == 422
    assert client.get("/v1/me/missions/99999", headers=auth_headers).status_code == 404


# ── Production secret guard ───────────────────────────────────────────────────

def test_production_secret_guard():
    from app.config import Settings

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings(
            environment="prod",
            database_url="postgresql://x",
            secret_key="too-short",
        ).ensure_production_secrets()
    # A strong secret still isn't enough: production must have a real payment
    # provider, or the stub would hand paid courses away for free.
    with pytest.raises(RuntimeError, match="PAYSTACK_SECRET_KEY"):
        Settings(
            environment="prod",
            database_url="postgresql://x",
            secret_key="a-strong-random-value-with-32plus-chars",
        ).ensure_production_secrets()
    Settings(
        environment="prod",
        database_url="postgresql://x",
        secret_key="a-strong-random-value-with-32plus-chars",
        paystack_secret_key="sk_live_x",
    ).ensure_production_secrets()
    Settings().ensure_production_secrets()


# ── Google OAuth ──────────────────────────────────────────────────────────────

def test_google_auth_unconfigured_is_503(client, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "google_client_id", "")
    r = client.post("/v1/auth/google", json={"id_token": "anything"})
    assert r.status_code == 503


def test_google_auth_creates_and_links_user(client, monkeypatch):
    from app import config as config_module
    from app.routers import auth as auth_module

    monkeypatch.setattr(config_module.settings, "google_client_id",
                        "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(
        auth_module, "verify_google_token",
        lambda token: {"sub": "google-123", "email": "guser@example.com",
                       "email_verified": True, "name": "G User"}
        if token == "good-token" else None,
    )

    r = client.post("/v1/auth/google", json={"id_token": "bad-token"})
    assert r.status_code == 401

    r = client.post("/v1/auth/google", json={"id_token": "good-token"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert r.json()["refresh_token"]

    me = client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
    ).json()
    assert me["email"] == "guser@example.com"
    assert me["learner_name"] == "G User"

    # second sign-in links the same account, no duplicate
    r2 = client.post("/v1/auth/google", json={"id_token": "good-token"})
    assert r2.status_code == 200
    me2 = client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {r2.json()['access_token']}"},
    ).json()
    assert me2["id"] == me["id"]


def test_google_auth_rejects_unverified_email(client, monkeypatch):
    from app import config as config_module
    from app.routers import auth as auth_module

    monkeypatch.setattr(config_module.settings, "google_client_id", "test-id")
    monkeypatch.setattr(
        auth_module, "verify_google_token",
        lambda token: {"sub": "g-1", "email": "nope@example.com",
                       "email_verified": False},
    )
    assert client.post(
        "/v1/auth/google", json={"id_token": "x"}).status_code == 403


# ── Email availability ────────────────────────────────────────────────────────

def test_email_available_flow(client, auth_headers):
    r = client.get("/v1/auth/email-available",
                   params={"email": "fresh@example.com"})
    assert r.status_code == 200
    assert r.json() == {"email": "fresh@example.com", "available": True}

    r = client.get("/v1/auth/email-available",
                   params={"email": "zainab@example.com"})
    assert r.json()["available"] is False

    assert client.get("/v1/auth/email-available",
                      params={"email": "not-an-email"}).status_code == 422
    assert client.get("/v1/auth/email-available").status_code == 422
