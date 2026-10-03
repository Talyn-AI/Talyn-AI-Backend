"""Social router: community posts, direct messages, live sessions."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import (
    can_manage_course,
    get_current_user,
    optional_user,
    require_creator,
)
from app.database import get_db
from app.models import (
    CommunityPost,
    CommunityReply,
    Course,
    DirectMessage,
    Enrollment,
    LiveSession,
    User,
)
from app.models.analytics import (
    COMMUNITY_POST_CREATED,
    LIVE_SESSION_ENDED,
    LIVE_SESSION_SCHEDULED,
    LIVE_SESSION_STARTED,
)
from app.models.course import STATUS_PUBLISHED
from app.models.social import (
    LIVE_CANCELLED,
    LIVE_ENDED,
    LIVE_LIVE,
    LIVE_SCHEDULED,
)
from app.schemas.social import (
    LiveIn,
    LiveRead,
    MessageIn,
    MessageRead,
    PostDetail,
    PostIn,
    PostRead,
    ReplyIn,
    ReplyRead,
    ThreadRead,
)
from app.services.analytics import track

community_router = APIRouter(prefix="/community", tags=["Community"])
messages_router = APIRouter(prefix="/messages", tags=["Messages"])
live_router = APIRouter(prefix="/courses", tags=["Live"])
live_detail_router = APIRouter(prefix="/live", tags=["Live"])


def _display_names(db: Session, user_ids: set[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    return {
        u.id: u.learner_name
        for u in db.scalars(
            select(User).where(User.id.in_(user_ids))
        ).all()
    }


# ── Community ─────────────────────────────────────────────────────────────────

@community_router.post("/posts", response_model=PostRead,
                        status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostRead:
    """Publish a community post."""
    post = CommunityPost(
        author_user_id=current_user.id, title=payload.title, body=payload.body
    )
    db.add(post)
    db.flush()
    track(db, COMMUNITY_POST_CREATED, current_user)
    db.commit()
    db.refresh(post)
    return PostRead(
        id=post.id, author_user_id=post.author_user_id,
        author_name=current_user.learner_name, title=post.title,
        body=post.body, created_at=post.created_at, reply_count=0,
    )


@community_router.get("/posts", response_model=list[PostRead])
def list_posts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PostRead]:
    """Newest community posts with reply counts (public)."""
    rows = db.execute(
        select(CommunityPost, func.count(CommunityReply.id).label("replies"))
        .outerjoin(CommunityReply,
                   CommunityReply.post_id == CommunityPost.id)
        .group_by(CommunityPost.id)
        .order_by(CommunityPost.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    names = _display_names(db, {p.author_user_id for p, _ in rows})
    return [
        PostRead(
            id=p.id, author_user_id=p.author_user_id,
            author_name=names.get(p.author_user_id, "?"), title=p.title,
            body=p.body, created_at=p.created_at, reply_count=int(count),
        )
        for p, count in rows
    ]


@community_router.get("/posts/{post_id}", response_model=PostDetail)
def get_post(post_id: int, db: Session = Depends(get_db)) -> PostDetail:
    """One post with its replies (public)."""
    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    replies = db.scalars(
        select(CommunityReply)
        .where(CommunityReply.post_id == post.id)
        .order_by(CommunityReply.id)
    ).all()
    names = _display_names(
        db, {post.author_user_id} | {r.author_user_id for r in replies}
    )
    return PostDetail(
        id=post.id, author_user_id=post.author_user_id,
        author_name=names.get(post.author_user_id, "?"), title=post.title,
        body=post.body, created_at=post.created_at, reply_count=len(replies),
        replies=[
            ReplyRead(
                id=r.id, author_user_id=r.author_user_id,
                author_name=names.get(r.author_user_id, "?"), body=r.body,
                created_at=r.created_at,
            )
            for r in replies
        ],
    )


@community_router.post("/posts/{post_id}/replies", response_model=ReplyRead,
                        status_code=status.HTTP_201_CREATED)
def reply_to_post(
    post_id: int,
    payload: ReplyIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReplyRead:
    """Reply to a post."""
    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    reply = CommunityReply(
        post_id=post.id, author_user_id=current_user.id, body=payload.body
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return ReplyRead(
        id=reply.id, author_user_id=reply.author_user_id,
        author_name=current_user.learner_name, body=reply.body,
        created_at=reply.created_at,
    )


@community_router.delete("/posts/{post_id}")
def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete own post (admin can delete any); replies cascade."""
    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.author_user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not your post")
    db.delete(post)
    db.commit()
    return {"message": f"Post {post_id} deleted"}


@community_router.delete("/posts/{post_id}/replies/{reply_id}")
def delete_reply(
    post_id: int,
    reply_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete own reply (admin can delete any)."""
    reply = db.scalar(
        select(CommunityReply).where(
            CommunityReply.id == reply_id,
            CommunityReply.post_id == post_id,
        )
    )
    if reply is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    if reply.author_user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not your reply")
    db.delete(reply)
    db.commit()
    return {"message": f"Reply {reply_id} deleted"}


# ── Messages ──────────────────────────────────────────────────────────────────

@messages_router.post("", response_model=MessageRead,
                      status_code=status.HTTP_201_CREATED)
def send_message(
    payload: MessageIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DirectMessage:
    """Send a direct message to another learner."""
    if payload.recipient_id == current_user.id:
        raise HTTPException(status_code=422, detail="Cannot message yourself")
    if db.get(User, payload.recipient_id) is None:
        raise HTTPException(status_code=404, detail="Recipient not found")
    message = DirectMessage(
        sender_id=current_user.id, recipient_id=payload.recipient_id,
        body=payload.body,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@messages_router.get("/threads", response_model=list[ThreadRead])
def list_threads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ThreadRead]:
    """Conversation threads, most recent first, with unread counts."""
    rows = db.scalars(
        select(DirectMessage)
        .where(
            or_(DirectMessage.sender_id == current_user.id,
                DirectMessage.recipient_id == current_user.id)
        )
        .order_by(DirectMessage.id.desc())
        .limit(500)
    ).all()
    threads: dict[int, dict] = {}
    for m in rows:
        other = m.recipient_id if m.sender_id == current_user.id else m.sender_id
        entry = threads.setdefault(other, {"last": m, "unread": 0})
        if m.recipient_id == current_user.id and m.read_at is None:
            entry["unread"] += 1
    names = _display_names(db, set(threads))
    ordered = sorted(threads.items(), key=lambda kv: kv[1]["last"].id, reverse=True)
    return [
        ThreadRead(
            other_user_id=other, other_name=names.get(other, "?"),
            last_body=entry["last"].body,
            last_at=entry["last"].created_at, unread_count=entry["unread"],
        )
        for other, entry in ordered
    ]


@messages_router.get("/with/{user_id}", response_model=list[MessageRead])
def read_thread(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DirectMessage]:
    """Message history with one learner, chronological."""
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return list(
        db.scalars(
            select(DirectMessage)
            .where(
                or_(
                    (DirectMessage.sender_id == current_user.id)
                    & (DirectMessage.recipient_id == user_id),
                    (DirectMessage.sender_id == user_id)
                    & (DirectMessage.recipient_id == current_user.id),
                )
            )
            .order_by(DirectMessage.id.asc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


@messages_router.post("/with/{user_id}/read")
def mark_thread_read(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Mark all messages from a learner as read."""
    count = (
        db.query(DirectMessage)
        .filter(
            DirectMessage.sender_id == user_id,
            DirectMessage.recipient_id == current_user.id,
            DirectMessage.read_at.is_(None),
        )
        .update(
            {DirectMessage.read_at: datetime.now(timezone.utc)},
            synchronize_session=False,
        )
    )
    db.commit()
    return {"message": f"Marked {count} messages as read"}


@messages_router.delete("/{message_id}")
def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete own sent message (admin can delete any)."""
    message = db.get(DirectMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not your message")
    db.delete(message)
    db.commit()
    return {"message": f"Message {message_id} deleted"}


# ── Live sessions ─────────────────────────────────────────────────────────────

@live_router.post("/{course_id}/live", response_model=LiveRead,
                  status_code=status.HTTP_201_CREATED)
def schedule_live(
    course_id: int,
    payload: LiveIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> LiveSession:
    """Schedule a live class on a course. Owner or admin."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, creator):
        raise HTTPException(status_code=403, detail="Not your course")
    if payload.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=422, detail="scheduled_at must be in the future"
        )
    session = LiveSession(
        course_id=course.id,
        title=payload.title,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        meeting_url=payload.meeting_url,
        status=LIVE_SCHEDULED,
    )
    db.add(session)
    db.flush()
    track(db, LIVE_SESSION_SCHEDULED, creator, course_id=course.id)
    db.commit()
    db.refresh(session)
    return _live_read(session)


@live_router.get("/{course_id}/live", response_model=list[LiveRead])
def list_live(
    course_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> list[LiveRead]:
    """Upcoming/live classes. Draft courses visible to owner/admin only."""
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not can_manage_course(course, viewer):
            raise HTTPException(status_code=403, detail="Not your course")
    rows = db.scalars(
        select(LiveSession)
        .where(LiveSession.course_id == course.id)
        .order_by(LiveSession.scheduled_at)
    ).all()
    return [_live_read(r, _joinable(db, viewer, course)) for r in rows]


def _joinable(db: Session, viewer: User | None, course: Course) -> bool:
    """Meeting links go to enrolled learners, owners, and admins."""
    if viewer is None:
        return False
    if can_manage_course(course, viewer):
        return True
    return (
        db.scalar(
            select(Enrollment).where(
                Enrollment.user_id == viewer.id,
                Enrollment.course_id == course.id,
            )
        )
        is not None
    )


def _live_read(session: LiveSession, show_link: bool = True) -> LiveRead:
    return LiveRead(
        id=session.id,
        course_id=session.course_id,
        title=session.title,
        scheduled_at=session.scheduled_at,
        duration_minutes=session.duration_minutes,
        meeting_url=session.meeting_url if show_link else None,
        status=session.status,
        is_recorded=session.is_recorded,
    )


@live_detail_router.get("/{session_id}", response_model=LiveRead)
def get_live(
    session_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> LiveRead:
    """Live class detail; the join link is hidden unless entitled."""
    session = db.get(LiveSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Live session not found")
    course = db.get(Course, session.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not can_manage_course(course, viewer):
            raise HTTPException(status_code=403, detail="Not your course")
    return _live_read(session, _joinable(db, viewer, course))


def _transition(db: Session, user: User, session_id: int, to_status: str,
                allowed_from: set[str], event: str) -> LiveRead:
    session = db.get(LiveSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Live session not found")
    course = db.get(Course, session.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_manage_course(course, user):
        raise HTTPException(status_code=403, detail="Not your course")
    if session.status not in allowed_from:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move from '{session.status}' to '{to_status}'",
        )
    session.status = to_status
    track(db, event, user, course_id=course.id)
    db.commit()
    db.refresh(session)
    return _live_read(session)


@live_detail_router.post("/{session_id}/start", response_model=LiveRead)
def start_live(
    session_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> LiveRead:
    """Go live (scheduled -> live)."""
    return _transition(db, creator, session_id, LIVE_LIVE, {LIVE_SCHEDULED},
                       LIVE_SESSION_STARTED)


@live_detail_router.post("/{session_id}/end", response_model=LiveRead)
def end_live(
    session_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> LiveRead:
    """End the class (live -> ended)."""
    return _transition(db, creator, session_id, LIVE_ENDED, {LIVE_LIVE},
                       LIVE_SESSION_ENDED)


@live_detail_router.post("/{session_id}/cancel", response_model=LiveRead)
def cancel_live(
    session_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> LiveRead:
    """Cancel a scheduled class."""
    return _transition(db, creator, session_id, LIVE_CANCELLED,
                       {LIVE_SCHEDULED}, LIVE_SESSION_ENDED)
