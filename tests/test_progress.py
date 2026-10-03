"""Tests for courses, progress, and XP endpoints."""
import pytest


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={
            "email": "chiamaka@example.com",
            "password": "secret12345",
            "learner_name": "Chiamaka",
            "difficulty_level": "beginner",
            "goals": "Learn web development",
            "interests": ["music", "technology"],
        },
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "chiamaka@example.com", "password": "secret12345"},
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture()
def course_id(client, admin_headers):
    r = client.post(
        "/v1/courses",
        json={
            "title": "Web Dev Basics",
            "description": "Intro course",
            "difficulty_level": "beginner",
            "lessons": [
                {"order": 1, "title": "HTML Fundamentals", "topic": "HTML", "estimated_minutes": 15},
                {"order": 2, "title": "CSS Selectors", "topic": "CSS Selectors", "estimated_minutes": 20},
                {"order": 3, "title": "Flexbox Layout", "topic": "CSS Flexbox", "estimated_minutes": 25},
            ],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Courses ──────────────────────────────────────────────────────────────────

def test_create_course(course_id):
    assert course_id >= 1


def test_list_courses(client, course_id, admin_headers):
    # drafts are hidden from public discovery
    assert client.get("/v1/courses").json() == []
    # owners see their own drafts via mine=true
    r = client.get("/v1/courses?mine=true", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "Web Dev Basics"
    assert body[0]["lesson_count"] == 3
    assert body[0]["status"] == "draft"


def test_get_course_with_lessons(client, course_id, admin_headers):
    # anonymous callers cannot see drafts
    assert client.get(f"/v1/courses/{course_id}").status_code == 401
    r = client.get(f"/v1/courses/{course_id}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["lessons"]) == 3
    assert body["lessons"][0]["order"] == 1
    assert body["lessons"][0]["title"] == "HTML Fundamentals"


def test_get_course_not_found(client):
    r = client.get("/v1/courses/99999")
    assert r.status_code == 404


def test_list_lessons_ordered(client, course_id, admin_headers):
    r = client.get(f"/v1/courses/{course_id}/lessons", headers=admin_headers)
    assert r.status_code == 200
    titles = [l["title"] for l in r.json()]
    assert titles == ["HTML Fundamentals", "CSS Selectors", "Flexbox Layout"]


# ── Enroll + progress ────────────────────────────────────────────────────────

def test_enroll(client, auth_headers, course_id):
    r = client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["enrolled"] is True


def test_enroll_unknown_course(client, auth_headers):
    r = client.post("/v1/me/enroll/99999", headers=auth_headers)
    assert r.status_code == 404


def test_complete_lesson_awards_xp(client, auth_headers, course_id, admin_headers):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{course_id}/lessons", headers=admin_headers).json()
    lesson_id = lessons[0]["id"]

    r = client.post(f"/v1/me/lessons/{lesson_id}/complete", json={}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is True
    assert body["xp_awarded"] == 25  # canonical lesson XP

    # completing again awards nothing
    r2 = client.post(f"/v1/me/lessons/{lesson_id}/complete", json={}, headers=auth_headers)
    assert r2.json()["xp_awarded"] == 0
    assert r2.json()["already_completed"] is True


def test_complete_unknown_lesson(client, auth_headers):
    r = client.post("/v1/me/lessons/99999/complete", json={}, headers=auth_headers)
    assert r.status_code == 404


# ── Quiz results ─────────────────────────────────────────────────────────────

def test_submit_quiz(client, auth_headers):
    r = client.post(
        "/v1/me/quiz-results",
        json={"topic": "CSS Selectors", "score_percent": 75.0, "attempts": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["score_percent"] == 75.0


# ── XP summary ───────────────────────────────────────────────────────────────

def test_xp_summary(client, auth_headers, course_id, admin_headers):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{course_id}/lessons", headers=admin_headers).json()
    client.post(f"/v1/me/lessons/{lessons[0]['id']}/complete", json={}, headers=auth_headers)
    client.post(
        "/v1/me/quiz-results",
        json={"topic": "HTML", "score_percent": 90.0},
        headers=auth_headers,
    )

    r = client.get("/v1/me/xp", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # lesson 25 + quiz 10 + 9 (90//10) = 44
    assert body["xp_total"] == 44
    assert body["xp_this_week"] == 44
    assert body["level"] == 1
    assert body["level_title"] == "Curious Explorer"
    assert len(body["breakdown"]) == 2

    # me requires auth
    assert client.get("/v1/me/xp").status_code == 401


# ── Learner context (AI coach contract) ──────────────────────────────────────

def test_learner_context(client, auth_headers, course_id, admin_headers):
    client.post(f"/v1/me/enroll/{course_id}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{course_id}/lessons", headers=admin_headers).json()
    client.post(f"/v1/me/lessons/{lessons[0]['id']}/complete", json={}, headers=auth_headers)
    client.post(
        "/v1/me/quiz-results",
        json={"topic": "CSS Selectors", "score_percent": 55.0, "attempts": 2},
        headers=auth_headers,
    )

    r = client.get("/v1/me/context", headers=auth_headers)
    assert r.status_code == 200
    ctx = r.json()

    assert ctx["learner_name"] == "Chiamaka"
    assert ctx["xp_total"] == 25 + 10 + 5  # lesson 25 + quiz 10 + 55//10=5
    assert ctx["lessons_total"] == 3
    assert ctx["lessons_completed"] == 1
    assert ctx["completion_percent"] == round(1 / 3 * 100, 1)
    assert ctx["quiz_performance"] == [
        {"topic": "CSS Selectors", "score_percent": 55.0, "attempts": 2, "last_attempt_date": ctx["quiz_performance"][0]["last_attempt_date"]}
    ]
    assert ctx["level"]["level"] == 1
    assert ctx["badges"] == []
    assert ctx["missions"]["active_mission"] is None
    assert ctx["missions"]["completed_mission_count"] == 0
    # matches the AI coach's LearnerContext field names
    for field in (
        "learner_id", "learner_name", "current_course", "current_lesson",
        "current_topic", "interests", "difficulty_level", "goals", "xp_total",
        "xp_this_week", "streak_days", "lessons_completed", "lessons_total",
        "completion_percent", "quiz_performance",
    ):
        assert field in ctx