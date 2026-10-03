"""Course and lesson schemas."""
from typing import Literal

from pydantic import BaseModel, Field

Difficulty = Literal["beginner", "intermediate", "advanced"]
CourseType = Literal["free", "paid"]
CourseStatus = Literal["draft", "published", "archived"]


class LessonCreate(BaseModel):
    order: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    topic: str = Field(..., min_length=1, max_length=255)
    lesson_type: str = Field(default="lesson", max_length=30)
    estimated_minutes: int = Field(default=15, ge=1)
    content: str = Field(default="", max_length=10000)
    is_published: bool = True


class CourseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    difficulty_level: Difficulty = "beginner"
    category: str = Field(default="", max_length=120)
    outcomes: list[str] = Field(default_factory=list, max_length=20)
    target_audience: str = Field(default="", max_length=500)
    requirements: str = Field(default="", max_length=1000)
    thumbnail_key: str | None = Field(default=None, max_length=500)
    course_type: CourseType = "free"
    price_naira: int = Field(default=0, ge=0)
    lessons: list[LessonCreate] = Field(default_factory=list)


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    difficulty_level: Difficulty | None = None
    category: str | None = Field(default=None, max_length=120)
    outcomes: list[str] | None = Field(default=None, max_length=20)
    target_audience: str | None = Field(default=None, max_length=500)
    requirements: str | None = Field(default=None, max_length=1000)
    course_type: CourseType | None = None
    price_naira: int | None = Field(default=None, ge=0)
    thumbnail_key: str | None = Field(default=None, max_length=500)


class LessonRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    order: int
    module_id: int | None = None
    title: str
    topic: str
    description: str = ""
    lesson_type: str
    estimated_minutes: int
    # None when the caller may see structure but not paid content.
    content: str | None = None
    is_published: bool


class ModuleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class ModuleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)


class ModuleRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    order: int
    lessons: list[LessonRead] = []


class LessonCreateIn(BaseModel):
    """Create a lesson inside a course; order defaults to end of its module."""

    module_id: int | None = None
    order: int | None = Field(default=None, ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    topic: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    lesson_type: str = Field(default="lesson", max_length=30)
    estimated_minutes: int = Field(default=15, ge=1)
    content: str = Field(default="", max_length=10000)
    is_published: bool = True


class LessonUpdate(BaseModel):
    module_id: int | None = None
    order: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    topic: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    lesson_type: str | None = Field(default=None, max_length=30)
    estimated_minutes: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, max_length=10000)
    is_published: bool | None = None


class ReorderIn(BaseModel):
    ordered_ids: list[int] = Field(..., min_length=1)


class PublishCheck(BaseModel):
    publishable: bool
    errors: list[str] = []


class CourseRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    difficulty_level: str
    creator_user_id: int | None = None
    category: str = ""
    outcomes: list[str] = []
    target_audience: str = ""
    requirements: str = ""
    thumbnail_key: str | None = None
    course_type: str = "free"
    price_naira: int = 0
    status: str = "draft"
    lessons: list[LessonRead] = []
    modules: list[ModuleRead] = []


class CourseListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    difficulty_level: str
    category: str = ""
    course_type: str = "free"
    price_naira: int = 0
    status: str = "draft"
    lesson_count: int = 0
