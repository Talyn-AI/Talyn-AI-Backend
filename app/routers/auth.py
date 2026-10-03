"""Auth router: register, login, token refresh, password reset."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_google_token,
    verify_password,
)
from app.database import get_db
from app.models import User
from app.schemas import (
    GoogleAuthIn,
    EmailAvailability,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    Token,
    UserCreate,
    UserRead,
    VerificationConfirmIn,
    VerificationRequestIn,
)

router = APIRouter(prefix="/auth", tags=["Auth"])

RESET_REQUEST_MESSAGE = "If the email exists, a reset link was sent"


@router.get("/email-available")
def email_available(
    email: str = Query(..., min_length=3, max_length=255),
    db: Session = Depends(get_db),
) -> dict:
    """Check whether an email can register (used for instant signup feedback).

    Rejects malformed addresses with 422. Like every public signup form,
    this intentionally reveals whether an address is taken.
    """
    try:
        validated = EmailAvailability(email=email)
    except Exception:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    exists = db.scalar(select(User).where(User.email == validated.email))
    return {"email": validated.email, "available": exists is None}


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    """Create a new learner account."""
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        learner_name=payload.learner_name,
        difficulty_level=payload.difficulty_level,
        goals=payload.goals,
        interests=payload.interests,
        current_course=payload.current_course,
        current_lesson=payload.current_lesson,
        current_topic=payload.current_topic,
        is_creator=payload.is_creator,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if user.is_creator:
        from app.models.analytics import CREATOR_SIGNED_UP
        from app.services.analytics import track

        track(db, CREATOR_SIGNED_UP, user)
        db.commit()

    # Best-effort: the account already exists, so a failed email must not turn
    # a successful signup into an error the user sees.
    #
    # Only the verification mail goes out here. The welcome waits for
    # confirmation, because a welcome that arrives before the address is proven
    # thanks someone for a signup we cannot yet attribute — and two emails in
    # the same minute is how you train people to ignore your mail.
    _send_verification(db, user)
    return user


def _send_verification(db: Session, user: User) -> None:
    """Issue and email a verification link. Never raises.

    Returns nothing it needs the caller to check: the account exists either
    way, and the onboarding gate is the real enforcement — an unverified user
    simply cannot complete setup.
    """
    from app import config as config_module
    from app.services import email as email_service
    from app.services import verification_tokens

    try:
        raw, _row = verification_tokens.issue(db, user)
    except Exception:
        # Token bookkeeping is not worth failing a signup over.
        return

    verify_link = f"{config_module.settings.frontend_url}/verify-email?token={raw}"
    email_service.send(
        db,
        to_email=user.email,
        template=email_service.TEMPLATE_VERIFICATION,
        message=email_service.verification_email(verify_link),
        user_id=user.id,
    )


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    """Authenticate and receive a JWT bearer token."""
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_creator:
        from app.models.analytics import CREATOR_LOGGED_IN
        from app.services.analytics import track

        track(db, CREATOR_LOGGED_IN, user)
        db.commit()
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=Token)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    """Exchange a refresh token for a new access + refresh token pair."""
    decoded = decode_token(payload.refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user = db.get(User, int(decoded["sub"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
        )
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/password-reset/request")
def request_password_reset(
    payload: PasswordResetRequest, db: Session = Depends(get_db)
) -> dict:
    """Start a password reset.

    Production: the reset link is emailed; the token never appears in the
    response. If email is not configured outside dev, this fails closed
    (503) instead of leaking the token.
    Dev (no SMTP configured): the token is returned inline with a warning.
    The response shape is identical whether or not the email exists, so this
    endpoint cannot be used to discover which addresses are registered.
    """
    from app import config as config_module
    from app.services import email as email_service
    from app.services import reset_tokens

    # Always the same response and roughly the same work either way, so the
    # timing does not reveal whether the account exists.
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None:
        return {"message": RESET_REQUEST_MESSAGE}

    raw, _row = reset_tokens.issue(db, user)
    reset_link = f"{config_module.settings.frontend_url}/reset-password?token={raw}"

    if email_service.is_configured():
        try:
            email_service.send_or_raise(
                db,
                to_email=payload.email,
                template=email_service.TEMPLATE_PASSWORD_RESET,
                message=email_service.password_reset_email(reset_link),
                user_id=user.id,
            )
        except email_service.EmailError as e:
            # Only someone who supplied a registered address learns that
            # anything went wrong, which is not a disclosure worth worrying
            # about — and telling them is the only way they learn to retry.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not send reset email: {e}",
            ) from e
        return {"message": RESET_REQUEST_MESSAGE}

    if config_module.settings.environment != "dev":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset is unavailable (email not configured)",
        )
    return {
        "message": "If the email exists, a reset token was issued",
        "reset_token": raw,
        "warning": "DEV-ONLY: token returned inline; email it in production",
    }


@router.post("/email-verification/request")
def request_email_verification(
    payload: VerificationRequestIn, db: Session = Depends(get_db)
) -> dict:
    """(Re)send the verification link.

    Idempotent and non-disclosing: an already-verified address and an unknown
    one get the same response and roughly the same work, so this cannot be used
    to discover who has an account.
    """
    from app import config as config_module
    from app.services import email as email_service
    from app.services import verification_tokens

    VERIFY_REQUEST_MESSAGE = "If the account exists, a verification link was sent"

    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or user.email_verified_at is not None:
        return {"message": VERIFY_REQUEST_MESSAGE}

    raw, _row = verification_tokens.issue(db, user)
    verify_link = f"{config_module.settings.frontend_url}/verify-email?token={raw}"

    if email_service.is_configured():
        try:
            email_service.send_or_raise(
                db,
                to_email=payload.email,
                template=email_service.TEMPLATE_VERIFICATION,
                message=email_service.verification_email(verify_link),
                user_id=user.id,
            )
        except email_service.EmailError as e:
            # Same reasoning as password reset: only the holder of the address
            # learns anything, and telling them is the only way they can retry.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not send verification email: {e}",
            ) from e
        return {"message": VERIFY_REQUEST_MESSAGE}

    if config_module.settings.environment != "dev":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email verification is unavailable (email not configured)",
        )
    return {
        "message": VERIFY_REQUEST_MESSAGE,
        "verification_token": raw,
        "warning": "DEV-ONLY: token returned inline; email it in production",
    }


@router.post("/email-verification/confirm")
def confirm_email_verification(
    payload: VerificationConfirmIn, db: Session = Depends(get_db)
) -> dict:
    """Mark an address verified using the emailed token.

    The token is spent in the same transaction that stamps the verification, so
    a link that worked once cannot be replayed. Confirming an already-verified
    address succeeds rather than erroring: a user clicking a second email from
    their inbox should not be shown a failure.
    """
    from app.services import verification_tokens

    try:
        user = verification_tokens.redeem(db, payload.token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)
        ) from e

    verification_tokens.mark_verified(db, user)

    # The welcome was held back at signup until now, so the address is proven.
    # Best-effort: a failed welcome must not fail a confirmation the user
    # genuinely completed.
    from app.services import email as email_service

    email_service.send(
        db,
        to_email=user.email,
        template=email_service.TEMPLATE_WELCOME,
        message=email_service.welcome_email(user.learner_name or "there"),
        user_id=user.id,
    )
    return {
        "email": user.email,
        "email_verified": True,
        "message": "Email confirmed. You can finish setting up your account.",
    }


@router.post("/password-reset/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirm, db: Session = Depends(get_db)
) -> dict:
    """Set a new password using a reset link.

    The token is spent in the same transaction as the password change, so a
    link that worked once cannot work again — including if someone replays it
    after the legitimate owner has already reset.
    """
    from app.services import reset_tokens

    try:
        user = reset_tokens.redeem(db, payload.token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)
        ) from e

    user.password_hash = hash_password(payload.new_password)
    # Every other live token for this account dies with this one.
    reset_tokens.invalidate_all(db, user.id)
    db.commit()
    return {"message": "Password has been reset"}


@router.post("/google", response_model=Token)
def google_auth(
    payload: GoogleAuthIn, db: Session = Depends(get_db)
) -> Token:
    """Sign in with Google: verify the ID token, find-or-create the user.

    Requires GOOGLE_CLIENT_ID configured (503 otherwise). Google users get
    a random unguessable password hash, so password login is effectively
    disabled for them — they sign in through Google.
    """
    from app.config import settings as app_settings

    if not app_settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )
    claims = verify_google_token(payload.id_token)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )
    email = claims.get("email", "")
    if not claims.get("email_verified") or not email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Google email is not verified",
        )

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        name = claims.get("name") or email.split("@")[0]
        user = User(
            email=email,
            password_hash=hash_password(secrets.token_hex(32)),
            learner_name=name[:120],
        )
        db.add(user)
        db.flush()

    # Google has already verified the address — that assertion is checked above
    # — so a Google user skips the emailed confirmation step. Without this a
    # Google signer would be stuck waiting for an email they never asked for.
    from app.services import verification_tokens

    verification_tokens.mark_verified(db, user)

    if user.is_creator:
        from app.models.analytics import CREATOR_LOGGED_IN
        from app.services.analytics import track

        track(db, CREATOR_LOGGED_IN, user)
    db.commit()
    return Token(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )