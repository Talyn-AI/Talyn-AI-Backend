"""Missions: creators write them, learners choose from them.

Two rules are load-bearing and both are covered from the wrong side:

  1. A learner cannot author a mission. The only way to start one is by
     adopting a published creator template.
  2. Adoption copies rather than links. If it linked, the first learner to
     tick a step would tick it for everyone - which is why step rows are
     duplicated into per-learner ones.
"""
import pytest

TEMPLATE_BODY = {
    "title": "Build your first button",
    "description": "Ship a real button",
    "purpose": "The quick win",
    "reward_xp": 150,
    "badge": "Button Builder",
    "steps": [
        {"title": "Write the HTML", "description": "", "order": 1},
        {"title": "Style the hover", "description": "", "order": 2},
    ],
}


def _register(client, email, name="Someone", is_creator=False) -> dict:
    r = client.post("/v1/auth/register", json={
        "email": email, "password": "password123",
        "learner_name": name, "is_creator": is_creator,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def creator_headers(client, onboard):
    _register(client, "mission-creator@example.com", "Creator", is_creator=True)
    r = client.post("/v1/auth/login", json={
        "email": "mission-creator@example.com", "password": "password123",
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

@pytest.fixture
def other_creator_headers(client):
    _register(client, "creator-two@example.com", "Second", is_creator=True)
    r = client.post("/v1/auth/login", json={
        "email": "creator-two@example.com", "password": "password123",
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def learner_headers(client, onboard):
    _register(client, "mission-learner@example.com", "Ade")
    r = client.post("/v1/auth/login", json={
        "email": "mission-learner@example.com", "password": "password123",
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    onboard(headers)
    return headers

def _published_template(client, creator_headers) -> dict:
    r = client.post("/v1/creator/missions", json=TEMPLATE_BODY,
                    headers=creator_headers)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r = client.patch(f"/v1/creator/missions/{tid}", json={"published": True},
                     headers=creator_headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── The rule: learners do not create ─────────────────────────────────────────


def test_catalogue_is_empty_until_a_creator_publishes(client, learner_headers):
    r = client.get("/v1/missions", headers=learner_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_unpublished_mission_is_not_offered(client, creator_headers,
                                            learner_headers):
    """A half-written mission must not appear in the catalogue."""
    assert client.post("/v1/creator/missions", json=TEMPLATE_BODY,
                       headers=creator_headers).status_code == 201
    assert client.get("/v1/missions", headers=learner_headers).json() == []


def test_publishing_offers_it_to_learners(client, creator_headers,
                                          learner_headers):
    template = _published_template(client, creator_headers)
    body = client.get("/v1/missions", headers=learner_headers).json()
    assert len(body) == 1
    entry = body[0]
    assert entry["id"] == template["id"]
    assert entry["title"] == "Build your first button"
    assert entry["reward_xp"] == 150
    assert entry["badge"] == "Button Builder"
    assert len(entry["steps"]) == 2
    assert entry["adopted"] is False
    assert entry["creator_name"] == "Creator"


def test_catalogue_is_public(client, creator_headers):
    """Browsing should not require an account - it is how a visitor decides."""
    _published_template(client, creator_headers)
    assert client.get("/v1/missions").status_code == 200


def test_learner_cannot_author_a_mission(client, learner_headers):
    """The old free-form body is gone; 422 because the schema rejects it."""
    r = client.post("/v1/me/missions", json={
        "title": "My own quest", "purpose": "nope", "steps": [],
    }, headers=learner_headers)
    assert r.status_code == 422
    assert "template_id" in r.text


def test_plain_learner_cannot_write_missions(client, learner_headers):
    r = client.post("/v1/creator/missions", json=TEMPLATE_BODY,
                    headers=learner_headers)
    assert r.status_code in (401, 403)


def test_anonymous_caller_cannot_write_missions(client):
    assert client.post("/v1/creator/missions", json=TEMPLATE_BODY).status_code \
        in (401, 403)


# ── The rule: adoption copies, it does not share ─────────────────────────────


def test_adopting_gives_the_learner_a_mission_with_its_own_steps(
    client, creator_headers, learner_headers
):
    template = _published_template(client, creator_headers)
    r = client.post("/v1/me/missions", json={"template_id": template["id"]},
                    headers=learner_headers)
    assert r.status_code == 201, r.text
    mission = r.json()
    assert mission["template_id"] == template["id"]
    assert mission["title"] == template["title"]
    assert mission["reward_xp"] == 150
    assert mission["status"] == "in_progress"
    assert len(mission["steps"]) == 2
    assert all(not s["completed"] for s in mission["steps"])


def test_progress_is_not_shared_between_learners(client, creator_headers,
                                                  learner_headers, onboard):
    """The reason adoption copies: linking would let one learner complete a
    step for everybody."""
    _register(client, "second-learner@example.com", "Bola")
    login = client.post("/v1/auth/login", json={
        "email": "second-learner@example.com", "password": "password123",
    }).json()
    hb = {"Authorization": f"Bearer {login['access_token']}"}
    # Adopting a mission is gated on onboarding.
    onboard(hb)

    template = _published_template(client, creator_headers)
    m_a = client.post("/v1/me/missions", json={"template_id": template["id"]},
                      headers=learner_headers).json()
    m_b = client.post("/v1/me/missions", json={"template_id": template["id"]},
                      headers=hb).json()

    assert m_a["steps"][0]["id"] != m_b["steps"][0]["id"], \
        "step rows must not be shared between learners"

    r = client.post(
        f"/v1/me/missions/{m_a['id']}/steps/{m_a['steps'][0]['id']}/complete",
        headers=learner_headers,
    )
    assert r.status_code == 200

    b = client.get(f"/v1/me/missions/{m_b['id']}", headers=hb).json()
    assert all(not s["completed"] for s in b["steps"])


def test_editing_the_template_does_not_rewrite_an_adopted_mission(
    client, creator_headers, learner_headers
):
    """Someone halfway through a mission should not find it renamed tomorrow."""
    template = _published_template(client, creator_headers)
    mission = client.post("/v1/me/missions", json={"template_id": template["id"]},
                          headers=learner_headers).json()

    client.patch(f"/v1/creator/missions/{template['id']}",
                 json={"title": "Totally different title"},
                 headers=creator_headers)

    r = client.get(f"/v1/me/missions/{mission['id']}", headers=learner_headers)
    assert r.json()["title"] == "Build your first button"


def test_catalogue_flags_a_mission_already_taken(client, creator_headers,
                                                 learner_headers):
    template = _published_template(client, creator_headers)
    client.post("/v1/me/missions", json={"template_id": template["id"]},
                headers=learner_headers)

    entry = client.get("/v1/missions", headers=learner_headers).json()[0]
    assert entry["adopted"] is True
    assert isinstance(entry["adopted_mission_id"], int)


def test_cannot_adopt_the_same_mission_twice(client, creator_headers,
                                             learner_headers):
    template = _published_template(client, creator_headers)
    assert client.post("/v1/me/missions", json={"template_id": template["id"]},
                       headers=learner_headers).status_code == 201
    r = client.post("/v1/me/missions", json={"template_id": template["id"]},
                    headers=learner_headers)
    assert r.status_code == 409


def test_cannot_adopt_an_unpublished_or_missing_mission(client, creator_headers,
                                                        learner_headers):
    draft = client.post("/v1/creator/missions", json=TEMPLATE_BODY,
                        headers=creator_headers).json()
    assert client.post("/v1/me/missions", json={"template_id": draft["id"]},
                       headers=learner_headers).status_code == 404
    assert client.post("/v1/me/missions", json={"template_id": 99999},
                       headers=learner_headers).status_code == 404


def test_one_active_mission_at_a_time(client, creator_headers, learner_headers):
    first = _published_template(client, creator_headers)
    client.post("/v1/me/missions", json={"template_id": first["id"]},
                headers=learner_headers)

    second = client.post("/v1/creator/missions",
                         json={**TEMPLATE_BODY, "title": "Another quest"},
                         headers=creator_headers).json()
    client.patch(f"/v1/creator/missions/{second['id']}",
                 json={"published": True}, headers=creator_headers)

    r = client.post("/v1/me/missions", json={"template_id": second["id"]},
                    headers=learner_headers)
    assert r.status_code == 409


# ── Completion still awards once ────────────────────────────────────────────


def test_finishing_every_step_awards_xp_and_badge(client, creator_headers,
                                                   learner_headers):
    template = _published_template(client, creator_headers)
    mission = client.post("/v1/me/missions", json={"template_id": template["id"]},
                          headers=learner_headers).json()

    last = None
    for step in mission["steps"]:
        last = client.post(
            f"/v1/me/missions/{mission['id']}/steps/{step['id']}/complete",
            headers=learner_headers,
        )
        assert last.status_code == 200

    body = last.json()
    assert body["completed"] is True
    assert body["xp_awarded"] == 150
    assert body["badge_awarded"] == "Button Builder"
    assert client.get(f"/v1/me/missions/{mission['id']}",
                      headers=learner_headers).json()["status"] == "completed"


def test_recompleting_does_not_award_twice(client, creator_headers,
                                           learner_headers):
    template = _published_template(client, creator_headers)
    mission = client.post("/v1/me/missions", json={"template_id": template["id"]},
                          headers=learner_headers).json()
    for step in mission["steps"]:
        client.post(
            f"/v1/me/missions/{mission['id']}/steps/{step['id']}/complete",
            headers=learner_headers,
        )
    r = client.patch(f"/v1/me/missions/{mission['id']}",
                     json={"status": "completed"}, headers=learner_headers)
    assert r.json()["already_completed"] is True
    assert r.json()["xp_awarded"] == 0


# ── Creator ownership ────────────────────────────────────────────────────────


def test_another_creator_cannot_see_or_edit_a_template(client, creator_headers,
                                                       other_creator_headers):
    """404 rather than 403: this endpoint must not confirm that someone else's
    id exists."""
    template = _published_template(client, creator_headers)
    assert client.patch(f"/v1/creator/missions/{template['id']}",
                        json={"title": "hijacked"},
                        headers=other_creator_headers).status_code == 404
    assert client.delete(f"/v1/creator/missions/{template['id']}",
                         headers=other_creator_headers).status_code == 404
    assert client.get(f"/v1/creator/missions/{template['id']}",
                      headers=other_creator_headers).status_code == 404


def test_creator_list_shows_only_their_own(client, creator_headers,
                                           other_creator_headers):
    _published_template(client, creator_headers)
    mine = client.get("/v1/creator/missions", headers=creator_headers).json()
    theirs = client.get("/v1/creator/missions",
                        headers=other_creator_headers).json()
    assert len(mine) == 1
    assert theirs == []


def test_unpublishing_withdraws_it_from_the_catalogue(client, creator_headers,
                                                      learner_headers):
    template = _published_template(client, creator_headers)
    client.patch(f"/v1/creator/missions/{template['id']}",
                 json={"published": False}, headers=creator_headers)
    assert client.get("/v1/missions", headers=learner_headers).json() == []


def test_deleting_a_template_leaves_adopted_missions_alone(
    client, creator_headers, learner_headers
):
    template = _published_template(client, creator_headers)
    mission = client.post("/v1/me/missions", json={"template_id": template["id"]},
                          headers=learner_headers).json()

    assert client.delete(f"/v1/creator/missions/{template['id']}",
                         headers=creator_headers).status_code == 200

    r = client.get(f"/v1/me/missions/{mission['id']}", headers=learner_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "Build your first button"


def test_adoption_counts_are_reported_to_the_creator(client, creator_headers,
                                                     learner_headers):
    template = _published_template(client, creator_headers)
    client.post("/v1/me/missions", json={"template_id": template["id"]},
                headers=learner_headers)
    r = client.get(f"/v1/creator/missions/{template['id']}/adoption",
                   headers=creator_headers)
    assert r.json() == {"template_id": template["id"], "adopted": 1,
                        "completed": 0}


def test_creator_can_replace_the_step_list(client, creator_headers,
                                          learner_headers):
    template = _published_template(client, creator_headers)
    r = client.patch(f"/v1/creator/missions/{template['id']}", json={
        "steps": [
            {"title": "New step A", "description": "", "order": 1},
            {"title": "New step B", "description": "", "order": 2},
            {"title": "New step C", "description": "", "order": 3},
        ],
    }, headers=creator_headers)
    assert r.status_code == 200
    assert [s["title"] for s in r.json()["steps"]] == [
        "New step A", "New step B", "New step C"
    ]
    # And a fresh learner sees the new list.
    assert len(client.get("/v1/missions",
                          headers=learner_headers).json()[0]["steps"]) == 3