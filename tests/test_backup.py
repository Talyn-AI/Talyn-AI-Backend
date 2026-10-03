"""Backups: the dump has to restore, and the sweep has to leave live files alone.

These run real pg_dump / pg_restore against the test database. A mocked
backup test proves nothing about whether the dump is restorable, which is
the only property that matters.
"""
import gzip
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import backup as backup_mod

pytestmark = pytest.mark.skipif(
    shutil.which("pg_dump") is None,
    reason="Postgres client tools are not on PATH",
)


def _has_tools() -> bool:
    return shutil.which("pg_dump") is not None and shutil.which("pg_restore") is not None


def _seed(client, creator_headers, learner_headers):
    """A database with enough shape that a partial restore would show."""
    course = client.post("/v1/courses", json={
        "title": "Backup Course", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=creator_headers).json()
    mid = client.post(f"/v1/courses/{course['id']}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    client.post(f"/v1/courses/{course['id']}/lessons", json={
        "module_id": mid, "title": "L", "topic": "Flexbox", "content": "c",
    }, headers=creator_headers)
    client.post(f"/v1/me/enroll/{course['id']}", headers=learner_headers)
    client.post(f"/v1/me/lessons/1/complete", json={}, headers=learner_headers)
    return course["id"]


def test_backup_writes_a_restorable_dump(client, creator_headers, learner_headers,
                                         tmp_path):
    """The whole point: produce a dump, then actually restore it."""
    if not _has_tools():
        pytest.skip("pg_restore not on PATH")

    _seed(client, creator_headers, learner_headers)

    result = backup_mod.main(["--dest", str(tmp_path), "--keep", "3"])
    assert result == 0, "backup reported failure"

    dumps = list(tmp_path.glob("talyn-*.dump.gz"))
    assert len(dumps) == 1, f"expected one dump, found {dumps}"

    with gzip.open(dumps[0], "rb") as handle:
        header = handle.read(5)
    assert header == b"PGDMP", "not a Postgres custom-format dump"

    manifest = (tmp_path / "MANIFEST.txt").read_text(encoding="utf-8")
    assert dumps[0].name in manifest
    assert "verified" in manifest


def test_restore_rehearsal_catches_a_truncated_dump(tmp_path):
    """A dump that cannot be restored must fail verification, not pass it."""
    if not _has_tools():
        pytest.skip("pg_restore not on PATH")

    broken = tmp_path / "talyn-broken.dump"
    broken.write_bytes(b"this is not a pg_dump file at all")

    ok, note = backup_mod.verify(broken, "talyn_restore_check")
    assert ok is False
    assert note, "a failure must explain itself"


def test_restore_rehearsal_detects_missing_rows(client, creator_headers,
                                                learner_headers, tmp_path,
                                                monkeypatch):
    """A dump that restores but is short of rows is still a bad backup."""
    if not _has_tools():
        pytest.skip("pg_restore not on PATH")

    _seed(client, creator_headers, learner_headers)
    assert backup_mod.main(["--dest", str(tmp_path)]) == 0
    dump = next(iter(tmp_path.glob("talyn-*.dump.gz")))

    # Pretend the live database has grown since the dump was taken.
    real_counts = backup_mod.table_counts
    calls = {"n": 0}

    def drifting_counts(env, database):
        counts = real_counts(env, database)
        calls["n"] += 1
        if calls["n"] == 1:  # the "live" read
            counts["users"] = counts.get("users", 0) + 99
        return counts

    monkeypatch.setattr(backup_mod, "table_counts", drifting_counts)
    ok, note = backup_mod.verify(dump, "talyn_restore_check")
    assert ok is False
    assert "differ" in note
    assert "users" in note


def test_verify_passes_on_an_untampered_dump(client, creator_headers,
                                             learner_headers, tmp_path):
    if not _has_tools():
        pytest.skip("pg_restore not on PATH")

    _seed(client, creator_headers, learner_headers)
    assert backup_mod.main(["--dest", str(tmp_path)]) == 0
    dump = next(iter(tmp_path.glob("talyn-*.dump.gz")))

    ok, note = backup_mod.verify(dump, "talyn_restore_check")
    assert ok is True, note
    assert "restored" in note


def test_unverified_dump_is_deleted_not_kept(tmp_path, monkeypatch):
    """Keeping a dump that failed verification is false comfort."""
    monkeypatch.setattr(backup_mod, "verify",
                        lambda dump, scratch: (False, "simulated corruption"))

    result = backup_mod.main(["--dest", str(tmp_path)])

    assert result == 1
    assert list(tmp_path.glob("talyn-*.dump*")) == [], (
        "a dump that failed verification must not be left on disk"
    )


def test_empty_dump_is_rejected(tmp_path, monkeypatch):
    """A zero-byte dump is the classic silent failure of a full disk."""
    def empty_dump(dest, compress=True):
        path = dest / "talyn-20990101-000000.dump"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    monkeypatch.setattr(backup_mod, "create_dump", empty_dump)

    assert backup_mod.main(["--dest", str(tmp_path)]) == 1
    assert list(tmp_path.glob("talyn-*.dump*")) == []


def test_dump_failure_exits_nonzero(tmp_path, monkeypatch):
    def boom(dest, compress=True):
        raise RuntimeError("pg_dump: could not connect to server")

    monkeypatch.setattr(backup_mod, "create_dump", boom)
    assert backup_mod.main(["--dest", str(tmp_path)]) == 1


# ── Retention ────────────────────────────────────────────────────────────────


def test_prune_keeps_the_newest_dumps(tmp_path):
    for stamp in ("20260101", "20260102", "20260103", "20260104", "20260105"):
        (tmp_path / f"talyn-{stamp}-000000.dump.gz").write_bytes(b"x")

    removed = backup_mod.prune(tmp_path, keep=2)

    assert len(removed) == 3
    remaining = sorted(p.name for p in tmp_path.glob("talyn-*.dump*"))
    assert remaining == ["talyn-20260104-000000.dump.gz",
                         "talyn-20260105-000000.dump.gz"]


def test_prune_removes_interrupted_partials(tmp_path):
    """A run killed mid-write leaves a .partial that is not a backup."""
    (tmp_path / ".20260101-000000.partial").write_bytes(b"half a dump")
    (tmp_path / "talyn-20260101-000000.dump.gz").write_bytes(b"x")

    backup_mod.prune(tmp_path, keep=10)

    assert not list(tmp_path.glob(".*.partial"))


def test_prune_under_the_limit_keeps_everything(tmp_path):
    for stamp in ("20260101", "20260102"):
        (tmp_path / f"talyn-{stamp}-000000.dump.gz").write_bytes(b"x")

    assert backup_mod.prune(tmp_path, keep=10) == []
    assert len(list(tmp_path.glob("talyn-*.dump*"))) == 2


def test_manifest_flags_an_unverified_backup(tmp_path):
    good = tmp_path / "talyn-20260101-000000.dump.gz"
    bad = tmp_path / "talyn-20260102-000000.dump.gz"
    for path in (good, bad):
        path.write_bytes(b"x")

    backup_mod.write_manifest(tmp_path, [
        backup_mod.DumpResult(good, "a" * 64, 1, True, "restored 9 tables"),
        backup_mod.DumpResult(bad, "b" * 64, 1, False, "row counts differ"),
    ])

    text = (tmp_path / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "20260101" in text and "verified" in text
    assert "UNVERIFIED (row counts differ)" in text


# ── Connection settings ──────────────────────────────────────────────────────


def test_connection_env_is_built_from_database_url(monkeypatch):
    """Credentials come from DATABASE_URL, never from ambient PG* vars."""
    from app.config import settings

    monkeypatch.setattr(
        settings, "database_url",
        "postgresql+psycopg://someone:s3cret@db.internal:6543/talyn_prod",
    )
    # A stale PGHOST in the environment must not win over the URL.
    monkeypatch.setenv("PGHOST", "wrong-host")
    monkeypatch.setenv("PGDATABASE", "wrong-db")

    env = backup_mod._psql_env()

    assert env["PGUSER"] == "someone"
    assert env["PGPASSWORD"] == "s3cret"
    assert env["PGHOST"] == "db.internal"
    assert env["PGPORT"] == "6543"
    assert env["PGDATABASE"] == "talyn_prod"


def test_connection_env_ignores_the_sqlalchemy_driver_prefix(monkeypatch):
    """The +psycopg driver suffix must not reach the psql tools."""
    from app.config import settings

    monkeypatch.setattr(
        settings, "database_url",
        "postgresql+psycopg://u:p@localhost:5432/talyn",
    )
    env = backup_mod._psql_env()
    assert env["PGDATABASE"] == "talyn"
    assert "psycopg" not in env.get("PGDATABASE", "")


def test_scratch_database_is_recreated_each_time(client, db_session):
    """Verification must not pass against a leftover database."""
    if not _has_tools():
        pytest.skip("psql not on PATH")
    _seed(client, *_headers(client))
    assert backup_mod.main(["--dest", "_tmp_backup_test"]) == 0
    ok, note = backup_mod.verify(
        next(iter(Path("_tmp_backup_test").glob("talyn-*.dump.gz"))),
        "talyn_restore_check",
    )
    assert ok is True, note
    shutil.rmtree("_tmp_backup_test", ignore_errors=True)


def _headers(client):
    def register(email, name, creator):
        client.post("/v1/auth/register", json={
            "email": email, "password": "password123",
            "learner_name": name, "is_creator": creator,
        })
        r = client.post("/v1/auth/login", json={
            "email": email, "password": "password123",
        })
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return (register("bkup-c@example.com", "BC", True),
            register("bkup-l@example.com", "BL", False))
