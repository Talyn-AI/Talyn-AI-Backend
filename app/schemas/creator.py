"""Creator profile schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class CreatorProfileIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    bio: str = Field(default="", max_length=1000)
    image_key: str | None = Field(default=None, max_length=500)


class CreatorProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    user_id: int
    display_name: str
    bio: str = ""
    image_key: str | None = None


class ActivityEntry(BaseModel):
    event: str
    course_id: int | None = None
    lesson_id: int | None = None
    created_at: datetime


class CreatorDashboard(BaseModel):
    total_courses: int
    published_courses: int
    draft_courses: int
    total_learners: int
    total_revenue_naira: int = 0
    recent_activity: list[ActivityEntry] = []
