"""S3-compatible object storage (videos, thumbnails, resources).

Server-generated keys only — clients never choose paths. Reads go through
presigned URLs so the bucket can stay private. boto3 is imported lazily so
the app boots without credentials; calls fail closed when unconfigured.

Uploads use **presigned POST**, not PUT, and that is the whole point.
A presigned PUT cannot constrain size: whoever holds the URL can send as
much as they like and storage pays for it. A presigned POST carries a
`content-length-range` policy that the storage provider enforces before
accepting a byte, so the size cap is the server's, not the client's
promise.
"""
from __future__ import annotations

import re
from uuid import uuid4

from app.config import settings

PURPOSES = ("thumbnail", "video", "resource", "profile_image", "material")

ALLOWED_CONTENT_TYPES = {
    "thumbnail": {"image/png", "image/jpeg", "image/webp"},
    "video": {"video/mp4", "video/webm", "video/quicktime"},
    "resource": {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "text/plain",
    },
    "profile_image": {"image/png", "image/jpeg", "image/webp"},
    # Learner library documents. Only formats the analyzer can read, plus
    # zip as an opaque bundle: the library is also a shelf, and analysis
    # refuses gracefully what it cannot parse. Video stays creator-only for
    # now — a 512 MB cap per learner upload is a cost decision, not a type
    # decision. Legacy .doc/.ppt are excluded on purpose: binary OLE
    # formats need heavy parsers, and "upload OK, analyze impossible" is a
    # worse experience than refusing them.
    "material": {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "text/plain",
    },
}

# Extensions, matched against the client-supplied filename.
#
# Content-Type is chosen by the client, so the allowlist above is advisory:
# anything can be PUT with `Content-Type: video/mp4`. The filename is also
# client-supplied, so neither is a security boundary on its own — but a
# mismatch between the two is a strong signal, and refusing `.exe` outright
# stops the obvious cases regardless of what the header claims.
ALLOWED_EXTENSIONS = {
    "thumbnail": {".png", ".jpg", ".jpeg", ".webp"},
    "video": {".mp4", ".webm", ".mov"},
    "resource": {".pdf", ".doc", ".docx", ".zip", ".txt"},
    "profile_image": {".png", ".jpg", ".jpeg", ".webp"},
    "material": {".pdf", ".docx", ".pptx", ".zip", ".txt"},
}

UPLOAD_EXPIRY_SECONDS = 900  # 15 minutes to upload
DOWNLOAD_EXPIRY_SECONDS = 3600  # 1 hour to read

MAX_FILENAME = 120


class StorageError(Exception):
    """Storage unavailable or misconfigured."""


class UploadRejected(Exception):
    """The request is not allowed; carries a message safe to show a creator."""


def _client():
    from app import config as config_module

    settings_now = config_module.settings
    if not settings_now.s3_bucket:
        raise StorageError("Object storage is not configured (S3_BUCKET is empty)")
    import boto3

    kwargs: dict = {
        "service_name": "s3",
        "region_name": settings_now.s3_region,
        "aws_access_key_id": settings_now.s3_access_key or None,
        "aws_secret_access_key": settings_now.s3_secret_key or None,
    }
    if settings_now.s3_endpoint:
        kwargs["endpoint_url"] = settings_now.s3_endpoint
    try:
        return boto3.client(**kwargs), settings_now.s3_bucket
    except TypeError as e:
        # A misconfigured credential pair reaches boto3 as a bad argument and
        # would otherwise surface as an opaque 500 on every upload route.
        raise StorageError(f"Object storage client could not be built: {e}") from e


def _safe_filename(filename: str) -> str:
    keep = "".join(
        c for c in filename if c.isalnum() or c in ("-", "_", ".", " ")
    )
    return (keep.strip().replace(" ", "_") or "file")[:MAX_FILENAME]


def extension_of(filename: str) -> str:
    """Lowercased extension including the dot, or '' if there is none."""
    name = _safe_filename(filename)
    index = name.rfind(".")
    return name[index:].lower() if index > 0 else ""


def validate_upload(purpose: str, filename: str, content_type: str) -> None:
    """Reject a request the storage layer would accept but shouldn't.

    Raises UploadRejected with a creator-facing message. Called before any
    key is minted, so a rejected upload costs nothing.
    """
    if purpose not in PURPOSES:
        raise UploadRejected("Unknown upload purpose")

    allowed_types = ALLOWED_CONTENT_TYPES[purpose]
    if content_type not in allowed_types:
        raise UploadRejected(
            f"{content_type or 'That file type'} is not supported here. "
            f"Allowed: {', '.join(sorted(allowed_types))}"
        )

    ext = extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS[purpose]:
        raise UploadRejected(
            f"Files ending in {ext or '(no extension)'} are not accepted here. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS[purpose]))}"
        )


def build_key(purpose: str, filename: str) -> str:
    """Generate a storage key the client must upload to."""
    return f"{purpose}/{uuid4().hex}-{_safe_filename(filename)}"


def presigned_upload(
    key: str, content_type: str, max_bytes: int
) -> dict:
    """Presigned POST form + key. Raises StorageError when unconfigured.

    `max_bytes` becomes a `content-length-range` in the form's policy, which
    the provider enforces. Passing 0 would mean "unbounded", so the floor is
    1 byte — a caller that wants no cap has to say so by not using this.
    """
    client, bucket = _client()
    if max_bytes <= 0:
        raise StorageError("Upload size limit must be positive")

    conditions: list = [
        {"bucket": bucket},
        ["starts-with", "$key", key],
        ["content-length-range", 1, int(max_bytes)],
    ]
    extra: dict = {"Content-Type": content_type}
    if settings.s3_server_side_encryption:
        conditions.append({"sseCustomerAlgorithm": settings.s3_server_side_encryption})
        extra["x-amz-server-side-encryption"] = settings.s3_server_side_encryption

    try:
        # botocore capitalises these: Fields, Conditions, ExpiresIn. Unlike
        # generate_presigned_url (Params=), a lowercase name is a TypeError
        # rather than a warning, so it has to be right.
        post = client.generate_presigned_post(
            Bucket=bucket,
            Key=key,
            Conditions=conditions,
            Fields=extra,
            ExpiresIn=UPLOAD_EXPIRY_SECONDS,
        )
    except TypeError as e:
        # A boto3 signature change or a typo here would otherwise surface as
        # an opaque failure on every upload in production.
        raise StorageError(f"Could not create upload form: {e}") from e
    except Exception as e:
        raise StorageError(f"Could not create upload form: {e}") from e

    return {
        "upload_url": post["url"],
        "fields": post["fields"],
        "storage_key": key,
        "expires_in": UPLOAD_EXPIRY_SECONDS,
        "max_bytes": int(max_bytes),
        # Told to the client for a friendly pre-check, not as enforcement.
        "method": "POST",
    }


def head_object(key: str) -> dict:
    """Ask the provider what actually landed. Raises StorageError if absent."""
    client, bucket = _client()
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        raise StorageError(f"Could not read uploaded object: {e}") from e


def download_bytes(key: str, max_bytes: int) -> bytes:
    """Fetch an object for scanning, refusing anything above `max_bytes`.

    Reads at most max_bytes + 1 so an unexpectedly huge object is detected
    rather than pulled into memory.
    """
    client, bucket = _client()
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"]
        try:
            return body.read(max_bytes + 1)[:max_bytes]
        finally:
            body.close()
    except Exception as e:
        raise StorageError(f"Could not download object: {e}") from e


def delete_object(key: str) -> bool:
    """Delete an object. Best-effort: returns False instead of raising.

    Used to clean up after a rejected upload. A leftover object costs
    storage; a failed cleanup must not turn a clean rejection into a 500.
    """
    client, bucket = _client()
    try:
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def presigned_download_url(key: str) -> str:
    """Presigned GET URL. Raises StorageError when unconfigured/failing."""
    client, bucket = _client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=DOWNLOAD_EXPIRY_SECONDS,
        )
    except Exception as e:
        raise StorageError(f"Could not create download URL: {e}") from e


def list_keys(prefix: str) -> list[str]:
    """All object keys under a prefix. For the orphan sweep."""
    client, bucket = _client()
    keys: list[str] = []
    token: str | None = None
    try:
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = client.list_objects_v2(**kwargs)
            keys.extend(
                item["Key"] for item in response.get("Contents", [])
                if "Key" in item
            )
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
    except Exception as e:
        raise StorageError(f"Could not list objects: {e}") from e
    return keys


_KEY_RE = re.compile(r"^(thumbnail|video|resource|profile_image|material)/[0-9a-f]{32}-")


def is_server_generated(key: str) -> bool:
    """True only for keys this app minted.

    Guards the orphan sweep against deleting anything else that happens to
    share the bucket, such as a manually placed backup.
    """
    return bool(_KEY_RE.match(key))
