# Talyn backend API — FastAPI + Postgres.
#
# Multi-stage so compilers and pip caches never reach the runtime image.
# Runs as a non-root user on a read-only-friendly filesystem; only /tmp is
# writable, which is all Python needs for bytecode and temp files.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for psycopg/cryptography wheels that lack a manylinux build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/home/app/.local/bin:${PATH}"

# curl for the container healthcheck, plus the Postgres client tools so
# scripts/backup.py can dump and rehearse a restore without shelling out to
# the database container.
#
# The client major version is pinned to the server's on purpose. A newer
# pg_dump against an older server emits settings that server rejects
# (pg_dump 17 writes SET transaction_timeout, which Postgres 16 refuses), so
# every restore rehearsal would fail for a reason that has nothing to do
# with your data. Debian trixie ships client 17 in its own repos, so 16 comes
# from the PGDG archive. Bump this when you bump the postgres: service image.
ARG POSTGRES_CLIENT_MAJOR=16
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        "postgresql-client-${POSTGRES_CLIENT_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. A container escape should not land on root.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app scripts ./scripts

USER app

EXPOSE 8000

# Unversioned on purpose: the load balancer polls this, and it must not
# depend on auth or the database being reachable in a specific order.
# Honors $PORT (Render and friends inject it; default 10000) with a local
# fallback, so the same image runs in compose and on a PaaS unchanged.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

# Migrate on boot, then serve. Render has no release phase, so the container
# brings its own schema with it; `alembic upgrade head` is idempotent, which
# is also what makes the compose `migrate` service safe to keep alongside.
# One worker per container; scale with replicas instead. Multiple workers in
# one container would each need their own connection pool.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app \
      --host 0.0.0.0 --port ${PORT:-8000} \
      --proxy-headers --forwarded-allow-ips '*' \
      --no-server-header"]
