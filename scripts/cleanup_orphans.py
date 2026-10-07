"""Delete uploaded objects that were never attached to anything.

Every presign mints a key, and most keys never get used: the creator closes
the tab, picks a different file, or the upload fails halfway. Those objects
cost storage forever, because nothing in the app references them.

This compares the bucket against the database and removes anything the app
minted that no course, asset, or profile points at, once it is older than
ORPHAN_UPLOAD_TTL_HOURS.

    python -m scripts.cleanup_orphans              # delete what is expired
    python -m scripts.cleanup_orphans --dry-run    # report only
    python -m scripts.cleanup_orphans --max-age 1  # for testing

Safety: only keys matching the server-generated pattern are ever considered,
so a backup or anything else sharing the bucket is left alone. Deleting an
unreferenced object is reversible only by re-upload, so it defaults to a
dry run when a bucket name is not passed explicitly.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import Course, CreatorProfile, LearnerMaterial, LessonAsset
from app.services import storage

BUCKETS = ("thumbnail", "video", "resource", "profile_image")


def referenced_keys(db) -> set[str]:
    """Every storage key the application currently points at.

    Uses scalars(), not query(). db.query(Column) yields Row objects rather
    than the column values, so a set built from them compares unequal to any
    plain string key — which would make every attached file look unreferenced
    and get it deleted.
    """
    from sqlalchemy import select

    keys: set[str] = set()
    keys.update(
        db.scalars(select(Course.thumbnail_key).where(Course.thumbnail_key.isnot(None)))
    )
    keys.update(
        db.scalars(
            select(CreatorProfile.image_key).where(CreatorProfile.image_key.isnot(None))
        )
    )
    keys.update(
        db.scalars(
            select(LessonAsset.storage_key).where(LessonAsset.storage_key.isnot(None))
        )
    )
    keys.update(
        db.scalars(
            select(LearnerMaterial.storage_key).where(
                LearnerMaterial.storage_key.isnot(None)
            )
        )
    )
    return {k for k in keys if k}


def age_in_hours(last_modified: datetime | None) -> float:
    if last_modified is None:
        # No timestamp means we cannot prove it is old. Treating unknown as
        # ancient would delete objects the provider cannot describe.
        return -1.0
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - last_modified
    return delta.total_seconds() / 3600.0


def find_orphans(db, client, bucket: str, max_age_hours: float) -> tuple[list, list]:
    """Split unreferenced objects into (old enough to delete, still fresh)."""
    referenced = referenced_keys(db)
    expired: list = []
    fresh: list = []

    response = client.list_objects_v2(Bucket=bucket)
    for item in response.get("Contents", []):
        key = item.get("Key")
        if not key or key in referenced:
            continue
        # Never touch anything this app did not mint.
        if not storage.is_server_generated(key):
            continue
        age = age_in_hours(item.get("LastModified"))
        (expired if age >= max_age_hours else fresh).append((key, age))
    return expired, fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted, delete nothing.")
    parser.add_argument("--max-age", type=float, default=None,
                        help="Hours old before deletion (default: config value).")
    parser.add_argument("--bucket", default=None,
                        help="Bucket name (default: S3_BUCKET from .env).")
    args = parser.parse_args(argv)

    from app.config import settings

    bucket = args.bucket or settings.s3_bucket
    if not bucket:
        print("No bucket configured (S3_BUCKET is empty).", file=sys.stderr)
        return 2

    max_age = args.max_age if args.max_age is not None else settings.orphan_upload_ttl_hours
    db = SessionLocal()
    try:
        s3, _ = storage._client()
        expired, fresh = find_orphans(db, s3, bucket, max_age)
    except storage.StorageError as e:
        print(f"Storage error: {e}", file=sys.stderr)
        return 2
    finally:
        db.close()

    for key, age in fresh:
        print(f"  keep   {key}  ({age:.1f}h old)")
    for key, age in expired:
        if args.dry_run:
            print(f"  WOULD DELETE  {key}  ({age:.1f}h old)")
        elif s3.delete_object(Bucket=bucket, Key=key):
            print(f"  deleted  {key}  ({age:.1f}h old)")
        else:
            print(f"  FAILED   {key}  ({age:.1f}h old)", file=sys.stderr)

    verb = "would delete" if args.dry_run else "deleted"
    print(f"\n{len(expired)} {verb}, {len(fresh)} kept as too recent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
