"""Progress, quiz, XP, and learner-context schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class QuizSubmit(BaseModel):
    course_id: int | None = None
    topic: str = Field(..., min_length=1, max_length=255)
    score_percent: float = Field(..., ge=0, le=100)
    attempts: int = Field(default=1, ge=1)


class LessonCompleteRequest(BaseModel):
    """Body for marking a lesson complete. Empty for now; exists so the
    contract can grow (e.g. time spent, difficulty rating)."""

    pass


class XpEntry(BaseModel):
    activity: str
    amount: int
    note: str | None = None
    earned_date: datetime


class XpSummary(BaseModel):
    xp_total: int
    xp_this_week: int
    level: int
    level_title: str
    xp_this_level: int
    level_up_xp: int
    next_level_title: str
    breakdown: list[XpEntry]


class QuizPerformanceOut(BaseModel):
    topic: str
    score_percent: float
    attempts: int
    last_attempt_date: str


class StudyPlanIn(BaseModel):
    """Create/replace the learner's study plan (one per learner)."""

    daily_goal_minutes: int = Field(default=30, ge=5, le=480)
    weekly_target_lessons: int = Field(default=3, ge=1, le=50)
    focus_topics: list[str] = Field(default_factory=list, max_length=20)
    deadline: str | None = Field(default=None, max_length=50)


class StudyPlanRead(BaseModel):
    id: int
    daily_goal_minutes: int
    weekly_target_lessons: int
    focus_topics: list[str] = []
    deadline: str | None = None


class EnrollmentProgress(BaseModel):
    course_id: int
    title: str
    difficulty_level: str
    lessons_total: int
    lessons_completed: int
    completion_percent: float
    completed: bool
    enrolled_at: datetime


class BadgeRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    badge_id: str
    name: str
    description: str
    icon: str | None = None
    earned_date: datetime


#: Activities awardable through the generic endpoint. lesson/quiz/mission
#: have dedicated endpoints (completion, quiz submit, mission complete) and
#: are rejected there to keep one source of truth per XP source.
MANUAL_XP_ACTIVITIES = {"revision", "challenge", "live_participation", "streak"}


class XpAwardIn(BaseModel):
    activity: str = Field(..., min_length=1, max_length=30)
    note: str | None = Field(default=None, max_length=500)


class ConversationIn(BaseModel):
    role: str = Field(..., min_length=1, max_length=20)
    content: str = Field(..., min_length=1, max_length=4000)


class ConversationRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    role: str
    content: str
    created_at: datetime


CONVERSATION_ROLES = {"user", "assistant"}


class LearnerContextOut(BaseModel):
    """Mirrors the LearnerContext contract used by the AI coach."""

    learner_id: str
    learner_name: str
    current_course: str = ""
    current_lesson: str = ""
    current_topic: str = ""
    interests: list[str] = []
    difficulty_level: str = "beginner"
    goals: str = ""
    xp_total: int = 0
    xp_this_week: int = 0
    streak_days: int = 0
    lessons_completed: int = 0
    lessons_total: int = 0
    completion_percent: float = 0.0
    quiz_performance: list[QuizPerformanceOut] = []
    level: dict | None = None
    badges: list[dict] = []
    xp_breakdown: list[XpEntry] = []
    missions: dict | None = None
    study_plan: dict | None = None
    conversation_history: list[dict] = []