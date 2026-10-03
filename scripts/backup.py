"""Automated Postgres backups with retention and a real restore rehearsal.

The important word is *verified*. A backup that has never been restored is a
guess, and a backup script that only writes files will keep reporting success
long after the dumps have stopped being restorable — wrong credentials, a
schema change nobody re-tested, a truncated file on a full disk.

So this does three things:

  1. Dump in Postgres custom format (`-Fc`). Compressed and restorable
     selectively; plain SQL of a large database is slow and much bigger.
  2. Verify immediately, by restoring into a scratch database and comparing
     row counts against the live one. A dump that cannot be restored is
     deleted rather than kept as false comfort.
  3. Prune by retention.

Run it from cron; the compose stack calls this same script:

    python -m scripts.backup --verify          # nightly
    python -m scripts.backup --verify --keep 14
    python -m scripts.backup --dest /mnt/backups
    python -m scripts.backup --no-verify       # faster, weaker

What this does NOT cover: object storage. Enable S3 versioning and a bucket
lifecycle rule, or take periodic bucket snapshots. A database restore
without its referenced media is a restore of a broken app.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Tables whose row counts are compared after a restore rehearsal. Chosen
# because losing any of them loses something a user would notice.
VERIFY_TABLES = (
    "users", "courses", "lessons", "enrollments", "payments",
    "xp_events", "lesson_progress", "lesson_assets", "email_log",
)

DEFAULT_KEEP = 7
# Enough history that a bad deploy has to be noticed before the good dumps
# rotate away. Seven daily runs is a week.
DEFAULT_DEST = Path(os.getenv("BACKUP_DIR", "./backups"))


@dataclass
class DumpResult:
    path: Path
    sha256: str
    size_bytes: int
    verified: bool
    verification_note: str


def _psql_env() -> dict:
    """Environment for the local psql tools, built from DATABASE_URL."""
    from app.config import settings

    url = settings.database_url
    # postgresql+psycopg://user:pass@host:port/db -> plain postgres URL.
    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    from sqlalchemy.engine import make_url

    parsed = make_url(plain)
    env = dict(os.environ)
    if parsed.username:
        env["PGUSER"] = parsed.username
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    if parsed.host:
        env["PGHOST"] = parsed.host
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    env["PGDATABASE"] = parsed.database or "talyn"
    return env


def _run(
    args: list[str],
    env: dict,
    stdin=None,
    stdout=None,
    timeout: int = 900,
) -> subprocess.CompletedProcess:
    """Run a Postgres client tool.

    `capture_output=True` and an explicit `stdout` are mutually exclusive in
    subprocess, so only pass capture when nobody supplied a stream.
    """
    kwargs: dict = {"env": env, "stdin": stdin, "timeout": timeout, "check": False}
    if stdout is not None:
        kwargs["stdout"] = stdout
    else:
        kwargs["capture_output"] = True
    return subprocess.run(args, **kwargs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_dump(dest: Path, compress: bool = True) -> Path:
    """Write one timestamped dump. Returns the path written."""
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    env = _psql_env()

    if compress:
        final = dest / f"talyn-{stamp}.dump.gz"
        tmp = dest / f".{stamp}.partial"
        try:
            with tmp.open("wb") as out:
                result = _run(["pg_dump", "--format=custom", "--no-owner",
                               "--no-acl", "--compress=6"], env, stdout=out)
            if result.returncode != 0:
                raise RuntimeError(f"pg_dump failed: {(result.stderr or b'').decode()[:400]}")
            with tmp.open("rb") as src, gzip.open(final, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        finally:
            # Always clear the scratch file, including on an exception: a
            # leftover .partial is what a killed run leaves behind, and
            # letting failures add more of them buries the real dumps.
            tmp.unlink(missing_ok=True)
        return final

    final = dest / f"talyn-{stamp}.dump"
    with final.open("wb") as out:
        result = _run(["pg_dump", "--format=custom", "--no-owner",
                       "--no-acl"], env, stdout=out)
    if result.returncode != 0:
        final.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {(result.stderr or b'').decode()[:400]}")
    return final


def _materialise(path: Path, workdir: Path) -> Path:
    """Return a plain, seekable file for pg_restore.

    pg_restore cannot read a custom-format archive from a pipe — it seeks
    within the file, and reports "too short" for a stream. So a gzipped dump
    is expanded to a real file first. Only used for verification, and the
    copy is deleted straight after.
    """
    if path.suffix != ".gz":
        return path
    plain = workdir / (path.stem.removesuffix(".dump") + ".verify.dump")
    with gzip.open(path, "rb") as src, plain.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    return plain


def table_counts(env: dict, database: str) -> dict[str, int]:
    """Row counts for the verified tables in one round trip."""
    query = (
        "SELECT 'users', count(*) FROM users "
        "UNION ALL SELECT 'courses', count(*) FROM courses "
        "UNION ALL SELECT 'lessons', count(*) FROM lessons "
        "UNION ALL SELECT 'enrollments', count(*) FROM enrollments "
        "UNION ALL SELECT 'payments', count(*) FROM payments "
        "UNION ALL SELECT 'xp_events', count(*) FROM xp_events "
        "UNION ALL SELECT 'lesson_progress', count(*) FROM lesson_progress "
        "UNION ALL SELECT 'lesson_assets', count(*) FROM lesson_assets "
        "UNION ALL SELECT 'email_log', count(*) FROM email_log"
    )
    env = {**env, "PGDATABASE": database}
    result = _run(["psql", "--tuples-only", "--no-align", "--command", query], env)
    if result.returncode != 0:
        raise RuntimeError(f"psql failed: {result.stderr.decode()[:300]}")
    counts: dict[str, int] = {}
    for line in result.stdout.decode().splitlines():
        parts = line.split("|")
        if len(parts) == 2 and parts[0].strip():
            counts[parts[0].strip()] = int(parts[1].strip())
    return counts


def verify(dump: Path, scratch_db: str) -> tuple[bool, str]:
    """Restore into a scratch database and compare it to the live one.

    This is the whole point. Without it a dump is an assumption.
    """
    env = _psql_env()
    live_db = env["PGDATABASE"]

    # Start from empty: a leftover database would silently pass the compare.
    drop = _run(["dropdb", "--if-exists", scratch_db], env)
    create = _run(["createdb", scratch_db], env)
    if create.returncode != 0:
        return False, f"could not create scratch db: {create.stderr.decode()[:200]}"

    try:
        with tempfile.TemporaryDirectory(prefix="talyn-restore-") as workdir:
            archive = _materialise(dump, Path(workdir))
            result = _run(["pg_restore", "--no-owner", "--no-acl",
                           "--dbname", scratch_db, str(archive)], env,
                          timeout=1800)
        if result.returncode != 0:
            return False, f"pg_restore failed: {(result.stderr or b'').decode()[:300]}"

        live = table_counts(env, live_db)
        restored = table_counts(env, scratch_db)
        mismatches = [
            f"{name}: live {live.get(name, 0)} != restored {restored.get(name, 0)}"
            for name in VERIFY_TABLES
            if live.get(name, 0) != restored.get(name, 0)
        ]
        if mismatches:
            return False, "row counts differ - " + "; ".join(mismatches)

        non_empty = sum(restored.values())
        return True, f"restored {len(restored)} tables, {non_empty} rows"
    finally:
        _run(["dropdb", "--if-exists", scratch_db], env)


def prune(dest: Path, keep: int) -> list[Path]:
    """Delete dumps beyond the newest `keep`. Returns what was removed."""
    dumps = sorted(dest.glob("talyn-*.dump*"), key=lambda p: p.name, reverse=True)
    removed: list[Path] = []
    for stale in dumps[keep:]:
        try:
            stale.unlink()
            removed.append(stale)
        except OSError:
            pass
    # Partial files from an interrupted run are not backups.
    for partial in dest.glob(".*.partial"):
        try:
            partial.unlink()
        except OSError:
            pass
    return removed


def write_manifest(dest: Path, entries: list[DumpResult]) -> None:
    """A plain-text index, so a human can tell what exists without tooling."""
    lines = ["# Talyn database backups", ""]
    for entry in entries:
        state = "verified" if entry.verified else f"UNVERIFIED ({entry.verification_note})"
        lines.append(f"{entry.path.name}  {entry.size_bytes} bytes  sha256:{entry.sha256[:16]}  {state}")
    lines.append("")
    lines.append(f"Written {time.strftime('%Y-%m-%d %H:%M:%S')}")
    (dest / "MANIFEST.txt").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                        help="Where to write dumps (default: ./backups or $BACKUP_DIR)")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help="How many dumps to retain")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the restore rehearsal (faster, weaker)")
    parser.add_argument("--no-compress", action="store_true",
                        help="Write plain .dump instead of .dump.gz")
    parser.add_argument("--scratch-db", default="talyn_restore_check",
                        help="Scratch database used for the rehearsal")
    args = parser.parse_args(argv)

    try:
        dump_path = create_dump(args.dest, compress=not args.no_compress)
    except Exception as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        return 1

    if dump_path.stat().st_size == 0:
        dump_path.unlink(missing_ok=True)
        print("BACKUP FAILED: dump was empty", file=sys.stderr)
        return 1

    digest = _sha256(dump_path)
    verified, note = (False, "skipped") if args.no_verify else verify(
        dump_path, args.scratch_db
    )
    result = DumpResult(
        path=dump_path,
        sha256=digest,
        size_bytes=dump_path.stat().st_size,
        verified=verified,
        verification_note=note,
    )

    if not args.no_verify and not verified:
        # A dump that cannot be restored is worse than no dump: it looks like
        # safety on disk. Remove it so the count reflects reality.
        dump_path.unlink(missing_ok=True)
        print(f"BACKUP FAILED verification: {note}", file=sys.stderr)
        print(f"Removed {dump_path.name}; it could not be restored.", file=sys.stderr)
        return 1

    removed = prune(args.dest, args.keep)
    existing = sorted(args.dest.glob("talyn-*.dump*"))
    write_manifest(args.dest, [DumpResult(
        p, _sha256(p), p.stat().st_size,
        p.name == dump_path.name and verified, note if p.name == dump_path.name else ""
    ) for p in existing])

    print(f"wrote   {dump_path.name}  ({result.size_bytes} bytes)")
    print(f"sha256  {digest}")
    print(f"verify  {note}")
    if removed:
        print(f"pruned  {len(removed)} old dump(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
