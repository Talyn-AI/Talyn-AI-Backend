"""Tests for coach profile reads + difficulty validation."""
import pytest


@pytest.fixture()
def auth_headers(client, onboard):
    client.post(
        "/v1/auth/register",
        json={
            "email": "profile@example.com",
            "password": "secret12345",
            "learner_name": "Profiler",
            "difficulty_level": "intermediate",
            "goals": "Ship projects",
            "interests": ["design", "music"],
            "current_course": "UI/UX Design Fundamentals",
            "current_topic": "Typography",
        },
    )
    r = client.post("/v1/auth/login", json={"email": "profile@example.com",
                                            "password": "secret12345"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture()
def active_learner(client, auth_headers, admin_headers, db_session):
    from app.models import Course

    cid = client.post(
        "/v1/courses",
        json={
            "title": "Design Basics",
            "description": "d",
            "difficulty_level": "beginner",
            "lessons": [
                {"order": 1, "title": "Type", "topic": "Typography",
                 "estimated_minutes": 30},
                {"order": 2, "title": "Color", "topic": "Color Theory",
                 "estimated_minutes": 30},
            ],
        },
        headers=admin_headers,
    ).json()["id"]
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    client.post(f"/v1/me/enroll/{cid}", headers=auth_headers)
    lessons = client.get(f"/v1/courses/{cid}/lessons").json()
    client.post(f"/v1/me/lessons/{lessons[0]['id']}/complete", json={},
                headers=auth_headers)
    client.post("/v1/me/quiz-results",
                json={"topic": "Typography", "score_percent": 70.0},
                headers=auth_headers)
    client.put("/v1/me/study-plan",
               json={"daily_goal_minutes": 60, "weekly_target_lessons": 5,
                     "focus_topics": ["Typography"]},
               headers=auth_headers)
    return cid


def test_personalization_profile(client, auth_headers):
    r = client.get("/v1/me/personalization-profile", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "learner_id": body["learner_id"],
        "learner_name": "Profiler",
        "interests": ["design", "music"],
        "difficulty_level": "intermediate",
        "current_course": "UI/UX Design Fundamentals",
        "current_topic": "Typography",
    }
    assert client.get("/v1/me/personalization-profile").status_code == 401


def test_path_profile(client, auth_headers, active_learner):
    body = client.get("/v1/me/path-profile", headers=auth_headers).json()
    assert body["skill_level"] == "intermediate"
    assert body["hours_per_week"] == 7.0  # 60 min/day from study plan
    assert body["goals"] == "Ship projects"
    assert body["completed_course_ids"] == []  # enrolled, not finished
    assert len(body["available_courses"]) == 1
    course = body["available_courses"][0]
    assert course["title"] == "Design Basics"
    assert course["skill_level"] == "beginner"
    assert course["estimated_hours"] == 1.0
    assert course["topics"] == ["Typography", "Color Theory"]


def test_path_profile_completed_ids(client, auth_headers, active_learner):
    for lesson in client.get(
        f"/v1/courses/{active_learner}/lessons"
    ).json():
        client.post(f"/v1/me/lessons/{lesson['id']}/complete", json={},
                    headers=auth_headers)
    body = client.get("/v1/me/path-profile", headers=auth_headers).json()
    assert body["completed_course_ids"] == [str(active_learner)]


def test_revision_profile(client, auth_headers, active_learner):
    body = client.get("/v1/me/revision-profile", headers=auth_headers).json()
    assert body["daily_study_minutes"] == 60
    assert body["difficulty_level"] == "intermediate"
    assert body["schedule_start_date"]
    by_topic = {t["topic"]: t for t in body["topic_records"]}
    assert set(by_topic) == {"Typography"}  # only encountered topics
    typo = by_topic["Typography"]
    assert typo["lesson"] == "Type"
    assert typo["lesson_completed"] is True
    assert typo["quiz_score_percent"] == 70.0
    assert typo["days_since_studied"] == 0
    assert typo["times_reviewed"] >= 1


def test_revision_profile_empty_for_new_learner(client, auth_headers):
    body = client.get("/v1/me/revision-profile", headers=auth_headers).json()
    assert body["topic_records"] == []
    assert body["daily_study_minutes"] == 30


def test_difficulty_validated_on_writes(client, auth_headers):
    r = client.post(
        "/v1/auth/register",
        json={"email": "bad@example.com", "password": "secret12345",
              "learner_name": "Bad", "difficulty_level": "expert"},
    )
    assert r.status_code == 422
    r = client.patch("/v1/users/me", json={"difficulty_level": "guru"},
                     headers=auth_headers)
    assert r.status_code == 422
    r = client.post(
        "/v1/courses",
        json={"title": "X", "difficulty_level": "nightmare"},
        headers=auth_headers,
    )
    assert r.status_code in (422, 403)  # 403 for non-admin hits first
