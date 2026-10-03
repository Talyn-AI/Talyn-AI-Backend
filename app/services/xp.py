"""XP ledger helpers: awards, level math, weekly totals, streaks."""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User, XpEvent

# One canonical list of XP awards (mirrors XpActivityType in the AI schemas).
XP_AMOUNTS = {
    "lesson": 25,    # completing a lesson
    "quiz": 10,      # base quiz attempt; score bonus computed separately
    "challenge": 40,
    "live_participation": 30,
    "streak": 20,    # bonus for hitting a streak milestone
    "mission": 100,
    "revision": 15,
}

# Level titles shown in LearnerLevel.
LEVEL_TITLES = [
    "Curious Explorer",
    "Rising Star",
    "Knowledge Seeker",
    "Skill Builder",
    "Path Master",
    "Talyn Champion",
]

LEVEL_CURVE_XP = 100  # XP to advance one level


def award_xp(
    db: Session,
    user: User,
    activity: str,
    note: str | None = None,
    amount: int | None = None,
) -> XpEvent:
    """Record an XP event.

    Uses the canonical amount for the activity unless `amount` is given
    (e.g. a mission's own reward_xp).
    """
    xp = XpEvent(
        user_id=user.id,
        activity=activity,
        amount=amount if amount is not None else XP_AMOUNTS.get(activity, 10),
        note=note,
    )
    db.add(xp)
    return xp


def award_quiz_xp(
    db: Session, user: User, score_percent: float, note: str | None = None
) -> XpEvent:
    """Quiz XP = base + score bonus (1 XP per 10 percentage points)."""
    amount = XP_AMOUNTS["quiz"] + int(score_percent // 10)
    xp = XpEvent(user_id=user.id, activity="quiz", amount=amount, note=note)
    db.add(xp)
    return xp


def xp_total(db: Session, user: User) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(XpEvent.amount), 0)).where(
            XpEvent.user_id == user.id
        )
    )
    return int(total or 0)


def xp_this_week(db: Session, user: User) -> int:
    """XP earned since Monday 00:00 (UTC)."""
    # UTC to match the range below; date.today() would pick a different week
    # for part of the day on a non-UTC host.
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday, time.min, tzinfo=timezone.utc)
    total = db.scalar(
        select(func.coalesce(func.sum(XpEvent.amount), 0)).where(
            XpEvent.user_id == user.id, XpEvent.earned_date >= start
        )
    )
    return int(total or 0)


def _title_for_level(i: int) -> str:
    return LEVEL_TITLES[i] if 0 <= i < len(LEVEL_TITLES) else f"Level {i + 1}"


def level_info(xp: int) -> dict:
    """Return {level, title, xp_this_level, level_up_xp, next_level_title}."""
    level = xp // LEVEL_CURVE_XP + 1
    xp_this_level = xp % LEVEL_CURVE_XP
    return {
        "level": level,
        "title": _title_for_level(level - 1),
        "xp_this_level": xp_this_level,
        "level_up_xp": LEVEL_CURVE_XP - xp_this_level,
        "next_level_title": _title_for_level(level),
    }


def streak_days(db: Session, user: User) -> int:
    """Current streak: consecutive days (ending today or yesterday) with XP earned."""
    dates = db.scalars(
        select(func.date(XpEvent.earned_date))
        .where(XpEvent.user_id == user.id)
        .distinct()
    ).all()

    # UTC: earned_date is recorded in UTC, so comparing against the server's
    # local date would break a streak for part of every day on a non-UTC host.
    today = datetime.now(timezone.utc).date()
    day_set = {d for d in dates if isinstance(d, date)}
    if today not in day_set:
        # Streak stays alive if they studied yesterday.
        today = today - timedelta(days=1)
    count = 0
    cursor = today
    while cursor in day_set:
        count += 1
        cursor = cursor - timedelta(days=1)
    return count