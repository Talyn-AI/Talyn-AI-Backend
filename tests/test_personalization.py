"""Learning preferences: interests, time commitment, pace.

PUT /v1/users/me/personalization writes the same vocabulary onboarding
collects, so a learner who outgrows their first answers is not stuck with
them. Everything optional; only present fields change.
"""
from app.models import StudyPlan


def _plan(db_session, email):
    from app.models import User

    user = db_session.query(User).filter(User.email == email).one()
    return db_session.query(StudyPlan).filter(
        StudyPlan.user_id == user.id).one_or_none()


# ── Interests ────────────────────────────────────────────────────────────────


def test_update_interests(client, learner_headers, db_session):
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"interests": ["writing", "design"]})
    assert r.status_code == 200, r.text
    assert r.json()["interests"] == ["writing", "design"]

    from app.models import User
    user = db_session.query(User).filter(
        User.email == "upload-learner@example.com").one()
    assert user.interests == ["writing", "design"]


def test_unknown_interests_are_rejected(client, learner_headers):
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"interests": ["design", "underwater-basket-weaving"]})
    assert r.status_code == 422


def test_duplicate_interests_collapse(client, learner_headers):
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"interests": ["design", "design"]})
    assert r.status_code == 200
    assert r.json()["interests"] == ["design"]


def test_absent_fields_do_not_change(client, learner_headers, db_session):
    from app.models import User

    before = db_session.query(User).filter(
        User.email == "upload-learner@example.com").one().interests
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"daily_goal_minutes": 60})
    assert r.status_code == 200
    assert r.json()["daily_goal_minutes"] == 60
    after = db_session.query(User).filter(
        User.email == "upload-learner@example.com").one().interests
    assert after == before


# ── Time commitment and pace ─────────────────────────────────────────────────


def test_daily_goal_creates_a_plan_when_missing(client, learner_headers,
                                                db_session):
    from app.models import User

    user = db_session.query(User).filter(
        User.email == "upload-learner@example.com").one()
    assert _plan(db_session, "upload-learner@example.com") is None

    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"daily_goal_minutes": 90})
    assert r.status_code == 200
    assert r.json()["daily_goal_minutes"] == 90
    assert _plan(db_session, "upload-learner@example.com") is not None
    assert user.learning_pace == "steady"  # the onboard default, untouched


def test_pace_reseeds_the_plan_like_onboarding(client, learner_headers):
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"learning_pace": "deep"})
    assert r.status_code == 200, r.text
    assert r.json()["learning_pace"] == "deep"
    assert r.json()["daily_goal_minutes"] == 90
    assert r.json()["weekly_target_lessons"] == 6


def test_explicit_daily_goal_wins_over_pace_reseed(client, learner_headers):
    r = client.put("/v1/users/me/personalization", headers=learner_headers,
                   json={"learning_pace": "light", "daily_goal_minutes": 25})
    assert r.status_code == 200
    assert r.json()["learning_pace"] == "light"
    assert r.json()["daily_goal_minutes"] == 25
    assert r.json()["weekly_target_lessons"] == 3


def test_unknown_pace_is_rejected(client, learner_headers):
    assert client.put(
        "/v1/users/me/personalization", headers=learner_headers,
        json={"learning_pace": "marathon"}).status_code == 422


def test_out_of_range_goal_is_rejected(client, learner_headers):
    assert client.put(
        "/v1/users/me/personalization", headers=learner_headers,
        json={"daily_goal_minutes": 4}).status_code == 422
    assert client.put(
        "/v1/users/me/personalization", headers=learner_headers,
        json={"daily_goal_minutes": 481}).status_code == 422


def test_personalization_needs_auth(client):
    assert client.put("/v1/users/me/personalization",
                      json={"interests": ["design"]}).status_code == 401
