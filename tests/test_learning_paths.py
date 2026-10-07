"""Stored learning paths: ordering over published courses.

Progress is derived from enrollments, never stored — so these tests pin the
derivation (not_started / in_progress / completed), the validation (drafts
and unknown ids), and the ownership (one learner's paths are invisible to
another).
"""
import pytest


@pytest.fixture
def published_courses(client, creator_headers, db_session):
    """Three published single-lesson courses, returned oldest first."""
    from app.models import Course

    ids = []
    for n in (1, 2, 3):
        cid = client.post("/v1/courses", json={
            "title": f"Path Course {n}",
            "description": "Used by the learning path tests",
            "category": "Design",
            "outcomes": ["Learn something"],
            "target_audience": "Everyone",
        }, headers=creator_headers).json()["id"]
        mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                          headers=creator_headers).json()["id"]
        client.post(f"/v1/courses/{cid}/lessons", json={
            "module_id": mid, "title": f"Lesson {n}", "topic": f"Topic {n}",
            "lesson_type": "lesson", "content": "Some content.",
        }, headers=creator_headers)
        db_session.get(Course, cid).status = "published"
        db_session.commit()
        ids.append(cid)
    return ids


@pytest.fixture
def draft_course(client, creator_headers):
    """A course that exists but is not published."""
    return client.post("/v1/courses", json={
        "title": "Draft Course",
        "description": "Not published",
        "category": "Design",
        "outcomes": ["Learn"],
        "target_audience": "Everyone",
    }, headers=creator_headers).json()["id"]


def _create(client, headers, course_ids, title="My Path"):
    return client.post("/v1/me/paths", headers=headers, json={
        "title": title, "course_ids": course_ids,
    })


# ── Creating ─────────────────────────────────────────────────────────────────


def test_create_returns_steps_in_order_with_zero_progress(
    client, learner_headers, published_courses
):
    r = _create(client, learner_headers, published_courses)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "My Path"
    assert [s["course_id"] for s in body["steps"]] == published_courses
    assert [s["position"] for s in body["steps"]] == [0, 1, 2]
    assert {s["status"] for s in body["steps"]} == {"not_started"}
    assert body["courses_total"] == 3
    assert body["courses_completed"] == 0
    assert body["completion_percent"] == 0.0


def test_create_dedupes_repeated_courses(client, learner_headers,
                                         published_courses):
    a, b, _ = published_courses
    r = _create(client, learner_headers, [a, b, a, b, a])
    assert r.status_code == 201, r.text
    assert [s["course_id"] for s in r.json()["steps"]] == [a, b]


def test_create_rejects_a_draft_course_by_name(
    client, learner_headers, published_courses, draft_course
):
    r = _create(client, learner_headers, [published_courses[0], draft_course])
    assert r.status_code == 422, r.text
    assert "Draft Course" in r.json()["detail"]


def test_create_rejects_an_unknown_course(client, learner_headers,
                                          published_courses):
    r = _create(client, learner_headers, [published_courses[0], 987654])
    assert r.status_code == 404
    assert "987654" in r.json()["detail"]


def test_create_rejects_an_empty_list(client, learner_headers):
    r = client.post("/v1/me/paths", headers=learner_headers, json={
        "title": "Empty", "course_ids": [],
    })
    assert r.status_code == 422


def test_create_needs_onboarding(client, published_courses):
    client.post("/v1/auth/register", json={
        "email": "freshpath@example.com", "password": "password123",
        "learner_name": "Fresh",
    })
    token = client.post("/v1/auth/login", json={
        "email": "freshpath@example.com", "password": "password123",
    }).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert _create(client, h, published_courses).status_code == 409


# ── Progress derivation ──────────────────────────────────────────────────────


def test_enrolling_moves_the_step_to_in_progress(
    client, learner_headers, published_courses, db_session
):
    pid = _create(client, learner_headers, published_courses).json()["id"]
    client.post(f"/v1/me/enroll/{published_courses[0]}", headers=learner_headers)

    steps = client.get(f"/v1/me/paths/{pid}",
                       headers=learner_headers).json()["steps"]
    assert [s["status"] for s in steps] == [
        "in_progress", "not_started", "not_started",
    ]


def test_finishing_a_course_completes_its_step(
    client, learner_headers, published_courses, db_session
):
    from sqlalchemy import select

    from app.models import Lesson

    pid = _create(client, learner_headers, published_courses).json()["id"]
    client.post(f"/v1/me/enroll/{published_courses[0]}", headers=learner_headers)
    lesson = db_session.scalar(
        select(Lesson).where(Lesson.course_id == published_courses[0])
    )
    r = client.post(f"/v1/me/lessons/{lesson.id}/complete",
                    headers=learner_headers)
    assert r.status_code == 200, r.text

    body = client.get(f"/v1/me/paths/{pid}", headers=learner_headers).json()
    assert [s["status"] for s in body["steps"]] == [
        "completed", "not_started", "not_started",
    ]
    assert body["courses_completed"] == 1
    assert body["completion_percent"] == 33.3


# ── Ownership ────────────────────────────────────────────────────────────────


def test_paths_are_per_learner(client, learner_headers, published_courses,
                               learner2):
    pid = _create(client, learner_headers, published_courses).json()["id"]

    assert client.get("/v1/me/paths", headers=learner2).json() == []
    assert client.get(f"/v1/me/paths/{pid}", headers=learner2).status_code == 404
    assert client.delete(f"/v1/me/paths/{pid}",
                         headers=learner2).status_code == 404


@pytest.fixture
def learner2(client, onboard):
    client.post("/v1/auth/register", json={
        "email": "path-two@example.com", "password": "password123",
        "learner_name": "Two",
    })
    token = client.post("/v1/auth/login", json={
        "email": "path-two@example.com", "password": "password123",
    }).json()["access_token"]
    onboard("path-two@example.com")
    return {"Authorization": f"Bearer {token}"}


# ── Updating and deleting ────────────────────────────────────────────────────


def test_update_replaces_the_list_and_renames(
    client, learner_headers, published_courses
):
    pid = _create(client, learner_headers, published_courses).json()["id"]
    r = client.put(f"/v1/me/paths/{pid}", headers=learner_headers, json={
        "title": "New Title",
        "course_ids": [published_courses[2], published_courses[0]],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "New Title"
    assert [s["course_id"] for s in body["steps"]] == [
        published_courses[2], published_courses[0],
    ]


def test_update_validates_like_create(
    client, learner_headers, published_courses, draft_course
):
    pid = _create(client, learner_headers, published_courses).json()["id"]
    r = client.put(f"/v1/me/paths/{pid}", headers=learner_headers, json={
        "course_ids": [draft_course],
    })
    assert r.status_code == 422


def test_delete_removes_the_path_but_not_the_enrollments(
    client, learner_headers, published_courses, db_session
):
    from app.models import Enrollment, LearningPath

    pid = _create(client, learner_headers, published_courses).json()["id"]
    client.post(f"/v1/me/enroll/{published_courses[0]}", headers=learner_headers)

    assert client.delete(
        f"/v1/me/paths/{pid}", headers=learner_headers).status_code == 200
    assert db_session.query(LearningPath).count() == 0
    # A plan is not the work: the enrollment survives it.
    assert db_session.query(Enrollment).count() == 1


def test_deleting_a_course_drops_its_step(
    client, learner_headers, published_courses, creator_headers, db_session
):
    """Through the real deletion endpoint: its manual dependent deletes plus
    the step CASCADE must leave no step pointing at nothing."""
    pid = _create(client, learner_headers, published_courses).json()["id"]
    r = client.delete(f"/v1/courses/{published_courses[1]}",
                      headers=creator_headers)
    assert r.status_code == 200, r.text

    steps = client.get(f"/v1/me/paths/{pid}",
                       headers=learner_headers).json()["steps"]
    assert [s["course_id"] for s in steps] == [
        published_courses[0], published_courses[2],
    ]
