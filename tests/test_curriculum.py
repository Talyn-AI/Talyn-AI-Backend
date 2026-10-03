"""Tests for curriculum builder (modules/lessons) and the publish workflow."""
import pytest


def _register(client, email, name, is_creator=False):
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "secret12345",
              "learner_name": name, "is_creator": is_creator},
    )
    r = client.post("/v1/auth/login",
                    json={"email": email, "password": "secret12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def creator_headers(client, onboard):
    headers = _register(client, "curr@example.com", "Curr", is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def other_headers(client):
    return _register(client, "other2@example.com", "Other2", is_creator=True)


@pytest.fixture()
def course_id(client, creator_headers):
    return client.post(
        "/v1/courses",
        json={"title": "Curr Course", "description": "Full desc",
              "category": "Design", "outcomes": ["Learn X"],
              "target_audience": "Beginners",
              "thumbnail_key": "thumbs/c.png"},
        headers=creator_headers,
    ).json()["id"]


@pytest.fixture()
def module_id(client, creator_headers, course_id):
    return client.post(f"/v1/courses/{course_id}/modules",
                       json={"title": "Module 1"},
                       headers=creator_headers).json()["id"]


# ── Modules ───────────────────────────────────────────────────────────────────

def test_module_crud_and_reorder(client, creator_headers, course_id):
    m1 = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M1"},
                     headers=creator_headers).json()
    m2 = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M2"},
                     headers=creator_headers).json()
    assert (m1["order"], m2["order"]) == (1, 2)

    r = client.patch(f"/v1/courses/{course_id}/modules/{m1['id']}",
                     json={"title": "M1 renamed"}, headers=creator_headers)
    assert r.json()["title"] == "M1 renamed"

    r = client.put(f"/v1/courses/{course_id}/modules/reorder",
                   json={"ordered_ids": [m2["id"], m1["id"]]},
                   headers=creator_headers)
    assert r.status_code == 200
    mods = client.get(f"/v1/courses/{course_id}",
                      headers=creator_headers).json()["modules"]
    assert [(m["id"], m["order"]) for m in mods] == [(m2["id"], 1), (m1["id"], 2)]

    r = client.put(f"/v1/courses/{course_id}/modules/reorder",
                   json={"ordered_ids": [m1["id"]]}, headers=creator_headers)
    assert r.status_code == 422


def test_module_scoped_to_course(client, creator_headers, course_id):
    other = client.post("/v1/courses", json={"title": "Other"},
                        headers=creator_headers).json()["id"]
    mid = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    r = client.patch(f"/v1/courses/{other}/modules/{mid}", json={"title": "X"},
                     headers=creator_headers)
    assert r.status_code == 404


def test_delete_module_unassigns_lessons(client, creator_headers, course_id,
                                         module_id):
    lid = client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": module_id, "title": "L", "topic": "T",
              "content": "c"},
        headers=creator_headers,
    ).json()["id"]
    client.delete(f"/v1/courses/{course_id}/modules/{module_id}",
                  headers=creator_headers)
    body = client.get(f"/v1/courses/{course_id}",
                      headers=creator_headers).json()
    assert body["modules"] == []
    assert body["lessons"][0]["id"] == lid
    assert body["lessons"][0]["module_id"] is None


# ── Lessons ───────────────────────────────────────────────────────────────────

def test_lesson_crud_move_reorder(client, creator_headers, course_id, module_id):
    l1 = client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": module_id, "title": "L1", "topic": "T",
              "content": "c1"},
        headers=creator_headers).json()
    l2 = client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": module_id, "title": "L2", "topic": "T",
              "content": "c2"},
        headers=creator_headers).json()
    assert (l1["order"], l2["order"]) == (1, 2)

    r = client.put(f"/v1/courses/{course_id}/lessons/reorder?module_id={module_id}",
                   json={"ordered_ids": [l2["id"], l1["id"]]},
                   headers=creator_headers)
    assert r.status_code == 200

    m2 = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M2"},
                     headers=creator_headers).json()
    r = client.patch(f"/v1/lessons/{l1['id']}", json={"module_id": m2["id"]},
                     headers=creator_headers)
    assert r.json()["module_id"] == m2["id"]

    r = client.patch(f"/v1/lessons/{l1['id']}",
                     json={"title": "L1x", "module_id": None},
                     headers=creator_headers)
    assert r.json()["title"] == "L1x"
    assert r.json()["module_id"] is None

    assert client.delete(f"/v1/lessons/{l2['id']}",
                         headers=creator_headers).status_code == 200
    assert client.delete("/v1/lessons/99999",
                         headers=creator_headers).status_code == 404


def test_lesson_move_rejects_foreign_module(client, creator_headers, course_id):
    other = client.post("/v1/courses", json={"title": "Other"},
                        headers=creator_headers).json()["id"]
    foreign_mod = client.post(f"/v1/courses/{other}/modules", json={"title": "FM"},
                              headers=creator_headers).json()["id"]
    lid = client.post(f"/v1/courses/{course_id}/lessons",
                      json={"title": "L", "topic": "T", "content": "c"},
                      headers=creator_headers).json()["id"]
    r = client.patch(f"/v1/lessons/{lid}", json={"module_id": foreign_mod},
                     headers=creator_headers)
    assert r.status_code == 404


def test_curriculum_requires_owner(client, other_headers, course_id, module_id):
    assert client.post(f"/v1/courses/{course_id}/modules", json={"title": "X"},
                       headers=other_headers).status_code == 403
    assert client.post(f"/v1/courses/{course_id}/lessons",
                       json={"title": "X", "topic": "T"},
                       headers=other_headers).status_code == 403
    assert client.post(f"/v1/courses/{course_id}/publish",
                       headers=other_headers).status_code == 403


# ── Publish workflow ──────────────────────────────────────────────────────────

def _publishable(client, creator_headers, course_id, module_id):
    client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": module_id, "title": "L", "topic": "T",
              "content": "real content"},
        headers=creator_headers)


def test_publish_check_lists_errors(client, creator_headers, course_id):
    body = client.get(f"/v1/courses/{course_id}/publish-check",
                      headers=creator_headers).json()
    assert body["publishable"] is False
    assert any("module" in e.lower() for e in body["errors"])
    r = client.post(f"/v1/courses/{course_id}/publish", headers=creator_headers)
    assert r.status_code == 422
    # draft stays hidden
    assert client.get("/v1/courses").json() == []


def test_publish_unpublish_archive_cycle(client, creator_headers, course_id,
                                         module_id):
    _publishable(client, creator_headers, course_id, module_id)
    check = client.get(f"/v1/courses/{course_id}/publish-check",
                       headers=creator_headers).json()
    assert check == {"publishable": True, "errors": []}

    r = client.post(f"/v1/courses/{course_id}/publish", headers=creator_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert len(client.get("/v1/courses").json()) == 1  # discoverable

    r = client.post(f"/v1/courses/{course_id}/unpublish", headers=creator_headers)
    assert r.json()["status"] == "draft"
    assert client.get("/v1/courses").json() == []

    r = client.post(f"/v1/courses/{course_id}/archive", headers=creator_headers)
    assert r.json()["status"] == "archived"


def test_publish_validates_content_and_price(client, creator_headers):
    cid = client.post(
        "/v1/courses",
        json={"title": "Priced", "description": "d", "category": "C",
              "outcomes": ["O"], "target_audience": "A",
              "thumbnail_key": "t.png", "course_type": "free"},
        headers=creator_headers).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    client.post(f"/v1/courses/{cid}/lessons",
                json={"module_id": mid, "title": "Empty", "topic": "T"},
                headers=creator_headers)
    errors = client.get(f"/v1/courses/{cid}/publish-check",
                        headers=creator_headers).json()["errors"]
    assert any("content" in e.lower() for e in errors)


def test_preview_before_and_after_publish(client, creator_headers, course_id,
                                          module_id):
    _publishable(client, creator_headers, course_id, module_id)
    r = client.get(f"/v1/courses/{course_id}/preview", headers=creator_headers)
    assert r.status_code == 200
    assert r.json()["modules"][0]["lessons"][0]["content"] == "real content"
    assert client.get(f"/v1/courses/{course_id}/preview").status_code == 401
    client.post(f"/v1/courses/{course_id}/publish", headers=creator_headers)
    assert client.get(f"/v1/courses/{course_id}").status_code == 200
