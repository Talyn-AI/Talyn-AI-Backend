"""Tests for advanced analytics (funnel, quiz aggregates, overview)."""
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
    headers = _register(client, "ana-creator@example.com", "AnaCreator",
                          is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def learner_headers(client, onboard):
    headers = _register(client, "ana-learner@example.com", "AnaLearner")
    onboard(headers)
    return headers

@pytest.fixture()
def published_course(client, creator_headers, db_session):
    from app.models import Course

    cid = client.post(
        "/v1/courses",
        json={"title": "Analytics 101", "description": "d", "category": "C",
              "outcomes": ["O"], "target_audience": "A",
              "thumbnail_key": "t.png"},
        headers=creator_headers,
    ).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    for i, topic in enumerate(("Alpha", "Beta"), start=1):
        client.post(f"/v1/courses/{cid}/lessons",
                    json={"module_id": mid, "title": f"L{i}", "topic": topic,
                          "content": "content"},
                    headers=creator_headers)
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


def _lessons(client, cid):
    return client.get(f"/v1/courses/{cid}/lessons").json()


def test_lesson_funnel(client, creator_headers, learner_headers,
                       published_course):
    lessons = _lessons(client, published_course)
    l1, l2 = lessons[0]["id"], lessons[1]["id"]

    client.post(f"/v1/me/enroll/{published_course}", headers=learner_headers)
    client.get(f"/v1/lessons/{l1}", headers=learner_headers)
    client.get(f"/v1/lessons/{l1}", headers=learner_headers)
    client.post(f"/v1/me/lessons/{l1}/start", headers=learner_headers)
    client.post(f"/v1/me/lessons/{l1}/complete", json={},
                headers=learner_headers)

    body = client.get(f"/v1/courses/{published_course}/analytics",
                      headers=creator_headers).json()
    assert body["course_id"] == published_course
    assert body["total_views"] == 2
    assert body["total_enrollments"] == 1
    assert body["total_completions"] == 0  # course not finished (l2 open)
    by_id = {l["lesson_id"]: l for l in body["lessons"]}
    assert by_id[l1]["views"] == 2
    assert by_id[l1]["starts"] == 1
    assert by_id[l1]["completions"] == 1
    assert by_id[l1]["completion_rate"] == 1.0
    assert by_id[l2]["views"] == 0
    assert by_id[l2]["completion_rate"] == 0.0


def test_quiz_topic_stats(client, creator_headers, learner_headers,
                          published_course):
    client.post("/v1/me/quiz-results",
                json={"course_id": published_course, "topic": "Alpha",
                      "score_percent": 60.0},
                headers=learner_headers)
    client.post("/v1/me/quiz-results",
                json={"course_id": published_course, "topic": "Alpha",
                      "score_percent": 80.0, "attempts": 2},
                headers=learner_headers)
    topics = client.get(f"/v1/courses/{published_course}/analytics",
                        headers=creator_headers).json()["quiz_topics"]
    assert topics == [{"topic": "Alpha", "attempts": 3,
                       "avg_score": 70.0, "best_score": 80.0}]


def test_analytics_requires_owner(client, learner_headers, published_course,
                                  admin_headers):
    assert client.get(
        f"/v1/courses/{published_course}/analytics").status_code == 401
    assert client.get(f"/v1/courses/{published_course}/analytics",
                      headers=learner_headers).status_code == 403
    assert client.get(f"/v1/courses/{published_course}/analytics",
                      headers=admin_headers).status_code == 200
    assert client.get("/v1/courses/99999/analytics",
                      headers=admin_headers).status_code == 404


def test_overview(client, creator_headers, learner_headers, published_course):
    client.post(f"/v1/me/enroll/{published_course}", headers=learner_headers)
    body = client.get("/v1/me/creator/analytics/overview?days=7",
                      headers=creator_headers).json()
    assert body["days"] == 7
    assert len(body["daily"]) == 7
    assert body["total_enrollments"] == 1
    assert body["active_learners"] == 1
    today = body["daily"][-1]
    assert today["enrollments"] == 1
    assert sum(d["enrollments"] for d in body["daily"]) == 1
    assert client.get("/v1/me/creator/analytics/overview",
                      headers=learner_headers).status_code == 403


def test_overview_empty_for_new_creator(client, creator_headers):
    body = client.get("/v1/me/creator/analytics/overview",
                      headers=creator_headers).json()
    assert body["total_enrollments"] == 0
    assert len(body["daily"]) == 30
