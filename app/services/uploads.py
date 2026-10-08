"""Upload lifecycle: presign, verify, scan, attach.

The problem this solves: with a bare presigned PUT, the server has no idea
what a client actually uploaded. `size_bytes` on an asset is whatever the
client claimed, nothing stops a 50 GB file, and nothing checks what is in
it.

The flow, and what each step is defending against:

  1. `presign`  - validates the declared type and extension, then mints a
      presigned PUT URL. R2 does not implement POST Object, so no policy
      can enforce the cap at upload time; it travels alongside as
      `max_bytes` for the client to pre-check, honestly advisory.
  2. `verify`   - asks the provider for the real `ContentLength`. The
      recorded size is what landed, not what was claimed, and over-sized
      objects are deleted here: this step, not the upload, is what makes
      the cap real.
  3. `scan`     — ClamAV verdict, if a scanner is configured. Infected files
     are deleted from storage and refused.
  4. `attach`   — links the verified object to a lesson.

Steps 2 and 3 are separate from attach on purpose: they run once, when the
object is claimed, rather than every time the asset is read.
"""
import logging
from dataclasses import dataclass

from app import config as config_module
from app.services import scanner, storage
from app.services.storage import UploadRejected

log = logging.getLogger("talyn.uploads")

# Scanned objects up to this size are downloaded and scanned. Above it, a
# video is large enough that pulling it into memory is its own problem.
MAX_SCAN_BYTES = 25 * 1024 * 1024

SCAN_CLEAN = "clean"
SCAN_INFECTED = "infected"
SCAN_UNSCANNED = "unscanned"
SCAN_FAILED = "failed"


@dataclass
class VerifiedUpload:
    """What the provider says about an object, plus its scan verdict."""

    size_bytes: int
    content_type: str
    scan_status: str
    scan_detail: str

    @property
    def safe_to_attach(self) -> bool:
        return self.scan_status in (SCAN_CLEAN, SCAN_UNSCANNED)


def presign(purpose: str, filename: str, content_type: str) -> dict:
    """Validate and mint an upload form. Raises UploadRejected/StorageError."""
    storage.validate_upload(purpose, filename, content_type)
    key = storage.build_key(purpose, filename)
    max_bytes = config_module.settings.max_upload_bytes(purpose)
    return storage.presigned_upload(key, content_type, max_bytes)


def claim(key: str, purpose: str) -> VerifiedUpload:
    """Verify an object before it is referenced by a course or profile.

    Thumbnails and profile images are not lesson assets, so they never pass
    through attach_asset. Without this they would be the easiest way to get an
    unchecked file in front of every learner on a discovery page, so they get
    the same size and scan treatment.

    `purpose` must match the key's prefix: a video key cannot be attached as a
    thumbnail just because it happens to exist.
    """
    if not key:
        raise UploadRejected("No file was provided")
    prefix = key.split("/", 1)[0] if "/" in key else ""
    if prefix != purpose:
        raise UploadRejected(
            f"That file was uploaded for a different use. "
            f"Expected a {purpose.replace('_', ' ')} file."
        )
    return verify(key)


def verify(key: str) -> VerifiedUpload:
    """Confirm the object exists, is within its cap, and is safe to attach.

    Deletes the object and raises UploadRejected when it must not be
    attached — over-sized, empty, or infected. Leaving a rejected object in
    the bucket would let a creator retry the same file forever.
    """
    limit_key = key.split("/", 1)[0] if "/" in key else ""
    max_bytes = config_module.settings.max_upload_bytes(limit_key)

    try:
        head = storage.head_object(key)
    except storage.StorageError as e:
        raise UploadRejected(
            "That upload did not complete. Please try again."
        ) from e

    size = int(head.get("ContentLength") or 0)
    actual_type = str(head.get("ContentType") or "")

    # The provider already enforced this on the POST policy, so a breach here
    # means something bypassed the form (a raw PUT, or a stale policy).
    if size <= 0:
        storage.delete_object(key)
        raise UploadRejected("That upload was empty. Please try again.")
    if size > max_bytes:
        storage.delete_object(key)
        raise UploadRejected(
            f"That file is too large ({_human(size)}). "
            f"The limit here is {_human(max_bytes)}."
        )

    scan_status, scan_detail = _scan(key, size)
    if scan_status == SCAN_INFECTED:
        # Delete before refusing: an infected object has no reason to persist
        # even briefly, and the creator has no way to clear it themselves.
        storage.delete_object(key)
        raise UploadRejected(
            f"That file was rejected by our virus scanner ({scan_detail}). "
            f"If you believe this is wrong, contact support."
        )

    if scan_status == SCAN_FAILED and config_module.settings.upload_scanning_required:
        # Strict mode was asked for and the scanner could not give an answer.
        # Accepting it anyway would make the setting a lie.
        storage.delete_object(key)
        raise UploadRejected(
            "We could not check that file for viruses right now, so it was not "
            "accepted. Please try again in a few minutes."
        )

    return VerifiedUpload(
        size_bytes=size,
        content_type=actual_type,
        scan_status=scan_status,
        scan_detail=scan_detail,
    )


def _scan(key: str, size: int) -> tuple[str, str]:
    """Run the scanner, mapping its verdicts onto ours."""
    if not scanner.available():
        # Recorded as unscanned rather than clean. Later, an operator can
        # tell the difference between "looked at, fine" and "never looked at".
        if config_module.settings.upload_scanning_required:
            return SCAN_FAILED, "scanner required but not configured"
        return SCAN_UNSCANNED, "no scanner configured"

    if size > MAX_SCAN_BYTES:
        # Too big to pull into memory. Not silently treated as clean.
        return SCAN_FAILED, f"file exceeds the {_human(MAX_SCAN_BYTES)} scan limit"

    try:
        data = storage.download_bytes(key, MAX_SCAN_BYTES)
    except storage.StorageError as e:
        return SCAN_FAILED, f"could not read object for scanning: {e}"

    verdict, detail = scanner.scan_bytes(data)
    if verdict is scanner.Verdict.CLEAN:
        return SCAN_CLEAN, detail
    if verdict is scanner.Verdict.INFECTED:
        return SCAN_INFECTED, detail
    if verdict is scanner.Verdict.UNAVAILABLE:
        # A scanner outage is not a malware verdict. Refuse only if the
        # operator asked for strictness.
        if config_module.settings.upload_scanning_required:
            return SCAN_FAILED, f"scanner unavailable: {detail}"
        return SCAN_UNSCANNED, f"scanner unavailable: {detail}"
    return SCAN_FAILED, detail


def _human(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"
