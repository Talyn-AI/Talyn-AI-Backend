"""User + auth schemas."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Difficulty = Literal["beginner", "intermediate", "advanced"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    learner_name: str = Field(..., min_length=1, max_length=120)
    difficulty_level: Difficulty = "beginner"
    is_creator: bool = False
    goals: str = Field(default="", max_length=500)
    interests: list[str] = Field(default_factory=list)
    current_course: str = Field(default="")
    current_lesson: str = Field(default="")
    current_topic: str = Field(default="")


class UserUpdate(BaseModel):
    learner_name: str | None = None
    difficulty_level: Difficulty | None = None
    goals: str | None = None
    interests: list[str] | None = None
    current_course: str | None = None
    current_lesson: str | None = None
    current_topic: str | None = None


class UserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    email: str
    learner_name: str
    difficulty_level: str
    goals: str
    interests: list[str]
    current_course: str
    current_lesson: str
    current_topic: str
    is_admin: bool = False
    is_creator: bool = False
    created_at: datetime


class Token(BaseModel):
    access_token: str
    refresh_token: str = ""
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Set a new password with a reset code. The code is a string, not a
    number: int("042013") is 42013, and a credential that changes when
    parsed is a support ticket."""

    email: EmailStr
    code: str = Field(..., pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8)


class GoogleAuthIn(BaseModel):
    id_token: str = Field(..., min_length=1)


class EmailAvailability(BaseModel):
    email: EmailStr


class TokenPayload(BaseModel):
    sub: str  # user id as string
    exp: datetime