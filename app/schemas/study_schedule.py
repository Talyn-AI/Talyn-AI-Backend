"""Monetization schemas: analysis previews, path checkout, stored schedules."""
from datetime import datetime

from pydantic import BaseModel


class AnalysisRead(BaseModel):
    """The free "here's what we found" preview."""

    topics: list[str] = []
    objectives: list[str] = []
    estimated_minutes: int = 0
    summary: str = ""


class PathCheckout(BaseModel):
    """What starting checkout hands back. `unlocked` is true only in the
    stub/dev path, where purchase settles inline and there is no hosted
    page to visit."""

    reference: str
    checkout_url: str | None = None
    unlocked: bool = False
    amount_naira: int


class PathPaymentStatus(BaseModel):
    reference: str
    status: str
    unlocked: bool
    amount_naira: int


class ScheduleDayRead(BaseModel):
    day: int
    title: str = ""
    objectives: list[str] = []
    tasks: list[str] = []
    completed: bool = False
    completed_at: datetime | None = None


class ScheduleRead(BaseModel):
    id: int
    material_id: int
    title: str = ""
    days: list[ScheduleDayRead] = []
    days_total: int = 0
    days_completed: int = 0
    completion_percent: float = 0.0
    created_at: datetime | None = None
