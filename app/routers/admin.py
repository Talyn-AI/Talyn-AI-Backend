"""Admin router: user management + audit log. Admin only throughout."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_admin
from app.database import get_db
from app.models import AdminAuditLog, EmailLog, User
from app.models.admin import ACTION_DEMOTE, ACTION_PROMOTE
from app.models.email_log import FAILED
from app.schemas.admin import AdminUserRead, AuditLogRead, EmailLogRead, RoleUpdate

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/users", response_model=list[AdminUserRead])
def list_users(
    q: str | None = Query(default=None, description="Search by email"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[User]:
    """List users, newest first, optionally filtered by email."""
    query = select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    if q:
        query = query.where(User.email.ilike(f"%{q}%"))
    return list(db.scalars(query).all())


@router.patch("/users/{user_id}", response_model=AdminUserRead)
def set_role(
    user_id: int,
    payload: RoleUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    """Change a user's roles. Self-demotion is rejected; changes are logged."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin.id and payload.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Admins cannot demote themselves",
        )

    changed_to = None
    if payload.is_admin is not None and payload.is_admin != target.is_admin:
        target.is_admin = payload.is_admin
        changed_to = payload.is_admin
    if payload.is_creator is not None and payload.is_creator != target.is_creator:
        target.is_creator = payload.is_creator
        changed_to = payload.is_creator if changed_to is None else changed_to
    if changed_to is None:
        return target

    db.add(
        AdminAuditLog(
            actor_user_id=admin.id,
            target_user_id=target.id,
            action=ACTION_PROMOTE if changed_to else ACTION_DEMOTE,
        )
    )
    db.commit()
    db.refresh(target)
    return target


@router.get("/audit-log", response_model=list[AuditLogRead])
def read_audit_log(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLogRead]:
    """Read the admin audit log, newest first."""
    rows = db.scalars(
        select(AdminAuditLog)
        .order_by(AdminAuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    user_ids = {r.actor_user_id for r in rows} | {r.target_user_id for r in rows}
    emails = {
        u.id: u.email
        for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}
    return [
        AuditLogRead(
            id=r.id,
            actor_user_id=r.actor_user_id,
            actor_email=emails.get(r.actor_user_id, "?"),
            target_user_id=r.target_user_id,
            target_email=emails.get(r.target_user_id, "?"),
            action=r.action,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/email-log", response_model=list[EmailLogRead])
def read_email_log(
    failed_only: bool = Query(
        default=False, description="Only rows where delivery did not succeed"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[EmailLogRead]:
    """Read transactional email history, newest first.

    Email is the failure mode that is easiest to miss: a reset that never
    arrived looks identical to a learner who forgot they asked. This is where
    you confirm what was actually sent and what bounced.
    """
    query = select(EmailLog).order_by(EmailLog.id.desc())
    if failed_only:
        query = query.where(EmailLog.status == FAILED)
    rows = db.scalars(query.limit(limit).offset(offset)).all()
    return [EmailLogRead.model_validate(r) for r in rows]
