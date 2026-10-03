"""Verify object storage works before deploying.

Credentials that look fine fail in specific, confusing ways: a wrong region
raises SignatureDoesNotMatch, a missing IAM permission fails only on the
one operation that needs it, and a bucket without the right CORS rule accepts
server-side writes while every browser upload dies with an opaque CORS error.

None of those are obvious from "the env vars are filled in". This script
exercises the paths the app actually uses, then deletes everything it made.

    python -m scripts.check_storage

Exits non-zero if any check fails, so it is usable as a deploy gate.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services.storage import (  # noqa: E402
    StorageError,
    head_object,
    presigned_upload,
)

CONTENT = b"talyn storage check - safe to delete"


def _line(label: str, value: str) -> None:
    print(f"  {label:<22} {value}")


def main() -> int:
    print("Configuration")
    _line("S3_BUCKET", settings.s3_bucket or "(empty - storage disabled)")
    _line("S3_ENDPOINT", settings.s3_endpoint or "aws (real S3)")
    _line("S3_REGION", settings.s3_region)
    _line("S3_ACCESS_KEY", "set" if settings.s3_access_key else "(unset - falls back to instance role)")
    _line("S3_SECRET_KEY", "set" if settings.s3_secret_key else "(unset - falls back to instance role)")
    _line("encryption", settings.s3_server_side_encryption or "(off)")

    if not settings.s3_bucket:
        print("\nFAILED: S3_BUCKET is empty, so every upload route is disabled.")
        return 1

    failures: list[str] = []

    # 1. Credentials and bucket access. Also the only check that tells you the
    #    region is right - a wrong region fails here with SignatureDoesNotMatch
    #    rather than anywhere later.
    key = f"_storagecheck/{uuid.uuid4().hex}.txt"
    try:
        from app.services.storage import _client

        client, bucket = _client()
        client.put_object(
            Bucket=bucket, Key=key, Body=CONTENT,
            **(
                {"ServerSideEncryption": settings.s3_server_side_encryption}
                if settings.s3_server_side_encryption
                else {}
            ),
        )
        print("\n  write                        OK")
    except Exception as exc:
        print(f"\n  write                        FAILED: {type(exc).__name__}: {exc}")
        return 1

    # 2. The read-back the app performs when confirming an upload.
    try:
        meta = head_object(key)
        _line("read back", f"OK ({meta.get('ContentLength')} bytes)")
        if settings.s3_server_side_encryption:
            got = meta.get("ServerSideEncryption")
            _line("encryption on object", got or "(not reported by provider)")
    except StorageError as exc:
        print(f"  read back                    FAILED: {exc}")
        failures.append("head_object")

    # 3. The presigned POST. This is the one that matters most: browsers post
    #    straight to the provider, so a policy the backend can satisfy may
    #    still be rejected by the browser. Printing the host and fields makes
    #    an obvious mismatch (wrong endpoint, missing CORS) visible by eye.
    try:
        form = presigned_upload(key, "text/plain", max_bytes=1024)
        print("\n  presigned POST               OK")
        _line("  host", form["upload_url"].split("?")[0])
        for name in sorted(form["fields"]):
            if name in ("key", "policy", "x-amz-signature"):
                continue
            value = form["fields"][name]
            print(f"    field {name:<18} {value}")
        if settings.s3_server_side_encryption and not any(
            k.lower() == "x-amz-server-side-encryption" for k in form["fields"]
        ):
            failures.append("presigned POST is missing the SSE header")
    except StorageError as exc:
        print(f"\n  presigned POST               FAILED: {exc}")
        failures.append("presigned_upload")

    # 4. Clean up. A leftover key here is harmless (it is not server-generated
    #    so the orphan sweep will ignore it), but leaving litter in the bucket
    #    makes real orphans harder to spot later.
    try:
        client.delete_object(Bucket=bucket, Key=key)
        print("\n  cleanup                      OK")
    except Exception as exc:
        print(f"\n  cleanup                      FAILED: {exc}")

    if settings.s3_server_side_encryption:
        print(
            "\n  NOTE: uploads set x-amz-server-side-encryption. The bucket CORS\n"
            "  rule must allow that header, or browsers fail every upload even\n"
            "  though the backend-side checks above all passed."
        )

    if failures:
        print(f"\nFAILED: {', '.join(failures)}")
        return 1

    print("\nAll storage checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())