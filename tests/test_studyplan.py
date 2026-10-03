"""Tests for study-plan persistence and enrollment progress aggregation."""
import pytest


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={
            "email": "amina@example.com",
            "password": "secret12345",
            "learner_name": "Amina",
            "difficulty_level": "intermediate",
            "goals": "Finish the design course",
            "interests": ["design"],
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "amina@example.com", "password": "secret12345"},
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture()
def course_id(client, admin_headers):
    r = client.post(
        "/v1/courses",
        json={
            "title": "Design Sprint",
            "description": "A short course",
            "difficulty_level": "beginner",
            "lessons": [
                {"order": 1, "title": "L1", "topic": "T1", "estimated_minutes": 10},
                {"order": 2, "title": "L2", "topic": "T2", "estimated_minutes": 10},
            ],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


PLAN = {
    "daily_goal_minutes": 45,
    "weekly_target_lessons": 4,
    "focus_topics": ["Typography", "Color Theory"],
    "deadline": "2026-12-31",
}


# ── Study plan ────────────────────────────────────────────────────────────────

def test_study_plan_missing_at_first(client, auth_headers):
    assert client.get("/v1/me/study-plan", headers=auth_headers).status_code == 404
    assert client.get("/v1/me/context", headers=auth_headers).json()["study_plan"] is None


def test_upsert_and_get_study_plan(client, auth_headers):
    r = client.put("/v1/me/study-plan", json=PLAN, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["focus_topics"] == ["Typography", "Color Theory"]
    assert r.json()["daily_goal_minutes"] == 45

    r = client.get("/v1/me/study-plan", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deadline"] == "2026-12-31"


def test_upsert_replaces_not_duplicates(client, auth_headers):
    client.put("/v1/me/study-plan", json=PLAN, headers=auth_headers)
    r = client.put(
        "/v1/me/study-plan",
        json={**PLAN, "daily_goal_minutes": 20, "focus_topics": []},
        headers=auth_headers,
    )
    assert r.json()["daily_goal_minutes"] == 20
    assert r.json()["focus_topics"] == []


def test_study_plan_validation(client, auth_headers):
    r = client.put(
        "/v1/me/study-plan", json={**PLAN, "daily_goal_minutes": 0}, headers=auth_headers
    )
    assert r.status_code == 422


def test_context_includes_study_plan(client, auth_headers):
    client.put("/v1/me/study-plan", json=PLAN, headers=auth_headers)
    ctx = client.get("/v1/me/context", headers=auth_headers).json()
    assert ctx["study_plan"]["weekly_target_lessons"] == 4
    assert ctx["study_plan"]["focus_topics"] == ["Typography", "Color Theory"]


def test_study_plan_requires_auth(client):
    assert client.get("/v1/me/study-plan").status_code == 401
    assert client.put("/v1/me/study-plan", json=PLAN).status_code == 401


# ── Enrollments + auto-completion ─────────────────────────────────────────────

def test_enrollments_progress(client, auth_headers, course_id):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    rows = client.get("/v1/me/enrollments", headers=auth_headers).json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Design Sprint"
    assert rows[0]["lessons_total"] == 2
    assert rows[0]["lessons_completed"] == 0
    assert rows[0]["completion_percent"] == 0.0
    assert rows[0]["completed"] is False


def test_finish_all_lessons_completes_enrollment(client, auth_headers, course_id,
                                                     admin_headers):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{course_id}/lessons",
                         headers=admin_headers).json()
    for lesson in lessons:
        r = client.post(
            f"/v1/me/lessons/{lesson['id']}/complete", json={}, headers=auth_headers
        )
        assert r.status_code == 200

    rows = client.get("/v1/me/enrollments", headers=auth_headers).json()
    assert rows[0]["lessons_completed"] == 2
    assert rows[0]["completion_percent"] == 100.0
    assert rows[0]["completed"] is True


def test_partial_progress_not_completed(client, auth_headers, course_id,
                                          admin_headers):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{course_id}/lessons",
                         headers=admin_headers).json()
    client.post(f"/v1/me/lessons/{lessons[0]['id']}/complete", json={}, headers=auth_headers)

    rows = client.get("/v1/me/enrollments", headers=auth_headers).json()
    assert rows[0]["completion_percent"] == 50.0
    assert rows[0]["completed"] is False


def test_enrollments_empty_when_not_enrolled(client, auth_headers):
    assert client.get("/v1/me/enrollments", headers=auth_headers).json() == []
    assert client.get("/v1/me/enrollments").status_code == 401
