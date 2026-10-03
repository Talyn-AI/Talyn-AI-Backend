"""Email verification and onboarding schemas."""
from pydantic import BaseModel, Field, field_validator

from app.core.onboarding import INTEREST_KEYS, PACE_KEYS


class VerificationRequestIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class VerificationConfirmIn(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)


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