"""Tests for mission lifecycle and buddy-match persistence endpoints."""
import pytest


def _register(client, email, name, is_creator=False):
    client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "secret12345",
            "learner_name": name,
            "difficulty_level": "beginner",
            "goals": "Learn fast",
            "interests": ["design"],
            "is_creator": is_creator,
        },
    )
    r = client.post(
        "/v1/auth/login", json={"email": email, "password": "secret12345"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def auth_headers(client, onboard):
    headers = _register(client, "funke@example.com", "Funke")
    onboard(headers)
    return headers

@pytest.fixture()
def buddy_headers(client):
    headers = _register(client, "tunde@example.com", "Tunde")
    r = client.get("/v1/users/me", headers=headers)
    return headers, r.json()["id"]


MISSION = {
    "title": "Build your first button",
    "description": "Design and code a button component",
    "purpose": "Ship something real today",
    "reward_xp": 120,
    "badge": "Button Builder",
    "steps": [
        {"order": 1, "title": "Sketch the button", "description": "Pen and paper"},
        {"order": 2, "title": "Code it in HTML/CSS", "description": "Make it clickable"},
    ],
}


@pytest.fixture()
def mission_template_id(client):
    """Missions are creator-authored now, so a catalogue entry has to exist
    before a learner can adopt one. See tests/test_missions.py for the
    catalogue's own rules."""
    creator = _register(client, "missions-author@example.com", "Author",
                        is_creator=True)
    created = client.post("/v1/creator/missions", json=MISSION, headers=creator)
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    client.patch(f"/v1/creator/missions/{tid}", json={"published": True},
                 headers=creator)
    return tid


@pytest.fixture()
def mission_id(client, auth_headers, mission_template_id):
    r = client.post("/v1/me/missions", json={"template_id": mission_template_id},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Mission lifecycle ─────────────────────────────────────────────────────────

def test_accept_mission(client, auth_headers, mission_id):
    r = client.get(f"/v1/me/missions/{mission_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "in_progress"
    assert len(body["steps"]) == 2
    assert body["reward_xp"] == 120


def test_one_active_mission_at_a_time(client, auth_headers, mission_id):
    """A second *catalogue* mission is refused while one is in progress."""
    creator = _register(client, "missions-author-2@example.com", "Author2",
                        is_creator=True)
    other = client.post("/v1/creator/missions",
                        json={**MISSION, "title": "A different quest"},
                        headers=creator).json()
    client.patch(f"/v1/creator/missions/{other['id']}",
                 json={"published": True}, headers=creator)
    r = client.post("/v1/me/missions", json={"template_id": other["id"]},
                    headers=auth_headers)
    assert r.status_code == 409


def test_list_and_filter_missions(client, auth_headers, mission_id):
    assert len(client.get("/v1/me/missions", headers=auth_headers).json()) == 1
    r = client.get("/v1/me/missions?status=completed", headers=auth_headers)
    assert r.json() == []
    r = client.get("/v1/me/missions?status=bogus", headers=auth_headers)
    assert r.status_code == 422


def test_mission_is_private(client, auth_headers, buddy_headers, mission_id):
    headers, _ = buddy_headers
    assert client.get(f"/v1/me/missions/{mission_id}", headers=headers).status_code == 404


def test_complete_steps_finishes_mission(client, auth_headers, mission_id):
    steps = client.get(f"/v1/me/missions/{mission_id}", headers=auth_headers).json()["steps"]

    r1 = client.post(
        f"/v1/me/missions/{mission_id}/steps/{steps[0]['id']}/complete",
        headers=auth_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["steps_remaining"] == 1

    r2 = client.post(
        f"/v1/me/missions/{mission_id}/steps/{steps[1]['id']}/complete",
        headers=auth_headers,
    )
    body = r2.json()
    assert body["step_completed"] is True
    assert body["completed"] is True
    assert body["xp_awarded"] == 120  # the mission's own reward
    assert body["badge_awarded"] == "Button Builder"

    # Mission + XP + badge all persisted
    assert client.get(f"/v1/me/missions/{mission_id}", headers=auth_headers).json()["status"] == "completed"
    xp = client.get("/v1/me/xp", headers=auth_headers).json()
    assert xp["xp_total"] == 120
    assert xp["breakdown"][0]["activity"] == "mission"
    ctx = client.get("/v1/me/context", headers=auth_headers).json()
    assert ctx["missions"]["completed_mission_count"] == 1
    assert ctx["missions"]["active_mission"] is None
    assert ctx["badges"][0]["name"] == "Button Builder"

    # Step on a finished mission is rejected
    r3 = client.post(
        f"/v1/me/missions/{mission_id}/steps/{steps[0]['id']}/complete",
        headers=auth_headers,
    )
    assert r3.status_code == 409


def test_complete_step_unknown_step(client, auth_headers, mission_id):
    r = client.post(
        f"/v1/me/missions/{mission_id}/steps/99999/complete", headers=auth_headers
    )
    assert r.status_code == 404


def test_manual_complete_is_idempotent(client, auth_headers, mission_id):
    r1 = client.patch(
        f"/v1/me/missions/{mission_id}", json={"status": "completed"}, headers=auth_headers
    )
    assert r1.json()["xp_awarded"] == 120
    r2 = client.patch(
        f"/v1/me/missions/{mission_id}", json={"status": "completed"}, headers=auth_headers
    )
    assert r2.json()["already_completed"] is True
    assert r2.json()["xp_awarded"] == 0
    # no double XP, no duplicate badge
    assert client.get("/v1/me/xp", headers=auth_headers).json()["xp_total"] == 120
    assert len(client.get("/v1/me/context", headers=auth_headers).json()["badges"]) == 1


def test_patch_invalid_status(client, auth_headers, mission_id):
    r = client.patch(
        f"/v1/me/missions/{mission_id}", json={"status": "flying"}, headers=auth_headers
    )
    assert r.status_code == 422


def test_context_shows_active_mission(client, auth_headers, mission_id):
    ctx = client.get("/v1/me/context", headers=auth_headers).json()
    assert ctx["missions"]["active_mission"]["title"] == "Build your first button"
    assert len(ctx["missions"]["active_mission"]["steps"]) == 2


def test_me_endpoints_require_auth(client, mission_id, mission_template_id):
    assert client.get("/v1/me/missions").status_code == 401
    r = client.post("/v1/me/missions", json={"template_id": mission_template_id})
    assert r.status_code == 401


# ── Buddy matches ─────────────────────────────────────────────────────────────

def test_save_and_list_match(client, auth_headers, buddy_headers):
    _, buddy_id = buddy_headers
    r = client.post(
        "/v1/me/buddies/matches",
        json={"buddy_user_id": buddy_id, "match_score": 87},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["status"] == "pending"
    assert r.json()["match_score"] == 87

    matches = client.get("/v1/me/buddies/matches", headers=auth_headers).json()
    assert len(matches) == 1
    assert matches[0]["buddy_user_id"] == buddy_id


def test_duplicate_pending_match_rejected(client, auth_headers, buddy_headers):
    _, buddy_id = buddy_headers
    payload = {"buddy_user_id": buddy_id, "match_score": 80}
    assert client.post("/v1/me/buddies/matches", json=payload, headers=auth_headers).status_code == 201
    r = client.post("/v1/me/buddies/matches", json=payload, headers=auth_headers)
    assert r.status_code == 409


def test_cannot_match_self(client, auth_headers):
    me = client.get("/v1/users/me", headers=auth_headers).json()
    r = client.post(
        "/v1/me/buddies/matches",
        json={"buddy_user_id": me["id"], "match_score": 100},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_match_unknown_user(client, auth_headers):
    r = client.post(
        "/v1/me/buddies/matches",
        json={"buddy_user_id": 99999, "match_score": 50},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_accept_match(client, auth_headers, buddy_headers):
    _, buddy_id = buddy_headers
    mid = client.post(
        "/v1/me/buddies/matches",
        json={"buddy_user_id": buddy_id, "match_score": 87},
        headers=auth_headers,
    ).json()["id"]

    r = client.patch(
        f"/v1/me/buddies/matches/{mid}", json={"status": "accepted"}, headers=auth_headers
    )
    assert r.json()["status"] == "accepted"

    r = client.patch(
        f"/v1/me/buddies/matches/{mid}", json={"status": "bogus"}, headers=auth_headers
    )
    assert r.status_code == 422


def test_match_is_private(client, auth_headers, buddy_headers):
    buddy_auth, buddy_id = buddy_headers
    mid = client.post(
        "/v1/me/buddies/matches",
        json={"buddy_user_id": buddy_id, "match_score": 87},
        headers=auth_headers,
    ).json()["id"]
    r = client.patch(
        f"/v1/me/buddies/matches/{mid}", json={"status": "accepted"}, headers=buddy_auth
    )
    assert r.status_code == 404
