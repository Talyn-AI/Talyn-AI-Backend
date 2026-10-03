"""Application settings, loaded from .env."""
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Talyn Backend API"
    debug: bool = False
    environment: str = "dev"  # dev | staging | prod

    # Database — no default: must come from .env or the environment.
    # Accepts postgres:// (Render, Heroku-style dashboards), bare
    # postgresql:// (Supabase, Neon), and the explicit postgresql+psycopg://
    # used locally. Bare postgresql:// defaults to the psycopg2 driver, which
    # is not installed here, and postgres:// is not a SQLAlchemy scheme at
    # all — both are rewritten below, so pasting a dashboard value just works.
    database_url: str

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_scheme(cls, value: Any) -> Any:
        if isinstance(value, str):
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+psycopg://" + value[len(prefix):]
        return value

    # Auth — no default: must come from .env or the environment.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str
    access_token_expire_minutes: int = 60 * 24  # 24h for dev convenience
    jwt_algorithm: str = "HS256"

    # CORS: comma-separated origins. "*" allows everything (dev only).
    cors_origins: str = "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000"

    # Rate limiting (requests per minute per IP; 60s windows).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 20
    rate_limit_per_minute: int = 300
    # Shared counters for multi-worker deployments. Empty = in-memory.
    redis_url: str = ""

    # S3-compatible object storage (videos, thumbnails, resources).
    # Empty s3_bucket = unconfigured (upload endpoints fail closed).
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    # Turn on S3 server-side encryption for uploaded objects. Cheapest
    # possible mitigation for "user files pile up".
    s3_server_side_encryption: str = "AES256"

    # Per-purpose upload ceilings in bytes. Enforced by the storage provider
    # on a presigned POST (content-length-range), not by the client, so a
    # direct PUT cannot exceed them. Defaults: 5 MB thumbnails, 512 MB
    # video, 100 MB resources, 2 MB profile images.
    max_thumbnail_bytes: int = 5 * 1024 * 1024
    max_video_bytes: int = 512 * 1024 * 1024
    max_resource_bytes: int = 100 * 1024 * 1024
    max_profile_image_bytes: int = 2 * 1024 * 1024

    # How long an uploaded-but-never-attached object survives before the
    # orphan sweep deletes it. Without this, every abandoned presign leaks
    # storage forever.
    orphan_upload_ttl_hours: int = 24

    # Malware scanning. Empty clamav_host = scanning disabled, and uploads are
    # accepted unscanned. Set a host to require a verdict before an asset can
    # be attached — see upload_scanning_required.
    clamav_host: str = ""
    clamav_port: int = 3310
    # Refuse to attach a file when ClamAV is configured but unreachable.
    # False keeps uploads working through a scanner outage, at the cost of
    # accepting unscanned files. Turn this on once you trust the host.
    upload_scanning_required: bool = False

    frontend_url: str = "http://localhost:3000"

    # Google OAuth sign-in. Empty = endpoint fails closed.
    # Create a Web client ID at Google Cloud Console > APIs & Services >
    # Credentials, then set it here and in the frontend (VITE_GOOGLE_CLIENT_ID).
    google_client_id: str = ""

    # Paystack (NGN payments). Empty secret = stub provider (auto-success),
    # which is only ever allowed outside a real environment.
    # Dashboard > Settings > API Keys: PAYSTACK_SECRET_KEY is the live key.
    paystack_secret_key: str = ""
    # Separate secret for verifying webhook signatures. Empty = reuse the API
    # key, which is what Paystack documents; set it if you rotate keys.
    paystack_webhook_secret: str = ""
    # Overridable for tests / self-hosted mocks. Empty = Paystack's API.
    paystack_api_url: str = ""
    # Where the learner lands after paying (Paystack appends ?reference=...).
    payment_return_url: str = ""
    # Public origin used to build checkout callback URLs. Falls back to
    # frontend_url when empty.
    public_url: str = ""
    allow_stub_payments: bool = False

    # Transactional email (password reset, receipts). Empty smtp_host =
    # unconfigured, and reset then fails closed.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "Talyn <no-reply@talyn.dev>"
    # Resend HTTPS API. Preferred over SMTP wherever SMTP ports are blocked
    # (Render's free tier blocks 25/465/587). Same templates, same logging —
    # only the transport changes. Set = used instead of SMTP. The sender
    # address must be verified in the Resend dashboard, or Resend rejects it.
    resend_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def scanner_enabled(self) -> bool:
        return bool(self.clamav_host)

    def max_upload_bytes(self, purpose: str) -> int:
        """Ceiling for one upload of this purpose."""
        return {
            "thumbnail": self.max_thumbnail_bytes,
            "video": self.max_video_bytes,
            "resource": self.max_resource_bytes,
            "profile_image": self.max_profile_image_bytes,
        }.get(purpose, self.max_resource_bytes)

    @property
    def payments_origin(self) -> str:
        """Base URL Paystack redirects the learner back to after checkout."""
        return (self.payment_return_url or self.frontend_url).rstrip("/")

    def payment_callback_url(self, course_id: int) -> str:
        """Where Paystack sends the learner once they finish (or abandon) paying.

        The frontend reads ?reference= off this URL and confirms the payment
        against our API before unlocking anything.
        """
        return (
            f"{self.payments_origin}/checkout/callback"
            f"?course_id={int(course_id)}"
        )

    def ensure_production_secrets(self) -> None:
        """Fail fast when a non-dev environment is misconfigured.

        Two things that are fine locally are unacceptable in production: a
        weak signing secret, and a missing payment provider (which would
        leave the stub provider auto-succeeding every purchase — handing
        paid courses away for free).
        """
        if self.environment != "dev" and len(self.secret_key) < 32:
            raise RuntimeError(
                f"SECRET_KEY must be a strong random value (>= 32 chars) when "
                f"ENVIRONMENT={self.environment!r}"
            )
        if (
            self.environment != "dev"
            and not self.paystack_secret_key
            and not self.allow_stub_payments
        ):
            raise RuntimeError(
                f"PAYSTACK_SECRET_KEY must be set when ENVIRONMENT="
                f"{self.environment!r}. Set it, or explicitly set "
                f"ALLOW_STUB_PAYMENTS=true to acknowledge that every "
                f"purchase will succeed without collecting money."
            )


settings = Settings()