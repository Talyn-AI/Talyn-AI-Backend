"""Social schemas: community, messaging, live sessions."""
from datetime import datetime

from pydantic import BaseModel, Field


# ── Community ─────────────────────────────────────────────────────────────────

class PostIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1, max_length=5000)


class ReplyIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class ReplyRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    author_user_id: int
    author_name: str = ""
    body: str
    created_at: datetime


class PostRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    author_user_id: int
    author_name: str = ""
    title: str
    body: str
    created_at: datetime
    reply_count: int = 0


class PostDetail(PostRead):
    replies: list[ReplyRead] = []


# ── Messaging ─────────────────────────────────────────────────────────────────

class MessageIn(BaseModel):
    recipient_id: int = Field(..., ge=1)
    body: str = Field(..., min_length=1, max_length=2000)


class MessageRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sender_id: int
    recipient_id: int
    body: str
    read_at: datetime | None = None
    created_at: datetime


class ThreadRead(BaseModel):
    other_user_id: int
    other_name: str
    last_body: str
    last_at: datetime
    unread_count: int


# ── Live sessions ─────────────────────────────────────────────────────────────

class LiveIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=5, le=480)
    meeting_url: str = Field(default="", max_length=2000)


class LiveRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    course_id: int
    title: str
    scheduled_at: datetime
    duration_minutes: int
    meeting_url: str | None = None
    status: str
    is_recorded: bool = False
