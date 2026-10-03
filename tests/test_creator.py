"""Tests for creator role, ownership, metadata, discovery, and dashboard."""
import pytest


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
    headers = _register(client, "creator@example.com", "Creator", is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def creator2_headers(client):
    return _register(client, "creator2@example.com", "Creator2", is_creator=True)


@pytest.fixture()
def learner_headers(client, onboard):
    headers = _register(client, "plain@example.com", "Plain")
    onboard(headers)
    return headers

@pytest.fixture()
def course_id(client, creator_headers):
    r = client.post(
        "/v1/courses",
        json={
            "title": "Naira Design",
            "description": "Design course",
            "difficulty_level": "beginner",
            "category": "Design",
            "outcomes": ["Ship screens", "Price work"],
            "target_audience": "Beginners",
            "requirements": "A laptop",
            "course_type": "paid",
            "price_naira": 15000,
            "lessons": [
                {"order": 1, "title": "Intro", "topic": "Intro",
                 "estimated_minutes": 10},
            ],
        },
        headers=creator_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_creator_flag_on_register(client, creator_headers):
    body = client.get("/v1/users/me", headers=creator_headers).json()
    assert body["is_creator"] is True
    assert body["is_admin"] is False


def test_non_creator_cannot_create_or_profile(client, learner_headers):
    assert client.post(
        "/v1/courses", json={"title": "X"}, headers=learner_headers
    ).status_code == 403
    assert client.put(
        "/v1/me/creator/profile", json={"display_name": "X"},
        headers=learner_headers,
    ).status_code == 403
    assert client.get("/v1/me/creator/dashboard",
                      headers=learner_headers).status_code == 403


def test_course_metadata_and_owner(client, creator_headers, course_id):
    body = client.get(f"/v1/courses/{course_id}",
                      headers=creator_headers).json()
    me = client.get("/v1/users/me", headers=creator_headers).json()
    assert body["creator_user_id"] == me["id"]
    assert body["category"] == "Design"
    assert body["outcomes"] == ["Ship screens", "Price work"]
    assert body["target_audience"] == "Beginners"
    assert body["course_type"] == "paid"
    assert body["price_naira"] == 15000
    assert body["status"] == "draft"


def test_paid_requires_price(client, creator_headers):
    r = client.post(
        "/v1/courses",
        json={"title": "NoPrice", "course_type": "paid", "price_naira": 0},
        headers=creator_headers,
    )
    assert r.status_code == 422
    cid = client.post(
        "/v1/courses", json={"title": "Freebie"}, headers=creator_headers
    ).json()["id"]
    r = client.patch(f"/v1/courses/{cid}", json={"course_type": "paid"},
                     headers=creator_headers)
    assert r.status_code == 422
    r = client.patch(
        f"/v1/courses/{cid}",
        json={"course_type": "paid", "price_naira": 5000,
              "category": "Design"},
        headers=creator_headers,
    )
    assert r.status_code == 200
    assert r.json()["price_naira"] == 5000


def test_ownership_enforced(client, creator_headers, creator2_headers, course_id,
                            admin_headers):
    other = {"title": "Hacked"}
    assert client.patch(f"/v1/courses/{course_id}", json=other,
                        headers=creator2_headers).status_code == 403
    assert client.delete(f"/v1/courses/{course_id}",
                         headers=creator2_headers).status_code == 403
    r = client.patch(f"/v1/courses/{course_id}", json=other,
                     headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "Hacked"


def test_discovery_filters(client, creator_headers, course_id):
    assert client.get("/v1/courses").json() == []  # draft hidden
    r = client.get("/v1/courses?category=Design")
    assert r.status_code == 200  # draft still hidden, but filter works
    assert client.get("/v1/courses?status=draft").status_code == 401
    rows = client.get("/v1/courses?status=draft&mine=true",
                      headers=creator_headers).json()
    assert [c["id"] for c in rows] == [course_id]
    rows = client.get("/v1/courses?q=naira").json()
    assert rows == []  # q works, draft hidden


def test_creator_profile_flow(client, creator_headers):
    assert client.get("/v1/me/creator/profile",
                      headers=creator_headers).status_code == 404
    r = client.put(
        "/v1/me/creator/profile",
        json={"display_name": "Design Coach", "bio": "I teach design"},
        headers=creator_headers,
    )
    assert r.status_code == 200
    me = client.get("/v1/users/me", headers=creator_headers).json()
    public = client.get(f"/v1/creators/{me['id']}/profile").json()
    assert public["display_name"] == "Design Coach"
    assert client.get("/v1/creators/99999/profile").status_code == 404


def test_dashboard_counts(client, creator_headers, learner_headers, course_id):
    dash = client.get("/v1/me/creator/dashboard",
                      headers=creator_headers).json()
    assert dash["total_courses"] == 1
    assert dash["published_courses"] == 0
    assert dash["draft_courses"] == 1
    assert dash["total_learners"] == 0
    assert dash["total_revenue_naira"] == 0
    assert [a["event"] for a in dash["recent_activity"]] == ["course_created"]
    client.post(f"/v1/me/enroll/{course_id}", headers=learner_headers)
    dash = client.get("/v1/me/creator/dashboard",
                      headers=creator_headers).json()
    assert dash["total_learners"] == 1
    assert [a["event"] for a in dash["recent_activity"]] == [
        "course_enrolled", "course_created"]
    own = client.get("/v1/me/creator/courses",
                     headers=creator_headers).json()
    assert [c["id"] for c in own] == [course_id]
    assert own[0]["status"] == "draft"
