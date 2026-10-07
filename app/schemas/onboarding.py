"""Email verification and onboarding schemas."""
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.onboarding import INTEREST_KEYS, PACE_KEYS


class VerificationRequestIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class OtpVerifyIn(BaseModel):
    """The single verification call for both flows.

    `purpose` selects the flow; `new_password` is required for resets and
    forbidden for signups, so one malformed call cannot set a password by
    accident. The code is a string, not a number: int("042013") is 42013.
    """

    email: EmailStr
    code: str = Field(..., pattern=r"^\d{6}$")
    purpose: Literal["signup", "reset"]
    new_password: str | None = Field(default=None, min_length=8)

    @model_validator(mode="after")
    def _password_matches_purpose(self):
        if self.purpose == "reset" and not self.new_password:
            raise ValueError("A password reset needs a new_password")
        if self.purpose == "signup" and self.new_password is not None:
            raise ValueError("Signup verification takes no new_password")
        return self


class PaceOptionOut(BaseModel):
    key: str
    label: str
    min_minutes: int
    max_minutes: int | None
    daily_goal_minutes: int
    description: str


class InterestOptionOut(BaseModel):
    key: str
    label: str


class OnboardingOptionsOut(BaseModel):
    """Everything a picker needs, so the frontend never hardcodes a list."""

    paces: list[PaceOptionOut]
    interests: list[InterestOptionOut]
    default_pace: str


class OnboardingStatusOut(BaseModel):
    email_verified: bool
    onboarding_completed: bool
    learning_pace: str | None
    interests: list[str]
    # The one thing the client needs to route on: which screen comes next, if
    # any. Deriving that in two places is how clients disagree about it.
    next_step: str


class OnboardingCompleteIn(BaseModel):
    learning_pace: str = Field(..., min_length=3, max_length=20)
    interests: list[str] = Field(..., min_length=1, max_length=12)

    @field_validator("learning_pace")
    @classmethod
    def _known_pace(cls, v: str) -> str:
        if v not in PACE_KEYS:
            raise ValueError(
                f"learning_pace must be one of: {', '.join(PACE_KEYS)}"
            )
        return v

    @field_validator("interests")
    @classmethod
    def _known_interests(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in INTEREST_KEYS]
        if unknown:
            raise ValueError(f"Unknown interests: {', '.join(unknown[:5])}")
        # Preserve order, drop duplicates: a picker can easily hand back the
        # same key twice and storing it twice would skew recommendations.
        seen: set[str] = set()
        return [v for v in values if not (v in seen or seen.add(v))]


class PersonalizationIn(BaseModel):
    """Update learning preferences after onboarding: interests, time
    commitment, pace. Everything optional; only present fields change."""

    interests: list[str] | None = Field(default=None, max_length=12)
    daily_goal_minutes: int | None = Field(default=None, ge=5, le=480)
    learning_pace: str | None = Field(default=None, min_length=3,
                                      max_length=20)

    @field_validator("learning_pace")
    @classmethod
    def _known_pace(cls, v: str | None) -> str | None:
        if v is not None and v not in PACE_KEYS:
            raise ValueError(
                f"learning_pace must be one of: {', '.join(PACE_KEYS)}"
            )
        return v

    @field_validator("interests")
    @classmethod
    def _known_interests(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        unknown = [v for v in values if v not in INTEREST_KEYS]
        if unknown:
            raise ValueError(f"Unknown interests: {', '.join(unknown[:5])}")
        seen: set[str] = set()
        return [v for v in values if not (v in seen or seen.add(v))]


class PersonalizationOut(BaseModel):
    interests: list[str]
    learning_pace: str | None
    daily_goal_minutes: int
    weekly_target_lessons: int