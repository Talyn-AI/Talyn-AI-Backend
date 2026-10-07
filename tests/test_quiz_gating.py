"""Quiz lessons gate progress.

A quiz is an ordinary lesson with lesson_type "quiz", so the gate is really
about ordering: the nearest earlier quiz lesson decides whether the learner may
move on. These tests pin that, and pin the two ways it can go wrong — crediting
a result that is not tied to a quiz lesson, and re-gating somebody whose
progress predates the quiz.
"""
import pytest


@pytest.fixture()
def make_learner(client, onboard):
    """Register + onboard + sign in an extra learner, returning auth headers."""

    def _make(email: str) -> dict:
        client.post("/v1/auth/register", json={
            "email": email, "password": "password123",
            "learner_name": "Extra Learner",
        })
        token = client.post("/v1/auth/login", json={
            "email": email, "password": "password123",
        }).json()["access_token"]
        onboard(email)
        return {"Authorization": f"Bearer {token}"}

    return _make


def build_course(client, headers, title, kinds):
    """Create a published course whose lessons are `kinds` ("lesson"/"quiz")."""
    from app.models import Course

    cid = client.post("/v1/courses", json={
        "title": title,
        "description": "Used by the quiz gate tests",
        "category": "Design",
        "outcomes": ["Learn something"],
        "target_audience": "Everyone",
    }, headers=headers).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "Module One"},
                      headers=headers).json()["id"]

    ids = []
    for n, kind in enumerate(kinds, start=1):
        ids.append(client.post(f"/v1/courses/{cid}/lessons", json={
            "module_id": mid,
            "title": f"{'Mini-Quiz' if kind == 'quiz' else 'Lesson'} {n}",
            "topic": f"Topic {n}",
            "lesson_type": kind,
            "content": "Some content.",
        }, headers=headers).json()["id"])

    return cid, mid, ids


@pytest.fixture()
def quiz_course(client, creator_headers, db_session):
    """A published course shaped: lesson -> quiz -> lesson."""
    from app.models import Course

    cid, mid, ids = build_course(
        client, creator_headers, "Gated Course", ["lesson", "quiz", "lesson"]
    )
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return {"course_id": cid, "first": ids[0], "quiz": ids[1], "last": ids[2]}


@pytest.fixture()
def enrolled(client, learner_headers, quiz_course):
    """The learner is enrolled in the course with a quiz."""
    r = client.post(f"/v1/me/enroll/{quiz_course['course_id']}",
                    headers=learner_headers)
    assert r.status_code in (200, 201), r.text
    return learner_headers


def pass_quiz(client, headers, quiz_course, score=90):
    return client.post("/v1/me/quiz-results", headers=headers, json={
        "course_id": quiz_course["course_id"],
        "lesson_id": quiz_course["quiz"],
        "topic": "Topic 2",
        "score_percent": score,
    })


# ── Pointing at the quiz ─────────────────────────────────────────────────────


def test_completing_a_lesson_points_at_the_quiz_that_follows(
    client, enrolled, quiz_course
):
    r = client.post(f"/v1/me/lessons/{quiz_course['first']}/complete",
                    headers=enrolled)
    assert r.status_code == 200, r.text
    nxt = r.json()["next"]
    assert nxt["type"] == "quiz"
    assert nxt["lesson_id"] == quiz_course["quiz"]
    assert nxt["quiz_required"] is True
    assert nxt["title"] == "Mini-Quiz 2"


def test_a_course_without_quizzes_never_reports_one(
    client, creator_headers, db_session, make_learner
):
    from app.models import Course

    cid, _mid, ids = build_course(
        client, creator_headers, "No Quiz Course", ["lesson", "lesson"]
    )
    db_session.get(Course, cid).status = "published"
    db_session.commit()

    h = make_learner("noquiz@example.com")
    client.post(f"/v1/me/enroll/{cid}", headers=h)

    r = client.post(f"/v1/me/lessons/{ids[0]}/complete", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["next"]["type"] == "lesson"
    assert r.json()["next"]["quiz_required"] is False


def test_completing_the_final_lesson_reports_the_course_complete(
    client, enrolled, quiz_course
):
    pass_quiz(client, enrolled, quiz_course)
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/complete",
                    headers=enrolled)
    assert r.status_code == 200, r.text
    assert r.json()["next"] == {
        "type": "course_complete", "lesson_id": None, "title": None, "topic": None,
        "quiz_required": False,
    }


# ── The gate ─────────────────────────────────────────────────────────────────


def test_starting_the_lesson_after_an_unpassed_quiz_is_blocked(
    client, enrolled, quiz_course
):
    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/start", headers=enrolled)
    assert r.status_code == 409, r.text
    assert "Mini-Quiz 2" in r.json()["detail"]


def test_completing_the_lesson_after_an_unpassed_quiz_is_blocked(
    client, enrolled, quiz_course
):
    """Skipping `start` must not skip the gate."""
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/complete",
                    headers=enrolled)
    assert r.status_code == 409, r.text
    assert "Mini-Quiz 2" in r.json()["detail"]


def test_passing_the_quiz_unblocks_the_next_lesson(client, enrolled, quiz_course):
    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    assert pass_quiz(client, enrolled, quiz_course, score=90).status_code == 200
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/start", headers=enrolled)
    assert r.status_code == 200, r.text


def test_a_failing_score_does_not_unblock(client, enrolled, quiz_course):
    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    assert pass_quiz(client, enrolled, quiz_course, score=40).status_code == 200
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/start", headers=enrolled)
    assert r.status_code == 409


def test_the_pass_mark_is_configurable(client, enrolled, quiz_course, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "quiz_pass_percent", 95.0)
    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    pass_quiz(client, enrolled, quiz_course, score=90)
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/start", headers=enrolled)
    assert r.status_code == 409, "90% should not clear a 95% pass mark"


def test_a_result_without_a_lesson_id_does_not_unblock(client, enrolled, quiz_course):
    """The regression this column exists to prevent.

    Standalone attempts (practice quizzes, rows written before the link) carry
    lesson_id NULL. If the gate counted one of those as a pass, every learner
    would walk straight through every quiz in the app.
    """
    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    r = client.post("/v1/me/quiz-results", headers=enrolled, json={
        "course_id": quiz_course["course_id"],
        "topic": "Topic 2",
        "score_percent": 100,
    })
    assert r.status_code == 200, r.text

    blocked = client.post(f"/v1/me/lessons/{quiz_course['last']}/start",
                          headers=enrolled)
    assert blocked.status_code == 409


def test_recompleting_a_finished_lesson_is_not_gated(
    client, enrolled, quiz_course, db_session
):
    """A quiz inserted behind somebody must not lock them out of their history.

    Re-submitting a completed lesson is a no-op clients rely on; it must keep
    answering 200 even where the gate would now refuse that lesson.
    """
    from app.models import QuizResult

    client.post(f"/v1/me/lessons/{quiz_course['first']}/complete", headers=enrolled)
    pass_quiz(client, enrolled, quiz_course)
    client.post(f"/v1/me/lessons/{quiz_course['last']}/complete", headers=enrolled)

    # Put the learner back in the state they were in before the quiz existed.
    db_session.query(QuizResult).filter(
        QuizResult.lesson_id == quiz_course["quiz"]
    ).delete()
    db_session.commit()

    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/complete",
                    headers=enrolled)
    assert r.status_code == 200, r.text
    assert r.json()["already_completed"] is True
    assert "next" in r.json()


def test_the_quiz_lesson_itself_is_always_reachable(client, enrolled, quiz_course):
    """The gate sits *before* the quiz, never on it."""
    r = client.post(f"/v1/me/lessons/{quiz_course['quiz']}/start", headers=enrolled)
    assert r.status_code == 200, r.text


def test_creator_previewing_their_own_course_is_not_gated(
    client, creator_headers, quiz_course
):
    """Managing a course bypasses progress checks; preview should not be gated."""
    r = client.post(f"/v1/me/lessons/{quiz_course['last']}/start",
                    headers=creator_headers)
    assert r.status_code == 200, r.text


def test_an_unpassed_quiz_elsewhere_does_not_gate_this_course(
    client, creator_headers, db_session, enrolled, quiz_course, make_learner
):
    """The gate is scoped to the lesson's own course."""
    from app.models import Course

    other_cid, _mid, other_ids = build_course(
        client, creator_headers, "Unrelated Course", ["lesson", "lesson"]
    )
    db_session.get(Course, other_cid).status = "published"
    db_session.commit()

    r = client.post(f"/v1/me/enroll/{other_cid}", headers=enrolled)
    assert r.status_code in (200, 201), r.text

    started = client.post(f"/v1/me/lessons/{other_ids[0]}/start", headers=enrolled)
    assert started.status_code == 200, started.text
