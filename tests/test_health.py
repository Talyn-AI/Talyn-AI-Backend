"""Health probes.

Liveness and readiness answer different questions, and conflating them
causes outages: a liveness probe that touches the database restarts a
healthy process every time Postgres hiccups.
"""
def test_readiness_reports_dependencies(client):
    """An uptime check needs to distinguish alive from able to serve."""
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    # Storage is reported but never blocks readiness, or an S3 outage would
    # take sign-in down with it.
    assert "storage_configured" in body["checks"]


def test_readiness_reports_degraded_when_the_database_is_gone(client):
    """The 503 has to name what failed, not arrive as one opaque 503."""
    from sqlalchemy.exc import OperationalError

    from app.database import get_db
    from app.main import app

    class BrokenSession:
        def execute(self, *a, **kw):
            raise OperationalError("SELECT 1", {}, Exception("refused"))

        def close(self):
            pass

    def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        r = client.get("/health/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["checks"]["database"] is False


def test_liveness_ignores_the_database(client):
    """A liveness probe that fails on a database blip restarts a healthy
    process and turns a small problem into an outage."""
    from sqlalchemy.exc import OperationalError

    from app.database import get_db
    from app.main import app

    class BrokenSession:
        def execute(self, *a, **kw):
            raise OperationalError("SELECT 1", {}, Exception("refused"))

        def close(self):
            pass

    def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["status"] == "ok"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_readiness_omits_redis_when_not_configured(client, monkeypatch):
    from app import config as config_module
    from app.core.limit_stores import reset_rate_limit_store

    monkeypatch.setattr(config_module.settings, "redis_url", "")
    reset_rate_limit_store()

    body = client.get("/health/ready").json()
    assert body["checks"]["redis"] is None


def test_health_needs_no_authentication(client):
    """The load balancer has no session; both probes must be open."""
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200