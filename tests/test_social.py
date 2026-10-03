"""Tests for community, messaging, and live sessions."""
import pytest
from datetime import datetime, timedelta, timezone


def _register(client, email, name, is_creator=False):
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "secret12345",
              "learner_name": name, "is_creator": is_creator},
    )
    r = client.post("/v1/auth/login",
                    json={"email": email, "password": "secret12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def creator_headers(client, onboard):
    headers = _register(client, "soc-creator@example.com", "SocCreator",
                          is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def learner_headers(client, onboard):
    headers = _register(client, "soc-learner@example.com", "SocLearner")
    onboard(headers)
    return headers

@pytest.fixture()
def learner2_headers(client):
    return _register(client, "soc-learner2@example.com", "SocLearner2")


def _user_id(client, headers):
    return client.get("/v1/users/me", headers=headers).json()["id"]


@pytest.fixture()
def published_course(client, creator_headers, db_session):
    from app.models import Course

    cid = client.post(
        "/v1/courses",
        json={"title": "Live Course", "description": "d", "category": "C",
              "outcomes": ["O"], "target_audience": "A",
              "thumbnail_key": "t.png", "course_type": "paid",
              "price_naira": 5000},
        headers=creator_headers,
    ).json()["id"]
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


# ── Community ─────────────────────────────────────────────────────────────────

def test_post_lifecycle(client, learner_headers, creator_headers):
    assert client.post("/v1/community/posts",
                       json={"title": "T", "body": "B"}).status_code == 401
    pid = client.post("/v1/community/posts",
                      json={"title": "Hello", "body": "First post"},
                      headers=learner_headers).json()["id"]

    rows = client.get("/v1/community/posts").json()
    assert len(rows) == 1
    assert rows[0]["author_name"] == "SocLearner"
    assert rows[0]["reply_count"] == 0

    r = client.post(f"/v1/community/posts/{pid}/replies",
                    json={"body": "Welcome!"},
                    headers=creator_headers)
    assert r.status_code == 201

    detail = client.get(f"/v1/community/posts/{pid}").json()
    assert detail["reply_count"] == 1
    assert detail["replies"][0]["author_name"] == "SocCreator"

    # non-author cannot delete
    assert client.delete(f"/v1/community/posts/{pid}",
                         headers=creator_headers).status_code == 403
    assert client.delete(f"/v1/community/posts/{pid}",
                         headers=learner_headers).status_code == 200
    assert client.get(f"/v1/community/posts/{pid}").status_code == 404


def test_reply_delete_rules(client, learner_headers, creator_headers):
    pid = client.post("/v1/community/posts",
                      json={"title": "T", "body": "B"},
                      headers=learner_headers).json()["id"]
    rid = client.post(f"/v1/community/posts/{pid}/replies",
                      json={"body": "R"},
                      headers=creator_headers).json()["id"]
    # post author cannot delete another's reply; reply author can
    assert client.delete(f"/v1/community/posts/{pid}/replies/{rid}",
                         headers=learner_headers).status_code == 403
    assert client.delete(f"/v1/community/posts/{pid}/replies/{rid}",
                         headers=creator_headers).status_code == 200
    assert client.post("/v1/community/posts/99999/replies",
                       json={"body": "x"},
                       headers=learner_headers).status_code == 404


# ── Messages ──────────────────────────────────────────────────────────────────

def test_messaging_flow(client, learner_headers, learner2_headers):
    u2 = _user_id(client, learner2_headers)
    assert client.post("/v1/messages",
                       json={"recipient_id": u2, "body": "Hi!"},
                       headers=learner_headers).status_code == 201
    assert client.post("/v1/messages",
                       json={"recipient_id": 99999, "body": "Hi!"},
                       headers=learner_headers).status_code == 404
    me = _user_id(client, learner_headers)
    assert client.post("/v1/messages",
                       json={"recipient_id": me, "body": "self"},
                       headers=learner_headers).status_code == 422

    threads = client.get("/v1/messages/threads",
                         headers=learner2_headers).json()
    assert len(threads) == 1
    assert threads[0]["other_name"] == "SocLearner"
    assert threads[0]["unread_count"] == 1

    history = client.get(f"/v1/messages/with/{me}",
                         headers=learner2_headers).json()
    assert [m["body"] for m in history] == ["Hi!"]

    r = client.post(f"/v1/messages/with/{me}/read", headers=learner2_headers)
    assert "1" in r.json()["message"]
    threads = client.get("/v1/messages/threads",
                         headers=learner2_headers).json()
    assert threads[0]["unread_count"] == 0


def test_message_delete_rules(client, learner_headers, learner2_headers):
    u2 = _user_id(client, learner2_headers)
    mid = client.post("/v1/messages",
                      json={"recipient_id": u2, "body": "Hi!"},
                      headers=learner_headers).json()["id"]
    # recipient cannot delete another's message
    assert client.delete(f"/v1/messages/{mid}",
                         headers=learner2_headers).status_code == 403
    assert client.delete(f"/v1/messages/{mid}",
                         headers=learner_headers).status_code == 200


# ── Live sessions ─────────────────────────────────────────────────────────────

def _future():
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def test_live_lifecycle(client, creator_headers, learner_headers,
                        published_course):
    assert client.post(
        f"/v1/courses/{published_course}/live",
        json={"title": "Q&A", "scheduled_at": _future(),
              "meeting_url": "https://meet.example/q"},
        headers=learner_headers).status_code == 403
    sid = client.post(
        f"/v1/courses/{published_course}/live",
        json={"title": "Q&A", "scheduled_at": _future(),
              "duration_minutes": 45,
              "meeting_url": "https://meet.example/q"},
        headers=creator_headers).json()["id"]

    rows = client.get(f"/v1/courses/{published_course}/live").json()
    assert len(rows) == 1
    assert rows[0]["meeting_url"] is None  # anonymous: no join link

    detail = client.get(f"/v1/live/{sid}",
                        headers=learner_headers).json()
    assert detail["meeting_url"] is None  # not enrolled yet

    client.post(f"/v1/courses/{published_course}/purchase",
                headers=learner_headers)
    detail = client.get(f"/v1/live/{sid}",
                        headers=learner_headers).json()
    assert detail["meeting_url"] == "https://meet.example/q"

    assert client.post(f"/v1/live/{sid}/end",
                       headers=creator_headers).status_code == 409
    assert client.post(f"/v1/live/{sid}/start",
                       headers=creator_headers).json()["status"] == "live"
    assert client.post(f"/v1/live/{sid}/end",
                       headers=creator_headers).json()["status"] == "ended"
    assert client.post(f"/v1/live/{sid}/cancel",
                       headers=creator_headers).status_code == 409


def test_live_validation(client, creator_headers, published_course):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = client.post(f"/v1/courses/{published_course}/live",
                    json={"title": "Old", "scheduled_at": past},
                    headers=creator_headers)
    assert r.status_code == 422
    assert client.get("/v1/live/99999",
                      headers=creator_headers).status_code == 404


def test_live_draft_visibility(client, creator_headers, learner_headers):
    cid = client.post("/v1/courses", json={"title": "DraftLive"},
                      headers=creator_headers).json()["id"]
    assert client.get(f"/v1/courses/{cid}/live").status_code == 401
    assert client.get(f"/v1/courses/{cid}/live",
                      headers=learner_headers).status_code == 403
    assert client.get(f"/v1/courses/{cid}/live",
                      headers=creator_headers).json() == []


def test_course_delete_cleans_live(client, creator_headers, published_course):
    client.post(f"/v1/courses/{published_course}/live",
                json={"title": "Q", "scheduled_at": _future()},
                headers=creator_headers)
    assert client.delete(f"/v1/courses/{published_course}",
                         headers=creator_headers).status_code == 200
    assert client.get(f"/v1/courses/{published_course}/live").status_code == 404


def test_account_delete_cleans_social(client, learner_headers,
                                      learner2_headers):
    u2 = _user_id(client, learner2_headers)
    client.post("/v1/messages",
                json={"recipient_id": u2, "body": "Hi!"},
                headers=learner_headers)
    client.post("/v1/community/posts",
                json={"title": "Bye", "body": "Leaving"},
                headers=learner_headers)
    assert client.delete("/v1/users/me",
                         headers=learner_headers).status_code == 200
    assert client.get("/v1/messages/threads",
                      headers=learner2_headers).json() == []
    assert client.get("/v1/community/posts").json() == []
