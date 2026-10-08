"""Deleting a user must work from outside the API too.

DELETE /v1/users/me pre-deletes every table by hand, so it never noticed
that most user foreign keys had no ON DELETE action. A dashboard (or SQL)
delete hits the bare constraints directly and died on the first one. These
tests pin both halves: the metadata declares an action everywhere, and a
raw row delete behaves exactly like the API path (private data gone, shared
and audit data detached, never blocking).
"""
from app.models import User


def _user_tables():
    """Every table with a direct FK to users.id, and its expected action."""
    return {
        # Private data goes with the account.
        "buddy_matches": "CASCADE",
        "conversation_messages": "CASCADE",
        "enrollments": "CASCADE",
        "lesson_progress": "CASCADE",
        "creator_profiles": "CASCADE",
        "quiz_results": "CASCADE",
        "xp_events": "CASCADE",
        "badges": "CASCADE",
        "study_plans": "CASCADE",
        "check_ins": "CASCADE",
        "missions": "CASCADE",
        "payments": "CASCADE",
        "community_posts": "CASCADE",
        "community_replies": "CASCADE",
        "direct_messages": "CASCADE",
        "learner_materials": "CASCADE",
        "learning_paths": "CASCADE",
        "study_schedules": "CASCADE",
        # Shared and audit data is detached, never destroyed.
        "analytics_events": "SET NULL",
        "courses": "SET NULL",
        "mission_templates": "SET NULL",
        "email_log": "SET NULL",
        "password_reset_tokens": "SET NULL",
        "email_verification_tokens": "SET NULL",
        "admin_audit_log": "SET NULL",
    }


def test_every_user_fk_declares_an_action():
    """A new user-rooted FK without ondelete fails here, not in prod."""
    from sqlalchemy import ForeignKeyConstraint

    from app.database import Base
    import app.models  # noqa: F401  ensure every model is registered

    actions = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            for element in constraint.elements:
                if element.column.table.name != "users":
                    continue
                actions.setdefault(table.name, element.ondelete)
    expected = _user_tables()
    assert set(actions) == set(expected), (
        f"untracked user FKs: {set(actions) ^ set(expected)}")
    for table, action in expected.items():
        assert (actions[table] or "").upper() == action, (
            f"{table}: ondelete is {actions[table]!r}, want {action}")


def _seed_everything(client, creator_headers, db_session):
    """One user with a row in every user-rooted table."""
    from datetime import datetime, timedelta, timezone

    from app.models import (
        AnalyticsEvent,
        Badge,
        BuddyMatch,
        CheckIn,
        CommunityPost,
        CommunityReply,
        ConversationMessage,
        Course,
        CreatorProfile,
        DirectMessage,
        EmailLog,
        EmailVerificationToken,
        Enrollment,
        LearnerMaterial,
        LearningPath,
        LearningPathStep,
        Lesson,
        LessonProgress,
        MaterialAnalysis,
        Mission,
        MissionStep,
        MissionTemplate,
        MissionTemplateStep,
        PasswordResetToken,
        Payment,
        QuizResult,
        ScheduleDay,
        StudyPlan,
        StudySchedule,
        XpEvent,
    )

    client.post("/v1/auth/register", json={
        "email": "doomed@example.com", "password": "password123",
        "learner_name": "Doomed", "is_creator": True,
    })
    user = db_session.query(User).filter(
        User.email == "doomed@example.com").one()
    uid = user.id
    now = datetime.now(timezone.utc)
    course = Course(title="C", description="d", creator_user_id=uid)
    db_session.add(course)
    db_session.flush()
    template = MissionTemplate(creator_user_id=uid, title="T")
    db_session.add(template)
    db_session.flush()
    lesson = Lesson(course_id=course.id, order=1, title="L", topic="T")
    db_session.add(lesson)
    db_session.flush()
    material = LearnerMaterial(user_id=uid, storage_key="material/k")
    db_session.add(material)
    db_session.flush()
    path = LearningPath(user_id=uid, title="P")
    db_session.add(path)
    db_session.flush()
    schedule = StudySchedule(user_id=uid, material_id=material.id)
    db_session.add(schedule)
    db_session.flush()
    mission = Mission(user_id=uid, title="M")
    db_session.add(mission)
    db_session.flush()
    post = CommunityPost(author_user_id=uid, title="P", body="b")
    db_session.add(post)
    db_session.flush()

    db_session.add_all([
        Enrollment(user_id=uid, course_id=course.id),
        LessonProgress(user_id=uid, lesson_id=lesson.id),
        QuizResult(user_id=uid, topic="T", score_percent=80.0),
        XpEvent(user_id=uid, activity="lesson", amount=25),
        Badge(user_id=uid, badge_id="b", name="B"),
        StudyPlan(user_id=uid),
        CheckIn(user_id=uid, check_date=now.date()),
        MissionStep(mission_id=mission.id, title="S", order=1),
        MissionTemplateStep(template_id=template.id, title="TS", order=1),
        Payment(user_id=uid, course_id=course.id, amount_naira=100,
                 reference="del-ref-1", status="success"),
        CommunityReply(post_id=post.id, author_user_id=uid, body="r"),
        DirectMessage(sender_id=uid, recipient_id=uid, body="m"),
        BuddyMatch(user_id=uid, buddy_user_id=uid),
        ConversationMessage(user_id=uid, role="user", content="hi"),
        CreatorProfile(user_id=uid, display_name="Doomed"),
        MaterialAnalysis(material_id=material.id),
        LearningPathStep(path_id=path.id, course_id=course.id, position=0),
        ScheduleDay(schedule_id=schedule.id, day_number=1),
        AnalyticsEvent(user_id=uid, event="e"),
        EmailLog(user_id=uid, to_email="d@e.com", template="t",
                 subject="s", status="sent"),
        PasswordResetToken(user_id=uid, token_hash="h",
                           expires_at=now + timedelta(hours=1)),
        EmailVerificationToken(user_id=uid, token_hash="h",
                               expires_at=now + timedelta(hours=1)),
    ])
    db_session.commit()
    return uid


def test_dashboard_style_delete_removes_private_data(client, creator_headers,
                                                     db_session):
    """DELETE the user row directly — no delete_me, no manual cleanup, the
    way Supabase dashboard deletes work. Must not raise."""
    from app.models import (
        AnalyticsEvent,
        CheckIn,
        Course,
        EmailLog,
        LearnerMaterial,
        LearningPath,
        Mission,
        MissionTemplate,
        Payment,
        StudyPlan,
        StudySchedule,
    )

    uid = _seed_everything(client, creator_headers, db_session)
    db_session.delete(db_session.get(User, uid))
    db_session.commit()

    assert db_session.get(User, uid) is None
    # Private rows are gone, through every cascade level.
    for model in (CheckIn, LearnerMaterial, LearningPath, Mission,
                  Payment, StudyPlan, StudySchedule):
        assert db_session.query(model).filter(
            getattr(model, "user_id", None) == uid).count() == 0, model
    # Shared rows survive, detached.
    assert db_session.query(Course).count() == 1
    assert db_session.query(Course).one().creator_user_id is None
    assert db_session.query(MissionTemplate).count() == 1
    assert db_session.query(MissionTemplate).one().creator_user_id is None
    # Audit rows survive, anonymized. (The fixture creator has their own
    # rows, so filter to this user: NULL after the delete anonymized them.
    # Doomed owns two events — the register tracking plus the seeded one.)
    assert db_session.query(AnalyticsEvent).filter(
        AnalyticsEvent.user_id == uid).count() == 0
    assert db_session.query(AnalyticsEvent).filter(
        AnalyticsEvent.user_id.is_(None)).count() == 2
    assert db_session.query(EmailLog).filter(
        EmailLog.user_id == uid).count() == 0
    assert db_session.query(EmailLog).filter(
        EmailLog.user_id.is_(None)).count() == 2


def test_api_delete_still_works_end_to_end(client, creator_headers, db_session):
    """The manual path must keep working with cascades present: pre-deleted
    rows simply leave the cascades nothing to do."""
    uid = _seed_everything(client, creator_headers, db_session)
    token = client.post("/v1/auth/login", json={
        "email": "doomed@example.com", "password": "password123",
    }).json()["access_token"]

    r = client.delete("/v1/users/me",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert db_session.get(User, uid) is None
