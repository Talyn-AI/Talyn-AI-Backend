"""Waitlist schemas."""
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.onboarding import INTEREST_KEYS

# The form offers a two-way choice, so this is a two-way choice here too.
WaitlistRole = Literal["learner", "creator"]

# The page says "Select 1-2 interests". Kept a cap without a floor: the copy
# lives in the frontend and can drift, and a 422 on submit loses the signup
# over a field the visitor did not think was required.
MAX_INTERESTS = 2


class WaitlistSignupIn(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=120)
    role: WaitlistRole
    interests: list[str] = Field(default_factory=list)
    course: str = Field(default="", max_length=200)

    @field_validator("interests")
    @classmethod
    def _known_interests(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in INTEREST_KEYS]
        if unknown:
            raise ValueError(f"Unknown interests: {', '.join(unknown[:5])}")
        # Preserve order, drop duplicates: a checkbox group hands back whatever
        # the visitor clicked, in DOM order, and a double-click should not
        # store the same interest twice.
        seen: set[str] = set()
        deduped = [v for v in values if not (v in seen or seen.add(v))]
        # Counted after collapsing, so ticking the same box twice is not a
        # validation error — only genuinely distinct choices are capped.
        if len(deduped) > MAX_INTERESTS:
            raise ValueError(f"Choose at most {MAX_INTERESTS} interests")
        return deduped


class WaitlistJoined(BaseModel):
    message: str
    email: str
    # Position at the moment of joining, from the serial id. Does not move as
    # others sign up, which is the point — it is an acknowledgement, not a
    # live queue position.
    position: int
    # False when an existing signup was updated rather than created.
    created: bool
