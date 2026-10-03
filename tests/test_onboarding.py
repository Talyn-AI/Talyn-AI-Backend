"""Email verification, learning pace, and interests.

Deliberately does not use the conftest `onboard` helper: this file drives the
real endpoints, because the whole point of them is that a user who has not
finished setup is refused. Tests that fake the state would prove nothing.

The order enforced is verify -> pace -> interests. Each test asserts one link
in that chain, and the gate tests assert from the wrong side too: that a user
who HAS onboarded is not blocked.
"""
import re

import pytest

LEARNER = {
    "email": "onboard@example.com", "password": "password123",
    "learner_name": "Onboarder",
}


@pytest.fixture
def learner(client, smtp):
    """A registered, signed-in learner with the verification email captured.

    `is_creator` so the gate tests can mint a course to try enrolling in
    without pulling in the conftest creator fixture, which onboards its user
    and would defeat the point.
    """
    client.post("/v1/auth/register", json={**LEARNER, "is_creator": True})
    token = client.post("/v1/auth/login", json={
        "email": LEARNER["email"], "password": LEARNER["password"],
    }).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _token_from_email(smtp) -> str:
    body = ""
    for part in smtp.sent[-1].walk():
        if part.get_content_type() == "text/plain":
            body = part.get_payload(decode=True).decode("utf-8")
    match = re.search(r"verify-email\?token=([^\s\"<]+)", body)
    assert match, f"no verification link found in {body[:200]!r}"
    return match.group(1)


def _verify(client, smtp):
    """Click the link, as the user would."""
    token = _token_from_email(smtp)
    r = client.post("/v1/auth/email-verification/confirm", json={"token": token})
    assert r.status_code == 200, r.text
    return r


# ── Verification ─────────────────────────────────────────────────────────────


def test_a_new_account_is_unverified(client, learner):
    status = client.get("/v1/onboarding/status", headers=learner).json()
    assert status["email_verified"] is False
    assert status["next_step"] == "verify_email"


def test_confirming_marks_the_address_verified(client, learner, smtp):
    _verify(client, smtp)
    status = client.get("/v1/onboarding/status", headers=learner).json()
    assert status["email_verified"] is True
    assert status["next_step"] == "choose_pace"


def test_a_verification_link_cannot_be_replayed(client, learner, smtp):
    """Single use, like a password reset: an attacker who later finds the link
    in the mailbox must not be able to undo a confirmation."""
    token = _token_from_email(smtp)
    assert client.post("/v1/auth/email-verification/confirm",
                       json={"token": token}).status_code == 200
    assert client.post("/v1/auth/email-verification/confirm",
                       json={"token": token}).status_code == 401


def test_a_forged_token_is_refused(client, learner, smtp):
    assert client.post("/v1/auth/email-verification/confirm",
                       json={"token": "not-a-real-token-at-all"}).status_code == 401


def test_requesting_a_new_link_invalidates_the_old_one(client, learner, smtp):
    """Two live links would mean the one that leaked still works after the
    newer one has been used."""
    first = _token_from_email(smtp)
    client.post("/v1/auth/email-verification/request", json={"email": LEARNER["email"]})
    second = _token_from_email(smtp)
    assert first != second

    assert client.post("/v1/auth/email-verification/confirm",
                       json={"token": second}).status_code == 200
    assert client.post("/v1/auth/email-verification/confirm",
                       json={"token": first}).status_code == 401


def test_request_does_not_disclose_whether_an_account_exists(client, learner):
    """Same response and shape for a known and an unknown address, so this
    cannot be used to enumerate who has an account."""
    known = client.post("/v1/auth/email-verification/request",
                        json={"email": LEARNER["email"]})
    unknown = client.post("/v1/auth/email-verification/request",
                          json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_resending_to_a_verified_address_is_a_no_op(client, learner, smtp):
    _verify(client, smtp)
    before = len(smtp.sent)
    r = client.post("/v1/auth/email-verification/request",
                    json={"email": LEARNER["email"]})
    assert r.status_code == 200
    assert len(smtp.sent) == before


def test_anonymous_cannot_ask_about_onboarding(client):
    assert client.get("/v1/onboarding/status").status_code == 401


# ── Options ──────────────────────────────────────────────────────────────────


def test_options_are_public_and_carry_the_expected_paces(client):
    """The pickers read this instead of hardcoding, so a client cannot offer a
    value the API rejects."""
    r = client.get("/v1/onboarding/options")
    assert r.status_code == 200
    body = r.json()
    assert [p["key"] for p in body["paces"]] == [
        "light", "steady", "deep", "intensive"
    ]
    light = body["paces"][0]
    assert light["min_minutes"] == 15
    assert light["max_minutes"] == 30
    # The open-ended top range must be null, not an invented ceiling.
    assert body["paces"][-1]["max_minutes"] is None
    assert len(body["interests"]) >= 10
    assert {"key", "label"} <= set(body["interests"][0])


# ── Completing onboarding ────────────────────────────────────────────────────


def test_onboarding_needs_a_verified_address(client, learner, smtp):
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "steady", "interests": ["design"]},
                    headers=learner)
    assert r.status_code == 409
    assert "confirm your email" in r.json()["detail"].lower()


def test_completing_records_pace_and_interests(client, learner, smtp):
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "deep", "interests": ["design", "devops"]},
                    headers=learner)
    assert r.status_code == 200
    body = r.json()
    assert body["learning_pace"] == "deep"
    assert body["interests"] == ["design", "devops"]
    assert body["onboarding_completed"] is True
    assert body["next_step"] == "done"


def test_pace_seeds_the_study_plan(client, learner, smtp):
    """Otherwise the account arrives with the same 30 minutes everyone gets and
    the choice does nothing."""
    _verify(client, smtp)
    client.post("/v1/onboarding/complete",
                json={"learning_pace": "intensive", "interests": ["design"]},
                headers=learner)

    plan = client.get("/v1/me/study-plan", headers=learner).json()
    assert plan["daily_goal_minutes"] == 150
    assert plan["weekly_target_lessons"] == 7


def test_changing_pace_later_updates_the_plan(client, learner, smtp):
    """A setting you have to open a support ticket to change is a support
    ticket."""
    _verify(client, smtp)
    client.post("/v1/onboarding/complete",
                json={"learning_pace": "deep", "interests": ["design"]},
                headers=learner)
    client.post("/v1/onboarding/complete",
                json={"learning_pace": "light", "interests": ["design"]},
                headers=learner)

    plan = client.get("/v1/me/study-plan", headers=learner).json()
    assert plan["daily_goal_minutes"] == 20


def test_an_unknown_pace_is_rejected(client, learner, smtp):
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "as-fast-as-possible",
                          "interests": ["design"]},
                    headers=learner)
    assert r.status_code == 422
    assert "learning_pace" in r.text


def test_unknown_interests_are_rejected(client, learner, smtp):
    """Interests drive recommendations, so free text here would need cleaning
    later with no way to tell a typo from a real interest."""
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "steady", "interests": ["underwater basketry"]},
                    headers=learner)
    assert r.status_code == 422
    assert "underwater basketry" in r.text


def test_interests_are_required(client, learner, smtp):
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "steady", "interests": []},
                    headers=learner)
    assert r.status_code == 422


def test_duplicate_interests_are_collapsed(client, learner, smtp):
    """A picker can hand the same key back twice; storing it twice would skew
    recommendations."""
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": "steady",
                          "interests": ["design", "design", "devops"]},
                    headers=learner)
    assert r.json()["interests"] == ["design", "devops"]


# ── The gate ─────────────────────────────────────────────────────────────────


def _onboarded(client, learner, smtp, pace="steady"):
    _verify(client, smtp)
    r = client.post("/v1/onboarding/complete",
                    json={"learning_pace": pace, "interests": ["design"]},
                    headers=learner)
    assert r.status_code == 200
    return learner


def test_enrolling_is_blocked_before_onboarding(client, learner):
    course = client.post("/v1/courses", json={
        "title": "Gated", "description": "d", "category": "C",
        "difficulty_level": "beginner", "course_type": "free",
    }, headers=learner).json()
    r = client.post(f"/v1/me/enroll/{course['id']}", headers=learner)
    assert r.status_code == 409
    detail = r.json()["detail"].lower()
    assert "pace" in detail and "interests" in detail
    # The message must also say the email is unconfirmed, or the user fixes
    # one thing and hits the same wall again.
    assert "email" in detail


def test_enrolling_works_once_onboarded(client, learner, smtp):
    course = client.post("/v1/courses", json={
        "title": "Gated", "description": "d", "category": "C",
        "difficulty_level": "beginner", "course_type": "free",
    }, headers=learner).json()
    _onboarded(client, learner, smtp)
    r = client.post(f"/v1/me/enroll/{course['id']}", headers=learner)
    assert r.status_code == 201


def test_reads_stay_open_while_blocked(client, learner):
    """A blocked user still needs to see their own state, or the app is a wall
    with no way out."""
    assert client.get("/v1/onboarding/status", headers=learner).status_code == 200
    assert client.get("/v1/me/xp", headers=learner).status_code == 200
    assert client.get("/v1/me/context", headers=learner).status_code == 200
    assert client.get("/v1/missions").status_code == 200


def test_adopting_a_mission_is_blocked_before_onboarding(client, learner):
    client.post("/v1/auth/register", json={
        "email": "mission-author@example.com", "password": "password123",
        "learner_name": "Author", "is_creator": True,
    })
    author = {"Authorization": "Bearer " + client.post("/v1/auth/login", json={
        "email": "mission-author@example.com", "password": "password123",
    }).json()["access_token"]}
    tid = client.post("/v1/creator/missions", json={
        "title": "Do a thing", "steps": [{"title": "Step", "order": 1}],
    }, headers=author).json()["id"]
    client.patch(f"/v1/creator/missions/{tid}", json={"published": True},
                 headers=author)

    r = client.post("/v1/me/missions", json={"template_id": tid}, headers=learner)
    assert r.status_code == 409


def test_quiz_results_are_blocked_before_onboarding(client, learner):
    r = client.post("/v1/me/quiz-results",
                    json={"topic": "Anything", "score_percent": 50.0},
                    headers=learner)
    assert r.status_code == 409