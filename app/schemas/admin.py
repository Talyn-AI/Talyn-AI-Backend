"""Admin schemas: user management + audit log."""
from datetime import datetime

from pydantic import BaseModel


class RoleUpdate(BaseModel):
    is_admin: bool | None = None
    is_creator: bool | None = None


class AdminUserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    email: str
    learner_name: str
    is_admin: bool
    created_at: datetime


class AuditLogRead(BaseModel):
    id: int
    actor_user_id: int | None
    actor_email: str
    target_user_id: int | None
    target_email: str
    action: str
    created_at: datetime


class EmailLogRead(BaseModel):
    """One transactional send, successful or not."""

    model_config = {"from_attributes": True}

    id: int
    user_id: int | None
    to_email: str
    template: str
    subject: str
    status: str
    error: str | None
    created_at: datetime
