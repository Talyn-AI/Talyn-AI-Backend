"""Timezone regressions.

Every one of these passed until the suite happened to run after a date
rollover. The cause was the same throughout: `date.today()` returns the
server's *local* date while the columns being compared are `timestamptz`
read back in UTC. On a host that is not set to UTC the two disagree for part
of every day, so "today" silently becomes "yesterday" � breaking streaks and
shifting revision priorities for real learners with no visible error.

If a host runs in UTC these pass either way, which is exactly why they went
unnoticed. The assertions below pin the behaviour, not the timezone.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models import LessonProgress, User, XpEvent
from app.services.context_builder import revision_profile
from app.services.xp import streak_days


@pytest.fixture()
def learner(client, db_session):
    client.post("/v1/auth/register", json={
        "email": "tz-learner@example.com", "password": "password123",
        "learner_name": "TzLearner",
    })
    return db_session.query(User).filter_by(
        email="tz-learner@example.com").one()


@pytest.fixture()
def creator_course(client, db_session):
    """A lesson the learner can complete, owned by a creator."""
    client.post("/v1/auth/register", json={
        "email": "tz-creator@example.com", "password": "password123",
        "learner_name": "TzCreator", "is_creator": True,
    })
    tok = client.post("/v1/auth/login", json={
        "email": "tz-creator@example.com", "password": "password123",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}

    cid = client.post("/v1/courses", json={
        "title": "Tz", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=headers).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=headers).json()["id"]
    return client.post(f"/v1/courses/{cid}/lessons", json={
        "module_id": mid, "title": "L", "topic": "Flexbox", "content": "c",
    }, headers=headers).json()["id"]


def test_streak_counts_consecutive_utc_days(client, db_session, learner):
    now = datetime.now(timezone.utc)
    for offset in (0, 1, 2):
        db_session.add(XpEvent(
            user_id=learner.id, amount=10, activity="lesson_complete",
            earned_date=(now - timedelta(days=offset)).date(),
        ))
    db_session.commit()

    assert streak_days(db_session, learner) == 3


def test_streak_survives_a_day_of_absence(client, db_session, learner):
    """Yesterday's XP still counts; the streak is not reported as broken
    before the learner has had a chance to study today."""
    now = datetime.now(timezone.utc)
    for offset in (1, 2):
        db_session.add(XpEvent(
            user_id=learner.id, amount=10, activity="lesson_complete",
            earned_date=(now - timedelta(days=offset)).date(),
        ))
    db_session.commit()

    assert streak_days(db_session, learner) == 2


def test_revision_reports_zero_days_for_a_lesson_completed_today(
    client, db_session, learner, creator_course
):
    db_session.add(LessonProgress(
        user_id=learner.id, lesson_id=creator_course,
        completed_at=datetime.now(timezone.utc), attempts=1,
    ))
    db_session.commit()

    profile = revision_profile(db_session, learner)
    flexbox = next(r for r in profile["topic_records"] if r["topic"] == "Flexbox")
    assert flexbox["days_since_studied"] == 0, (
        "a lesson completed today must read as 0 days since, not 1"
    )


def test_creator_overview_buckets_todays_enrolment_in_today(
    client, db_session, onboard
):
    """The daily chart must not drop today's rows into yesterday's bucket."""
    client.post("/v1/auth/register", json={
        "email": "tz2-creator@example.com", "password": "password123",
        "learner_name": "Tz2Creator", "is_creator": True,
    })
    tok = client.post("/v1/auth/login", json={
        "email": "tz2-creator@example.com", "password": "password123",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}

    cid = client.post("/v1/courses", json={
        "title": "Tz2", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=headers).json()["id"]
    db_session.get(__import__("app.models", fromlist=["Course"]).Course,
                   cid).status = "published"
    db_session.commit()

    client.post("/v1/auth/register", json={
        "email": "tz2-learner@example.com", "password": "password123",
        "learner_name": "Tz2Learner",
    })
    ltok = client.post("/v1/auth/login", json={
        "email": "tz2-learner@example.com", "password": "password123",
    }).json()["access_token"]
    # Enrolment is gated on onboarding; this learner registered inline, so finish
    # setup before acting.
    onboard("tz2-learner@example.com")
    client.post(f"/v1/me/enroll/{cid}", headers={"Authorization": f"Bearer {ltok}"})

    body = client.get("/v1/me/creator/analytics/overview?days=7",
                      headers=headers).json()
    today = body["daily"][-1]
    assert today["enrollments"] == 1
    assert today["date"] == datetime.now(timezone.utc).date().isoformat()
