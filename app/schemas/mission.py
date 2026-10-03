"""Mission, mission template, and buddy-match schemas."""
from pydantic import BaseModel, Field


# ── Mission catalogue (creator-authored) ──────────────────────────────────────


class MissionStepCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=1000)
    order: int = Field(..., ge=1)


class MissionTemplateCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=1000)
    purpose: str = Field(default="", max_length=1000)
    reward_xp: int = Field(default=100, ge=0, le=1000)
    badge: str | None = Field(default=None, max_length=120)
    steps: list[MissionStepCreate] = Field(default_factory=list)


class MissionTemplateUpdate(BaseModel):
    """Every field optional: a creator may edit one step's wording without
    restating the whole mission."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    purpose: str | None = Field(default=None, max_length=1000)
    reward_xp: int | None = Field(default=None, ge=0, le=1000)
    badge: str | None = Field(default=None, max_length=120)
    published: bool | None = None
    steps: list[MissionStepCreate] | None = None


class MissionTemplateStepRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    order: int


class MissionTemplateRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    purpose: str
    reward_xp: int
    badge: str | None
    published: bool
    steps: list[MissionTemplateStepRead] = []


class MissionCatalogRead(MissionTemplateRead):
    """A catalogue entry as a learner sees it.

    `adopted` and `adopted_mission_id` let the UI say "You're on this one"
    without the client cross-referencing two lists.
    """

    creator_name: str = ""
    adopted: bool = False
    adopted_mission_id: int | None = None


# ── Missions (a learner's adopted instances) ──────────────────────────────────


class MissionAdopt(BaseModel):
    """Adopt a catalogue mission. The learner's only way to start one."""

    template_id: int = Field(..., ge=1)


class MissionStepRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    order: int
    completed: bool


class MissionRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str
    description: str
    purpose: str
    reward_xp: int
    badge: str | None
    status: str
    template_id: int | None = None
    steps: list[MissionStepRead] = []


class MissionStatusUpdate(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


MISSION_STATUSES = {"not_started", "in_progress", "completed"}


# ── Buddy matches ────────────────────────────────────────────────────────────


class BuddyMatchCreate(BaseModel):
    buddy_user_id: int = Field(..., ge=1)
    match_score: int = Field(default=0, ge=0, le=100)


class BuddyMatchRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    buddy_user_id: int
    match_score: int
    status: str


class BuddyMatchStatusUpdate(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)


BUDDY_MATCH_STATUSES = {"pending", "accepted", "declined"}