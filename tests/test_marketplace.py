"""Tests for stub purchases, content gating, lesson start, and analytics."""
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
    headers = _register(client, "pay-creator@example.com", "PayCreator",
                          is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def learner_headers(client, onboard):
    headers = _register(client, "payer@example.com", "Payer")
    onboard(headers)
    return headers

@pytest.fixture()
def paid_course_id(client, creator_headers, db_session):
    from app.models import Course

    cid = client.post(
        "/v1/courses",
        json={"title": "Paid Design", "description": "Worth it",
              "category": "Design", "outcomes": ["Earn"],
              "target_audience": "All", "thumbnail_key": "t.png",
              "course_type": "paid", "price_naira": 12000},
        headers=creator_headers,
    ).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    client.post(f"/v1/courses/{cid}/lessons",
                json={"module_id": mid, "title": "Secret Sauce",
                      "topic": "Pricing", "content": "Charge more."},
                headers=creator_headers)
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


@pytest.fixture()
def free_course_id(client, creator_headers, db_session):
    from app.models import Course

    cid = client.post(
        "/v1/courses",
        json={"title": "Free Design", "description": "Freebie",
              "category": "Design", "outcomes": ["Learn"],
              "target_audience": "All", "thumbnail_key": "t.png"},
        headers=creator_headers,
    ).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    client.post(f"/v1/courses/{cid}/lessons",
                json={"module_id": mid, "title": "Open Lesson",
                      "topic": "Basics", "content": "Free content."},
                headers=creator_headers)
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


# ── Purchases ─────────────────────────────────────────────────────────────────

def test_purchase_flow(client, learner_headers, paid_course_id):
    r = client.post(f"/v1/courses/{paid_course_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrolled"] is True
    assert body["payment"]["amount_naira"] == 12000
    assert body["payment"]["currency"] == "NGN"
    assert body["payment"]["status"] == "success"
    assert body["payment"]["provider"] == "stub"
    assert body["payment"]["reference"].startswith("stub-")

    rows = client.get("/v1/me/enrollments", headers=learner_headers).json()
    assert len(rows) == 1

    # duplicate purchase rejected
    assert client.post(f"/v1/courses/{paid_course_id}/purchase",
                       headers=learner_headers).status_code == 409


def test_purchase_rejects_free_and_drafts(client, learner_headers,
                                          free_course_id, creator_headers):
    r = client.post(f"/v1/courses/{free_course_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 422
    draft = client.post("/v1/courses", json={"title": "DraftPaid",
                                             "course_type": "paid",
                                             "price_naira": 1000},
                        headers=creator_headers).json()["id"]
    assert client.post(f"/v1/courses/{draft}/purchase",
                       headers=learner_headers).status_code == 422
    assert client.post("/v1/courses/99999/purchase",
                       headers=learner_headers).status_code == 404
    assert client.post(
        f"/v1/courses/{free_course_id}/purchase").status_code == 401


# ── Content gating ────────────────────────────────────────────────────────────

def test_paid_content_gated(client, learner_headers, creator_headers,
                            paid_course_id):
    lid = client.get(f"/v1/courses/{paid_course_id}/lessons",
                     headers=creator_headers).json()[0]["id"]

    # anonymous: structure without content
    rows = client.get(f"/v1/courses/{paid_course_id}/lessons").json()
    assert rows[0]["content"] is None
    assert client.get(f"/v1/lessons/{lid}").json()["content"] is None

    # direct enroll: still locked
    client.post(f"/v1/me/enroll/{paid_course_id}", headers=learner_headers)
    assert client.get(f"/v1/lessons/{lid}",
                      headers=learner_headers).json()["content"] is None

    # purchase unlocks (upgrades the preview enrollment)
    r = client.post(f"/v1/courses/{paid_course_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 200, r.text
    assert client.get(
        f"/v1/lessons/{lid}", headers=learner_headers).json()["content"] == "Charge more."
    rows = client.get(f"/v1/courses/{paid_course_id}/lessons",
                      headers=learner_headers).json()
    assert rows[0]["content"] == "Charge more."


def test_free_content_public(client, free_course_id):
    lid = client.get(f"/v1/courses/{free_course_id}/lessons").json()[0]["id"]
    assert client.get(f"/v1/lessons/{lid}").json()["content"] == "Free content."


def test_lesson_detail_draft_rules(client, creator_headers, learner_headers):
    cid = client.post("/v1/courses", json={"title": "Drafty"},
                      headers=creator_headers).json()["id"]
    lid = client.post(f"/v1/courses/{cid}/lessons",
                      json={"title": "D", "topic": "T", "content": "draft-c"},
                      headers=creator_headers).json()["id"]
    assert client.get(f"/v1/lessons/{lid}").status_code == 401
    assert client.get(f"/v1/lessons/{lid}",
                      headers=learner_headers).status_code == 403
    assert client.get(f"/v1/lessons/{lid}",
                      headers=creator_headers).json()["content"] == "draft-c"
    assert client.get("/v1/lessons/99999",
                      headers=creator_headers).status_code == 404


# ── Lesson start + progress gating ────────────────────────────────────────────

def test_progress_needs_enrollment_even_on_drafts(client, learner_headers,
                                                  creator_headers):
    cid = client.post("/v1/courses", json={"title": "DraftHole"},
                      headers=creator_headers).json()["id"]
    lid = client.post(f"/v1/courses/{cid}/lessons",
                      json={"title": "D", "topic": "T", "content": "c"},
                      headers=creator_headers).json()["id"]
    # unenrolled learner: locked even though the course is a draft
    assert client.post(f"/v1/me/lessons/{lid}/start",
                       headers=learner_headers).status_code == 403
    assert client.post(f"/v1/me/lessons/{lid}/complete", json={},
                       headers=learner_headers).status_code == 403
    # owner preview bypass still works without enrolling
    assert client.post(f"/v1/me/lessons/{lid}/start",
                       headers=creator_headers).status_code == 200
    assert client.post(f"/v1/me/lessons/{lid}/complete", json={},
                       headers=creator_headers).status_code == 200

def test_start_and_complete_require_enrollment_on_published(
        client, learner_headers, paid_course_id):
    lid = client.get(f"/v1/courses/{paid_course_id}/lessons").json()[0]["id"]
    assert client.post(f"/v1/me/lessons/{lid}/start",
                       headers=learner_headers).status_code == 403
    assert client.post(f"/v1/me/lessons/{lid}/complete", json={},
                       headers=learner_headers).status_code == 403

    # direct enroll first (preview path), then start works
    client.post(f"/v1/me/enroll/{paid_course_id}", headers=learner_headers)
    r = client.post(f"/v1/me/lessons/{lid}/start", headers=learner_headers)
    assert r.json() == {"started": True, "lesson_id": lid, "first_time": True}
    r = client.post(f"/v1/me/lessons/{lid}/start", headers=learner_headers)
    assert r.json()["first_time"] is False


# ── Analytics + dashboard ─────────────────────────────────────────────────────

def test_creator_activity_and_revenue(client, creator_headers, learner_headers,
                                     paid_course_id):
    client.post(f"/v1/courses/{paid_course_id}/purchase",
                headers=learner_headers)
    lid = client.get(f"/v1/courses/{paid_course_id}/lessons",
                     headers=creator_headers).json()[0]["id"]
    client.post(f"/v1/me/lessons/{lid}/complete", json={},
                headers=learner_headers)

    dash = client.get("/v1/me/creator/dashboard",
                      headers=creator_headers).json()
    assert dash["total_revenue_naira"] == 12000
    assert dash["total_learners"] == 1
    events = [a["event"] for a in dash["recent_activity"]]
    assert "course_purchased" in events
    assert "course_enrolled" in events
    assert "lesson_completed" in events
    assert "course_completed" in events

    activity = client.get("/v1/me/creator/activity",
                          headers=creator_headers).json()
    assert len(activity) >= 4
    assert client.get("/v1/me/creator/activity",
                      headers=learner_headers).status_code == 403
