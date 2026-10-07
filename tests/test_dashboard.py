"""Daily check-ins and the learner dashboard.

A check-in is one tap that says "I showed up": the first tap of the day
records a row and awards XP through the normal ledger (so the XP streak
moves), repeats return the row with nothing further. The dashboard is the
read-only aggregate behind the Dashboard and Progress pages.
"""
from datetime import datetime, timezone

import pytest


@pytest.fixture
def course_ids(client, creator_headers, db_session):
    """Two published single-lesson courses."""
    from app.models import Course

    ids = []
    for n in (1, 2):
        cid = client.post("/v1/courses", json={
            "title": f"Dash Course {n}",
            "description": "Used by dashboard tests",
            "category": "Design",
            "outcomes": ["Learn something"],
            "target_audience": "Everyone",
        }, headers=creator_headers).json()["id"]
        mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                          headers=creator_headers).json()["id"]
        client.post(f"/v1/courses/{cid}/lessons", json={
            "module_id": mid, "title": f"L{n}", "topic": "T",
            "lesson_type": "lesson", "content": "C",
        }, headers=creator_headers)
        db_session.get(Course, cid).status = "published"
        db_session.commit()
        ids.append(cid)
    return ids


# ── Check-ins ────────────────────────────────────────────────────────────────


def test_first_check_in_of_the_day_awards_xp(client, learner_headers,
                                             db_session):
    from app.models import CheckIn, XpEvent

    r = client.post("/v1/me/check-ins", headers=learner_headers,
                    json={"mood": "focused", "note": "Morning session"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["xp_awarded"] == 10
    assert body["mood"] == "focused"
    assert body["date"] == datetime.now(timezone.utc).date().isoformat()

    assert db_session.query(CheckIn).count() == 1
    assert db_session.query(XpEvent).filter(
        XpEvent.activity == "streak").count() == 1


def test_second_tap_same_day_is_free(client, learner_headers, db_session):
    from app.models import CheckIn, XpEvent

    assert client.post("/v1/me/check-ins", headers=learner_headers,
                       json={}).status_code == 201
    r = client.post("/v1/me/check-ins", headers=learner_headers,
                    json={"mood": "tired"})
    assert r.status_code == 200
    assert r.json()["xp_awarded"] == 0
    # The first tap's mood stands; repeats do not overwrite.
    assert r.json()["mood"] is None
    assert db_session.query(CheckIn).count() == 1
    assert db_session.query(XpEvent).count() == 1


def test_check_in_history_is_newest_first(client, learner_headers, db_session):
    from datetime import timedelta

    from app.models import CheckIn, User

    user = db_session.query(User).filter(
        User.email == "upload-learner@example.com").one()
    today = datetime.now(timezone.utc).date()
    for back in (2, 0):
        db_session.add(CheckIn(user_id=user.id,
                               check_date=today - timedelta(days=back)))
    db_session.commit()

    dates = [c["date"] for c in client.get(
        "/v1/me/check-ins", headers=learner_headers).json()]
    assert dates == sorted(dates, reverse=True)
    assert len(dates) == 2


def test_check_in_needs_onboarding(client, db_session):
    client.post("/v1/auth/register", json={
        "email": "freshcheck@example.com", "password": "password123",
        "learner_name": "Fresh",
    })
    token = client.post("/v1/auth/login", json={
        "email": "freshcheck@example.com", "password": "password123",
    }).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/v1/me/check-ins", headers=h, json={}).status_code == 409


# ── Dashboard ────────────────────────────────────────────────────────────────


def test_dashboard_reports_learning_state(client, learner_headers, course_ids,
                                          db_session):
    from sqlalchemy import select

    from app.models import Lesson

    client.post(f"/v1/me/enroll/{course_ids[0]}", headers=learner_headers)
    lesson = db_session.scalar(
        select(Lesson).where(Lesson.course_id == course_ids[0]))
    client.post(f"/v1/me/lessons/{lesson.id}/complete", headers=learner_headers)
    client.post("/v1/me/quiz-results", headers=learner_headers, json={
        "course_id": course_ids[0], "topic": "T", "score_percent": 80,
    })
    client.post("/v1/me/check-ins", headers=learner_headers, json={})

    body = client.get("/v1/me/dashboard", headers=learner_headers).json()

    assert len(body["enrollments"]) == 1
    assert body["enrollments"][0]["lessons_total"] == 1
    assert body["enrollments"][0]["lessons_completed"] == 1
    assert body["enrollments"][0]["completed"] is True
    assert body["xp_total"] > 0
    assert body["level"] >= 1
    assert body["level_title"]
    assert body["streak_days"] >= 1
    assert body["quiz"]["quizzes_taken"] == 1
    assert body["quiz"]["average_score"] == 80.0
    assert body["quiz"]["topics_attempted"] == 1
    assert len(body["recent_activity"]) >= 1
    assert body["checked_in_today"] is True
    assert len(body["checkins_last_7_days"]) == 1
    assert body["onboarding_next_step"] == "done"


def test_dashboard_includes_saved_paths(client, learner_headers, course_ids):
    pid = client.post("/v1/me/paths", headers=learner_headers, json={
        "title": "My Path", "course_ids": course_ids,
    }).json()["id"]

    paths = client.get("/v1/me/dashboard",
                       headers=learner_headers).json()["learning_paths"]
    assert [p["id"] for p in paths] == [pid]
    assert paths[0]["courses_total"] == 2


def test_dashboard_for_a_brand_new_learner(client, learner_headers):
    body = client.get("/v1/me/dashboard", headers=learner_headers).json()
    assert body["enrollments"] == []
    assert body["learning_paths"] == []
    assert body["xp_total"] == 0
    assert body["streak_days"] == 0
    assert body["quiz"] == {"quizzes_taken": 0, "average_score": 0.0,
                            "topics_attempted": 0}
    assert body["recent_activity"] == []
    assert body["checked_in_today"] is False
    assert body["study_plan"] is None


def test_dashboard_needs_auth(client):
    assert client.get("/v1/me/dashboard").status_code == 401
