"""Tests for the auth endpoints."""
import pytest

VALID_USER = {
    "email": "adeola@example.com",
    "password": "secret12345",
    "learner_name": "Adeola",
    "difficulty_level": "beginner",
    "goals": "Become a junior UI/UX designer",
    "interests": ["football", "music"],
    "current_course": "UI/UX Design Fundamentals",
    "current_lesson": "Lesson 4: Layout & Spacing",
    "current_topic": "CSS Flexbox",
}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_success(client):
    r = client.post("/v1/auth/register", json=VALID_USER)
    assert r.status_code == 201
    body = r.json()
    assert body["learner_name"] == "Adeola"
    assert body["email"] == "adeola@example.com"
    assert body["id"] is not None
    # password must never leak
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email(client):
    client.post("/v1/auth/register", json=VALID_USER)
    r = client.post("/v1/auth/register", json=VALID_USER)
    assert r.status_code == 409


def test_register_short_password(client):
    payload = dict(VALID_USER, email="tunde@example.com", password="short")
    r = client.post("/v1/auth/register", json=payload)
    assert r.status_code == 422


def test_login_success_returns_token(client):
    client.post("/v1/auth/register", json=VALID_USER)
    r = client.post(
        "/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password(client):
    client.post("/v1/auth/register", json=VALID_USER)
    r = client.post(
        "/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "wrongpass"},
    )
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever1"})
    assert r.status_code == 401


@pytest.fixture()
def auth_headers(client):
    client.post("/v1/auth/register", json=VALID_USER)
    r = client.post(
        "/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    )
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_me_requires_token(client):
    assert client.get("/v1/users/me").status_code == 401


def test_me_with_token(client, auth_headers):
    r = client.get("/v1/users/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["learner_name"] == "Adeola"
    assert body["interests"] == ["football", "music"]


def test_me_invalid_token(client):
    r = client.get("/v1/users/me", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


def test_update_me(client, auth_headers):
    r = client.patch("/v1/users/me", json={"goals": "Full-stack developer"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["goals"] == "Full-stack developer"