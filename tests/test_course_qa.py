"""Grounded course Q&A: answers strictly from lesson text.

The backend assembles the content; the coach only reasons over it. The
coach is faked at the client boundary, so these tests pin the assembly
(which lessons, how much, whose titles are cited) and the access rules —
not the model's wording.
"""
import pytest


@pytest.fixture
def course_with_content(client, creator_headers, db_session):
    """A published course with two content-bearing lessons."""
    from app.models import Course

    cid = client.post("/v1/courses", json={
        "title": "Photosynthesis 101",
        "description": "A short course",
        "category": "Academics",
        "outcomes": ["Learn"],
        "target_audience": "Everyone",
    }, headers=creator_headers).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    lids = []
    for title, content in (
        ("Light reactions", "Chlorophyll absorbs red and blue light."),
        ("Calvin cycle", "Rubisco fixes carbon dioxide into sugar."),
    ):
        lids.append(client.post(f"/v1/courses/{cid}/lessons", json={
            "module_id": mid, "title": title, "topic": "Photosynthesis",
            "lesson_type": "lesson", "content": content,
        }, headers=creator_headers).json()["id"])
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return {"course_id": cid, "lessons": lids}


@pytest.fixture
def coach_answer(monkeypatch):
    """Capture what the backend hands the coach."""
    from app.services import coach_client as cc

    seen = {}

    def _fake(user_id, course_title, content, question):
        seen.update(course_title=course_title, content=content,
                    question=question)
        return "Mock answer from the lesson text."

    monkeypatch.setattr(cc, "ask_course_question", _fake)
    return seen


def _ask(client, headers, course_id, question="What absorbs light?",
         lesson_id=None):
    body = {"course_id": course_id, "question": question}
    if lesson_id is not None:
        body["lesson_id"] = lesson_id
    return client.post("/v1/me/course-qa", headers=headers, json=body)


def test_answer_comes_with_cited_lessons(client, learner_headers,
                                          course_with_content, coach_answer):
    cid = course_with_content["course_id"]
    client.post(f"/v1/me/enroll/{cid}", headers=learner_headers)

    r = _ask(client, learner_headers, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Mock answer from the lesson text."
    assert body["sources"] == ["Light reactions", "Calvin cycle"]

    # And the coach received the actual lesson text, not metadata.
    assert "Chlorophyll absorbs red and blue light." in coach_answer["content"]
    assert "Rubisco fixes carbon dioxide" in coach_answer["content"]
    assert coach_answer["course_title"] == "Photosynthesis 101"
    assert coach_answer["question"] == "What absorbs light?"


def test_single_lesson_scopes_content_and_sources(client, learner_headers,
                                                  course_with_content,
                                                  coach_answer):
    cid = course_with_content["course_id"]
    second = course_with_content["lessons"][1]
    client.post(f"/v1/me/enroll/{cid}", headers=learner_headers)

    r = _ask(client, learner_headers, cid, lesson_id=second)
    assert r.status_code == 200, r.text
    assert r.json()["sources"] == ["Calvin cycle"]
    assert "Rubisco" in coach_answer["content"]
    assert "Chlorophyll" not in coach_answer["content"]


def test_unenrolled_learner_is_refused(client, learner_headers,
                                      course_with_content, coach_answer):
    r = _ask(client, learner_headers, course_with_content["course_id"])
    assert r.status_code == 403


def test_unknown_course_is_404(client, learner_headers):
    assert _ask(client, learner_headers, 987654).status_code == 404


def test_lesson_from_another_course_is_404(client, learner_headers,
                                           course_with_content,
                                           creator_headers, db_session):
    from app.models import Course

    other = client.post("/v1/courses", json={
        "title": "Other", "description": "Other course",
        "category": "Design", "outcomes": ["Learn"],
        "target_audience": "Everyone",
    }, headers=creator_headers).json()["id"]
    db_session.get(Course, other).status = "published"
    db_session.commit()

    cid = course_with_content["course_id"]
    client.post(f"/v1/me/enroll/{cid}", headers=learner_headers)
    client.post(f"/v1/me/enroll/{other}", headers=learner_headers)
    foreign = course_with_content["lessons"][0]
    # The lesson belongs to cid, not to other: asking within other 404s
    # instead of leaking across courses.
    r = client.post("/v1/me/course-qa", headers=learner_headers, json={
        "course_id": other, "lesson_id": foreign, "question": "What?",
    })
    assert r.status_code == 404


def test_coach_outage_is_502_not_500(client, learner_headers,
                                     course_with_content, monkeypatch):
    from app.services import coach_client as cc

    def _boom(*a, **k):
        raise cc.CoachError("coach is down")

    monkeypatch.setattr(cc, "ask_course_question", _boom)
    cid = course_with_content["course_id"]
    client.post(f"/v1/me/enroll/{cid}", headers=learner_headers)
    assert _ask(client, learner_headers, cid).status_code == 502


def test_unconfigured_coach_is_503(client, learner_headers, course_with_content,
                                   monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "coach_base_url", "")
    cid = course_with_content["course_id"]
    client.post(f"/v1/me/enroll/{cid}", headers=learner_headers)
    # No fake here: the real client must fail closed before any HTTP.
    assert _ask(client, learner_headers, cid).status_code == 503
