"""Advanced analytics schemas (aggregates only — no per-learner detail)."""
from pydantic import BaseModel, Field


class LessonFunnel(BaseModel):
    lesson_id: int
    title: str
    order: int
    views: int
    starts: int
    completions: int
    completion_rate: float


class QuizTopicStats(BaseModel):
    topic: str
    attempts: int
    avg_score: float
    best_score: float


class CourseAnalytics(BaseModel):
    course_id: int
    lessons: list[LessonFunnel]
    quiz_topics: list[QuizTopicStats]
    total_views: int
    total_enrollments: int
    total_completions: int


class DailyPoint(BaseModel):
    date: str
    enrollments: int
    purchases: int
    revenue_naira: int
    completions: int


class OverviewResponse(BaseModel):
    days: int
    total_enrollments: int
    total_purchases: int
    total_revenue_naira: int
    total_completions: int
    active_learners: int
    daily: list[DailyPoint]


class OverviewQuery(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
