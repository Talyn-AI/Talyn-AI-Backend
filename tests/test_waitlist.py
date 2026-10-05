"""Waitlist signups from the early-access page.

Public and unauthenticated by design, so these tests lean on the validation
that protects it: a controlled interest vocabulary, a two-way role, and a
submit-twice path that must not create a second row.
"""


def join(client, **overrides):
    body = {
        "email": "ada@example.com",
        "name": "Ada Lovelace",
        "role": "learner",
        "interests": ["design", "web-development"],
        "course": "",
    }
    body.update(overrides)
    return client.post("/v1/waitlist", json=body)


# ── Joining ──────────────────────────────────────────────────────────────────


def test_joining_needs_no_auth(client):
    r = join(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "ada@example.com"
    assert body["created"] is True
    assert body["position"] == 1
    assert "list" in body["message"].lower()


def test_position_reflects_the_order_people_joined(client):
    join(client, email="first@example.com")
    r = join(client, email="second@example.com")
    assert r.status_code == 201
    assert r.json()["position"] == 2


def test_a_creator_can_join_without_interests(client):
    r = join(client, role="creator", interests=[], name="Grace Hopper")
    assert r.status_code == 201, r.text
    assert r.json()["created"] is True


def test_the_named_course_is_stored(client, db_session):
    from app.models import WaitlistSignup

    join(client, role="creator", course="  Rust for embedded systems  ")
    row = db_session.query(WaitlistSignup).one()
    assert row.course == "Rust for embedded systems"


def test_whitespace_around_the_name_is_trimmed(client, db_session):
    from app.models import WaitlistSignup

    join(client, name="  Ada Lovelace  ")
    assert db_session.query(WaitlistSignup).one().name == "Ada Lovelace"


# ── Repeat submissions ───────────────────────────────────────────────────────


def test_resubmitting_updates_rather_than_duplicates(client, db_session):
    from app.models import WaitlistSignup

    assert join(client, course="Python").status_code == 201
    r = join(client, course="Rust")
    assert r.status_code == 200, r.text
    assert r.json()["created"] is False

    assert db_session.query(WaitlistSignup).count() == 1
    assert db_session.query(WaitlistSignup).one().course == "Rust"


def test_a_repeat_keeps_the_position_it_joined_at(client):
    join(client, email="first@example.com")
    second = join(client, email="second@example.com")
    assert second.json()["position"] == 2
    again = join(client, email="second@example.com", course="Something else")
    assert again.status_code == 200
    assert again.json()["position"] == 2


def test_the_same_address_in_a_different_case_is_the_same_person(client):
    join(client, email="ada@example.com")
    r = join(client, email="ADA@Example.com")
    assert r.status_code == 200
    assert r.json()["created"] is False


# ── Validation ───────────────────────────────────────────────────────────────


def test_a_malformed_address_is_rejected(client):
    assert join(client, email="not-an-email").status_code == 422


def test_an_unknown_role_is_rejected(client):
    assert join(client, role="admin").status_code == 422


def test_a_missing_role_is_rejected(client):
    assert join(client, role=None).status_code == 422


def test_an_unknown_interest_is_rejected(client):
    r = join(client, interests=["design", "underwater-basket-weaving"])
    assert r.status_code == 422
    assert "underwater-basket-weaving" in r.text


def test_more_than_two_interests_is_rejected(client):
    r = join(client, interests=["design", "writing", "gaming"])
    assert r.status_code == 422


def test_duplicate_interests_are_collapsed(client, db_session):
    from app.models import WaitlistSignup

    r = join(client, interests=["design", "design", "writing"])
    assert r.status_code == 201, r.text
    assert db_session.query(WaitlistSignup).one().interests == ["design", "writing"]


def test_a_blank_name_is_rejected(client):
    assert join(client, name="").status_code == 422


def test_an_over_long_course_name_is_rejected(client):
    assert join(client, course="x" * 201).status_code == 422


def test_no_email_is_sent_on_joining(client, smtp):
    """Confirmation mail is deliberately skipped — see the router docstring."""
    assert join(client).status_code == 201
    assert smtp.sent == []
