"""Progress, quiz, XP, and learner-context schemas."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.learning_path import PathRead


class QuizSubmit(BaseModel):
    course_id: int | None = None
    topic: str = Field(..., min_length=1, max_length=255)
    score_percent: float = Field(..., ge=0, le=100)
    attempts: int = Field(default=1, ge=1)
    # Set this when the attempt came from a lesson in the course (lesson_type
    # "quiz"). Without it the score is recorded but clears nothing, which is
    # the right behaviour for a standalone practice quiz.
    lesson_id: int | None = None


class CourseQaIn(BaseModel):
    course_id: int
    lesson_id: int | None = None
    question: str = Field(..., min_length=1, max_length=2000)


class CourseQaOut(BaseModel):
    answer: str
    # Lesson titles the answer was drawn from, reported by the backend —
    # never by the model, so a cited lesson always exists.
    sources: list[str] = []


class LessonCompleteRequest(BaseModel):
    """Body for marking a lesson complete. Empty for now; exists so the
    contract can grow (e.g. time spent, difficulty rating)."""

    pass


class NextStepOut(BaseModel):
    """What the client should show after a lesson is done.

    `"quiz"` also means "required": the gate refuses the lesson after it
    until this quiz is passed, so `type` and `quiz_required` always agree.
    """

    type: Literal["quiz", "lesson", "course_complete"]
    lesson_id: int | None = None
    title: str | None = None
    topic: str | None = None
    quiz_required: bool = False


class LessonCompleteOut(BaseModel):
    completed: bool
    lesson_id: int
    xp_awarded: int
    already_completed: bool
    next: NextStepOut


class LessonStartOut(BaseModel):
    started: bool
    lesson_id: int
    first_time: bool


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


class CheckInIn(BaseModel):
    mood: str | None = Field(default=None, max_length=30)
    note: str = Field(default="", max_length=500)


class CheckInRead(BaseModel):
    date: date
    mood: str | None = None
    note: str = ""
    xp_awarded: int = 0


class ActivityEntry(BaseModel):
    activity: str
    amount: int
    note: str = ""
    at: datetime


class QuizSummary(BaseModel):
    quizzes_taken: int = 0
    average_score: float = 0.0
    topics_attempted: int = 0


class DashboardOut(BaseModel):
    """Everything the learner Dashboard and Progress pages need in one call:
    enrollments with progress ("my learning"), saved learning paths, XP and
    streak, quiz summary, recent activity, recent check-ins, the study plan,
    and where onboarding stands."""

    enrollments: list[EnrollmentProgress] = []
    learning_paths: list[PathRead] = []
    xp_total: int = 0
    xp_this_week: int = 0
    level: int = 1
    level_title: str = ""
    streak_days: int = 0
    quiz: QuizSummary = QuizSummary()
    recent_activity: list[ActivityEntry] = []
    checkins_last_7_days: list[date] = []
    checked_in_today: bool = False
    study_plan: StudyPlanRead | None = None
    onboarding_next_step: str = "done"


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