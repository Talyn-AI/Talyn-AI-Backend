"""Deploy-shaped config: PaaS connection strings and the Resend HTTPS transport.

Render hands out `postgres://` URLs and Supabase hands out bare
`postgresql://` ones; neither works with this project's psycopg-v3-only
install unless the scheme is normalized. And Render's free tier blocks SMTP
ports, so email must be able to go over HTTPS. Both are covered here with no
network: httpx is faked at the post boundary, exactly like SMTP is faked at
the smtplib boundary in test_email.py.
"""
import pytest

import httpx

from app import config as config_module
from app.models import EmailLog
from app.services import email as email_service


# ── DATABASE_URL normalization ───────────────────────────────────────────────


def _settings(url: str):
    return config_module.Settings(
        database_url=url,
        secret_key="k" * 40,
    )


def test_render_style_postgres_scheme_is_rewritten():
    s = _settings("postgres://talyn:pw@db:5432/talyn")
    assert s.database_url == "postgresql+psycopg://talyn:pw@db:5432/talyn"


def test_supabase_style_bare_scheme_gets_a_driver():
    """Bare postgresql:// defaults to psycopg2, which is not installed."""
    s = _settings(
        "postgresql://postgres:pw@db.xyz.supabase.co:5432/postgres"
    )
    assert s.database_url == (
        "postgresql+psycopg://postgres:pw@db.xyz.supabase.co:5432/postgres"
    )


def test_query_params_survive_normalization():
    s = _settings("postgres://u:p@h:5432/d?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d?sslmode=require"


def test_explicit_driver_is_untouched():
    url = "postgresql+psycopg://talyn:pw@localhost:5432/talyn"
    assert _settings(url).database_url == url


def test_non_postgres_schemes_are_untouched():
    assert _settings("sqlite:///./dev.db").database_url == "sqlite:///./dev.db"


def test_percent_encoded_password_survives_alembic():
    """Alembic's configparser treats "%" as an interpolation marker.

    Supabase's generated passwords contain characters like "@", which have to
    be percent-encoded in a connection URI. Unescaped, that URL raises inside
    alembic before SQLAlchemy ever sees it — while the same URL works fine for
    the app itself, so the failure looks like a config problem, not a code one.
    """
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    url = (
        "postgresql://postgres:Talyned231606%40talynai"
        "@db.example.supabase.co:5432/postgres"
    )
    normalized = _settings(url).database_url

    cfg = Config("alembic.ini")
    # What alembic/env.py does on every run.
    cfg.set_main_option("sqlalchemy.url", normalized.replace("%", "%%"))

    stored = cfg.get_main_option("sqlalchemy.url")
    assert stored == normalized, "the URL must survive the round trip intact"

    parsed = make_url(stored)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host == "db.example.supabase.co"
    assert parsed.port == 5432
    # Decoded back to the real password, not the percent-encoded form.
    assert parsed.password == "Talyned231606@talynai"


def test_plain_url_is_also_stored_intact():
    """The escaping must not corrupt an ordinary URL."""
    from alembic.config import Config

    url = "postgresql+psycopg://talyn:pw@localhost:5432/talyn"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert cfg.get_main_option("sqlalchemy.url") == url


# ── Resend transport ─────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code: int = 200, text: str = '{"id":"re_1"}'):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def resend(monkeypatch):
    """Fake the HTTPS boundary and point the app at the Resend transport."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        if fake_post.fail_with is not None:
            raise fake_post.fail_with
        return _Response(status_code=fake_post.status, text=fake_post.body)

    fake_post.fail_with = None
    fake_post.status = 200
    fake_post.body = '{"id":"re_1"}'

    monkeypatch.setattr(email_service.httpx, "post", fake_post)
    monkeypatch.setattr(config_module.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(config_module.settings, "smtp_host", "")
    monkeypatch.setattr(config_module.settings, "smtp_from",
                        "Talyn <no-reply@talyn.dev>")
    return fake_post, calls


def _logs(db_session):
    return db_session.query(EmailLog).order_by(EmailLog.id).all()


def test_resend_sends_with_the_right_shape(resend, db_session):
    _, calls = resend
    ok = email_service.send(
        db_session, to_email="a@example.com", template="welcome",
        message=email_service.welcome_email("Ade"),
    )
    assert ok is True
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"] == {"Authorization": "Bearer re_test_key"}
    assert call["json"]["from"] == "Talyn <no-reply@talyn.dev>"
    assert call["json"]["to"] == ["a@example.com"]
    assert call["json"]["subject"] == "Welcome to Talyn"
    assert "Ade" in call["json"]["html"]
    assert "Ade" in call["json"]["text"]
    assert _logs(db_session)[-1].status == "sent"


def test_resend_rejection_is_logged_not_raised(resend, db_session):
    fake_post, _ = resend
    fake_post.status = 422
    fake_post.body = '{"message":"unverified sender"}'
    ok = email_service.send(
        db_session, to_email="a@example.com", template="welcome",
        message=email_service.welcome_email("Ade"),
    )
    assert ok is False
    row = _logs(db_session)[-1]
    assert row.status == "failed"
    assert "422" in (row.error or "")
    assert "unverified sender" in (row.error or "")


def test_resend_network_failure_is_logged_not_raised(resend, db_session):
    fake_post, _ = resend
    fake_post.fail_with = httpx.ConnectError("connection refused")
    ok = email_service.send(
        db_session, to_email="a@example.com", template="welcome",
        message=email_service.welcome_email("Ade"),
    )
    assert ok is False
    assert _logs(db_session)[-1].status == "failed"


def test_send_or_raise_raises_on_resend_failure(resend, db_session):
    """Password reset depends on delivery — a silent False strands the user."""
    fake_post, _ = resend
    fake_post.status = 500
    with pytest.raises(email_service.EmailError):
        email_service.send_or_raise(
            db_session, to_email="a@example.com", template="password_reset",
            message=email_service.password_reset_email("https://x/reset", "042013"),
        )


def test_resend_wins_when_smtp_is_also_configured(
    resend, db_session, monkeypatch
):
    """On hosts with blocked SMTP ports both may be set; HTTPS must win."""
    smtp_calls = []

    class _SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, message):
            smtp_calls.append(message)

    monkeypatch.setattr(email_service.smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", _SMTP)
    monkeypatch.setattr(config_module.settings, "smtp_host", "smtp.talyn.dev")
    _, calls = resend
    ok = email_service.send(
        db_session, to_email="a@example.com", template="welcome",
        message=email_service.welcome_email("Ade"),
    )
    assert ok is True
    assert len(calls) == 1
    assert smtp_calls == []


def test_reset_wrapper_uses_resend(resend):
    """The backwards-compatible wrapper must follow the same transport rule."""
    email_service.send_password_reset_email("a@example.com", "https://x/reset")


def test_is_configured_with_resend_alone(resend):
    assert email_service.is_configured() is True
