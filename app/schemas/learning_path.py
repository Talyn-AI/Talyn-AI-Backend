"""Learning path schemas."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Long enough for a real curriculum, short enough that a runaway client
# cannot turn one path into a denial of rendering.
MAX_PATH_COURSES = 20

PathStepStatus = Literal["completed", "in_progress", "not_started"]


class PathCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    # Ordered course ids. Deduped preserving order; every id must be a
    # published course, because a learner cannot open anything else.
    course_ids: list[int] = Field(..., min_length=1, max_length=MAX_PATH_COURSES)


class PathUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    # Replaces the whole step list when present: partial reorder patches
    # invite half-applied states, and the list is short.
    course_ids: list[int] | None = Field(
        default=None, min_length=1, max_length=MAX_PATH_COURSES
    )


class PathStepRead(BaseModel):
    position: int
    course_id: int
    title: str
    difficulty_level: str = ""
    lessons_total: int = 0
    # Derived from the learner's enrollment, not stored: completed means the
    # enrollment is complete, in_progress means enrolled but not done, and
    # not_started means not enrolled at all.
    status: PathStepStatus


class PathRead(BaseModel):
    id: int
    title: str
    description: str = ""
    steps: list[PathStepRead] = []
    courses_total: int = 0
    courses_completed: int = 0
    completion_percent: float = 0.0
    created_at: datetime | None = None
    updated_at: datetime | None = None
