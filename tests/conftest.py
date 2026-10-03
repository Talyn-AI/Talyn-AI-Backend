"""Pytest fixtures for Talyn backend tests."""
import os
from datetime import datetime, timezone

# Disabled suite-wide: ~150 auth hits from one IP would trip the limiter.
# Rate limiting itself is covered with it enabled in test_gaps.py.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

# Never inherit real credentials from a developer's .env. Without this, a
# local PAYSTACK_SECRET_KEY makes the suite take the live checkout path
# instead of the stub, and tests quietly depend on whether a developer has
# finished configuring their machine. Tests that need Paystack enable it
# themselves (see tests/test_paystack.py).
os.environ["PAYSTACK_SECRET_KEY"] = ""
os.environ["PAYSTACK_WEBHOOK_SECRET"] = ""
os.environ["PAYSTACK_API_URL"] = ""

# Same reasoning for SMTP. A developer's real mail credentials would make the
# suite attempt genuine sends against the live server, and a credential
# failure would surface as an unexpected 502 rather than as "unconfigured".
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USERNAME"] = ""
os.environ["SMTP_PASSWORD"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Isolated test database. The backend expects `talyn_test` to exist on the
# same Postgres server (see README).
TEST_DATABASE_URL = os.getenv(
    "TALYN_TEST_DATABASE_URL",
    "postgresql+psycopg://talyn:talyn_dev_password@localhost:5432/talyn_test",
)

engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
def prepare_schema():
    """Create all tables once per test session."""
    from app.database import Base
    from app import models  # noqa: F401  ensure models are imported

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_tables(prepare_schema):
    """Truncate all tables before each test so tests start from a clean DB."""
    from app.database import Base

    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture()
def db_session(prepare_schema):
    session: Session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def onboard(db_session):
    """Mark a user as having verified their email and finished onboarding.

    Onboarding is gated on a token that only exists in an email, so tests
    would otherwise have to fake a confirmation for every learner. This
    stamps the same state the real flow ends at, which keeps the gate itself
    tested where it matters — in tests/test_onboarding.py, which drives the
    real endpoints and deliberately does not use this.

    Accepts either an email address or a ready-made auth-headers dict, since
    both spellings are common in this suite.
    """

    def _onboard(who, pace: str = "steady", interests=("design",)) -> None:
        from app.models import User

        if isinstance(who, dict):
            who = who["Authorization"].removeprefix("Bearer ")
            # tokens are JWTs; recover the subject by decoding rather than
            # making every call site remember which email it signed in with.
            from app.core.security import decode_token

            who = str(decode_token(who)["sub"])
            user = db_session.get(User, int(who))
        else:
            user = db_session.query(User).filter(User.email == who).one()

        now = datetime.now(timezone.utc)
        user.email_verified_at = now
        user.learning_pace = pace
        # Only fill interests if the fixture has not already chosen some —
        # several deliberately set them, and overwriting quietly broke their
        # assertions about what the learner is into.
        if not user.interests:
            user.interests = list(interests)
        user.onboarding_completed_at = now
        db_session.commit()

    return _onboard


@pytest.fixture()
def client(db_session):
    """TestClient with the DB session dependency overridden to the test DB."""
    from app.database import get_db
    from app.main import app

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def admin_headers(client, db_session, onboard):
    """Headers for an admin user (course management)."""
    from sqlalchemy import select

    from app.models import User

    client.post(
        "/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "secret12345",
            "learner_name": "Admin",
        },
    )
    user = db_session.scalar(select(User).where(User.email == "admin@example.com"))
    user.is_admin = True
    db_session.commit()
    r = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "secret12345"},
    )
    onboard("admin@example.com")

    return {"Authorization": f"Bearer {r.json()['access_token']}"}

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def creator_headers(client, onboard):
    client.post("/v1/auth/register", json={
        "email": "upload-creator@example.com", "password": "password123",
        "learner_name": "Uploader", "is_creator": True,
    })
    r = client.post("/v1/auth/login", json={
        "email": "upload-creator@example.com", "password": "password123",
    })
    onboard("upload-creator@example.com")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def learner_headers(client, onboard):
    client.post("/v1/auth/register", json={
        "email": "upload-learner@example.com", "password": "password123",
        "learner_name": "Learner",
    })
    r = client.post("/v1/auth/login", json={
        "email": "upload-learner@example.com", "password": "password123",
    })
    onboard("upload-learner@example.com")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def lesson_id(client, creator_headers, db_session):
    """A published lesson the creator owns."""
    from app.models import Course

    cid = client.post("/v1/courses", json={
        "title": "Upload Course", "description": "Has lessons",
        "category": "Design", "outcomes": ["Learn"],
        "target_audience": "All", "thumbnail_key": "t.png",
    }, headers=creator_headers).json()["id"]
    mid = client.post(f"/v1/courses/{cid}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    lid = client.post(f"/v1/courses/{cid}/lessons", json={
        "module_id": mid, "title": "Lesson One", "topic": "Basics",
        "content": "Content.",
    }, headers=creator_headers).json()["id"]
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return lid


# ── Captured SMTP ────────────────────────────────────────────────────────────
# Lives here rather than in test_email.py because test_onboarding.py also has
# to read the verification token out of a sent message to follow the link the
# way a user would.


class _SMTP:
    """Stand-in for smtplib.SMTP that records what it was asked to send."""
    # Imported lazily inside the fixture below; referenced here for the
    # exception type raised on a simulated failure.

    def __init__(self, state):
        self.state = state
        self.host = state["host"]
        self.port = state["port"]
        self.timeout = state["timeout"]
        self.logged_in = False
        self.starttls_used = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_used = True

    def login(self, user, password):
        self.logged_in = True

    def send_message(self, message):
        from app.services import email as email_service

        if self.state["fail"]:
            raise email_service.smtplib.SMTPException("mailbox unavailable")
        self.state["sent"].append(message)


@pytest.fixture
def smtp(monkeypatch):
    """Replace SMTP with a capture double.

    State is shared across every client the service builds, so `smtp.fail()`
    affects sends that happen after it is set rather than only ones already
    in flight.
    """
    from app import config as config_module
    from app.services import email as email_service
    state = {
        "sent": [], "fail": False, "host": None, "port": None,
        "timeout": None, "last": None,
    }

    def factory(host, port, timeout=None, **kwargs):
        state.update(host=host, port=port, timeout=timeout)
        instance = _SMTP(state)
        state["last"] = instance
        return instance

    monkeypatch.setattr(email_service.smtplib, "SMTP", factory)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", factory)
    monkeypatch.setattr(config_module.settings, "smtp_host", "smtp.talyn.dev")
    monkeypatch.setattr(config_module.settings, "smtp_port", 587)
    monkeypatch.setattr(config_module.settings, "smtp_username", "apikey")
    monkeypatch.setattr(config_module.settings, "smtp_password", "secret")
    monkeypatch.setattr(config_module.settings, "smtp_from",
                        "Talyn <no-reply@talyn.dev>")

    class Handle:
        sent = state["sent"]

        @property
        def last(self):
            return state["last"]

        def fail(self):
            state["fail"] = True

    return Handle()


@pytest.fixture
def smtp_off(monkeypatch):
    """No SMTP configured: the dev path that returns the token inline."""
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "smtp_host", "")
    monkeypatch.setattr(config_module.settings, "environment", "dev")


