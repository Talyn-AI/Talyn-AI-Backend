"""Users router: profile read/update, password change, account deletion."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.onboarding import PACE_BY_KEY, WEEKLY_TARGET_BY_PACE
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.models import (
    Badge,
    BuddyMatch,
    CommunityPost,
    CommunityReply,
    ConversationMessage,
    DirectMessage,
    EmailLog,
    Enrollment,
    LessonProgress,
    Mission,
    MissionStep,
    PasswordResetToken,
    Payment,
    QuizResult,
    StudyPlan,
    User,
    XpEvent,
)
from app.schemas import (
    PasswordChange,
    PersonalizationIn,
    PersonalizationOut,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserRead)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    """Get the authenticated learner's profile."""
    return current_user


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Update the authenticated learner's profile fields."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/personalization", response_model=PersonalizationOut)
def update_personalization(
    payload: PersonalizationIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PersonalizationOut:
    """Update learning preferences: interests, time commitment, pace.

    Everything optional; only present fields change. A pace reseeds the
    study plan exactly like onboarding does, and an explicit daily goal
    wins over the reseeded one when both are given. Clearing interests is
    allowed, and honestly flips onboarding status back to choose_interests
    the next time it is read.
    """
    if payload.interests is not None:
        current_user.interests = payload.interests

    plan = db.scalar(
        select(StudyPlan).where(StudyPlan.user_id == current_user.id)
    )
    if plan is None:
        plan = StudyPlan(user_id=current_user.id)
        db.add(plan)

    if payload.learning_pace is not None:
        pace = PACE_BY_KEY[payload.learning_pace]
        current_user.learning_pace = pace.key
        plan.daily_goal_minutes = pace.daily_goal_minutes
        plan.weekly_target_lessons = WEEKLY_TARGET_BY_PACE.get(
            pace.key, plan.weekly_target_lessons
        )
    if payload.daily_goal_minutes is not None:
        plan.daily_goal_minutes = payload.daily_goal_minutes

    db.commit()
    db.refresh(current_user)
    db.refresh(plan)
    return PersonalizationOut(
        interests=list(current_user.interests or []),
        learning_pace=current_user.learning_pace,
        daily_goal_minutes=plan.daily_goal_minutes,
        weekly_target_lessons=plan.weekly_target_lessons,
    )


@router.patch("/me/password")
def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Change the learner's password (requires the current one)."""
    user = db.get(User, current_user.id)
    if user is None or not verify_password(
        payload.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password changed"}


@router.delete("/me", status_code=status.HTTP_200_OK)
def delete_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete the learner's account and all of their data."""
    uid = current_user.id
    db.query(ConversationMessage).filter(ConversationMessage.user_id == uid).delete(
        synchronize_session=False
    )
    db.query(DirectMessage).filter(
        (DirectMessage.sender_id == uid) | (DirectMessage.recipient_id == uid)
    ).delete(synchronize_session=False)
    own_post_ids = select(CommunityPost.id).where(
        CommunityPost.author_user_id == uid
    )
    db.query(CommunityReply).filter(
        (CommunityReply.author_user_id == uid)
        | (CommunityReply.post_id.in_(own_post_ids))
    ).delete(synchronize_session=False)
    db.query(CommunityPost).filter(
        CommunityPost.author_user_id == uid
    ).delete(synchronize_session=False)
    from app.models.analytics import AnalyticsEvent

    # Anonymize (don't delete): course-level stats must survive erasure.
    db.query(AnalyticsEvent).filter(AnalyticsEvent.user_id == uid).update(
        {AnalyticsEvent.user_id: None}, synchronize_session=False
    )
    db.query(Payment).filter(Payment.user_id == uid).delete(
        synchronize_session=False
    )
    db.query(BuddyMatch).filter(
        (BuddyMatch.user_id == uid) | (BuddyMatch.buddy_user_id == uid)
    ).delete(synchronize_session=False)
    db.query(MissionStep).filter(
        MissionStep.mission_id.in_(
            select(Mission.id).where(Mission.user_id == uid)
        )
    ).delete(synchronize_session=False)
    for model, column in (
        (Mission, Mission.user_id),
        (StudyPlan, StudyPlan.user_id),
        (Badge, Badge.user_id),
        (XpEvent, XpEvent.user_id),
        (QuizResult, QuizResult.user_id),
        (LessonProgress, LessonProgress.user_id),
        (Enrollment, Enrollment.user_id),
        (PasswordResetToken, PasswordResetToken.user_id),
    ):
        db.query(model).filter(column == uid).delete(synchronize_session=False)

    # EmailLog is anonymized, not deleted, for the same reason as
    # AnalyticsEvent: the record of what was sent outlives the account. Its
    # user_id is nullable and drops to NULL when the user row goes.
    db.query(EmailLog).filter(EmailLog.user_id == uid).update(
        {EmailLog.user_id: None}, synchronize_session=False
    )

    db.delete(db.get(User, uid))
    db.commit()
    return {"message": "Account and all associated data deleted"}