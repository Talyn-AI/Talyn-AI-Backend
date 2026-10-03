"""Upload hardening: size caps, verified sizes, malware scanning, orphans.

S3 and ClamAV are both faked at their boundaries, so these tests exercise the
real policy maths, the real scan framing, and the real DB writes with no
network.
"""
import hashlib
import json
import socket
import threading

import pytest

from app import config as config_module
from app.services import scanner, storage
from app.services import uploads as upload_service


# ── Fake S3 ──────────────────────────────────────────────────────────────────


class FakeS3:
    """Minimal S3 stand-in recording what the service asked for."""

    def __init__(self):
        self.objects: dict = {}       # key -> {ContentLength, ContentType, Body}
        self.post_conditions: list = []
        self.deleted: list[str] = []

    def generate_presigned_post(self, Bucket, Key, Conditions, Fields=None,
                                ExpiresIn=None):
        self.post_conditions = Conditions
        return {
            "url": f"https://{Bucket}.s3.test/{Key}",
            "fields": {"key": Key, "policy": "signed", **(Fields or {})},
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404 Not Found")
        return self.objects[Key]

    def get_object(self, Bucket, Key):
        record = self.objects[Key]
        import io

        return {"Body": io.BytesIO(record.get("Body", b""))}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, Bucket, Prefix="", **kwargs):
        return {
            "Contents": [
                {"Key": k, "LastModified": v["LastModified"]}
                for k, v in self.objects.items()
                if k.startswith(Prefix)
            ],
            "IsTruncated": False,
        }


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: (fake, "talyn-test"))
    monkeypatch.setattr(config_module.settings, "s3_bucket", "talyn-test")
    return fake


def _put(s3, key, size=1024, content_type="application/pdf"):
    from datetime import datetime, timezone

    s3.objects[key] = {
        "ContentLength": size,
        "ContentType": content_type,
        "Body": b"x" * min(size, 4096),
        "LastModified": datetime.now(timezone.utc),
    }
    return key


# ── Fake ClamAV ──────────────────────────────────────────────────────────────


class FakeClamAV:
    """Speaks enough of the clamd INSTREAM protocol to be believed."""

    def __init__(self, reply="stream: OK"):
        self.reply = reply
        self.received = b""
        self.server = None
        self.thread = None

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self.port

    def _serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                # Command first: "zINSTREAM\0". A single recv can return the
                # command *and* the start of the chunk stream, so keep the
                # remainder rather than discarding it.
                buffer = b""
                while b"\0" not in buffer:
                    block = conn.recv(64)
                    if not block:
                        return
                    buffer += block
                _, _, buffer = buffer.partition(b"\0")

                # Then chunks: <len>\r\n<data>...\r\n, terminated by "0\r\n".
                while True:
                    while not buffer.endswith(b"\r\n"):
                        block = conn.recv(64)
                        if not block:
                            return
                        buffer += block
                    header, _, buffer = buffer.partition(b"\r\n")
                    length = int(header)
                    if length == 0:
                        break
                    while len(buffer) < length:
                        buffer += conn.recv(length - len(buffer))
                    self.received += buffer[:length]
                    buffer = buffer[length:]

                conn.sendall(self.reply.encode() + b"\0")
            except (OSError, ValueError):
                pass

    def stop(self):
        if self.server:
            self.server.close()


@pytest.fixture
def clamav(monkeypatch):
    """A running clamd stand-in. `clamav.reply` controls the verdict."""
    servers: list = []

    def start(reply="stream: OK"):
        server = FakeClamAV(reply)
        servers.append(server)
        port = server.start()
        monkeypatch.setattr(config_module.settings, "clamav_host", "127.0.0.1")
        monkeypatch.setattr(config_module.settings, "clamav_port", port)
        return server

    yield start
    for server in servers:
        server.stop()


# ── Presign: the size cap must be the server's ───────────────────────────────


def test_presign_enforces_size_in_the_post_policy(client, s3, creator_headers):
    """A presigned PUT cannot cap size; the POST policy can."""
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "resource", "filename": "notes.pdf",
              "content_type": "application/pdf"},
        headers=creator_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["method"] == "POST"
    ranges = [
        c for c in s3.post_conditions
        if isinstance(c, list) and c[0] == "content-length-range"
    ]
    assert ranges, "no content-length-range in the signed policy"
    assert ranges[0][2] == config_module.settings.max_resource_bytes


def test_presign_rejects_oversized_declaration_without_minting_a_key(
    client, s3, creator_headers
):
    before = len(s3.objects)
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "lecture.mp4",
              "content_type": "video/mp4",
              "size_bytes": config_module.settings.max_video_bytes + 1},
        headers=creator_headers,
    )
    assert r.status_code == 422
    assert "too large" in r.json()["detail"].lower()
    assert len(s3.objects) == before  # nothing created


def test_presign_rejects_disallowed_content_type(client, s3, creator_headers):
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "payload.mp4",
              "content_type": "application/x-msdownload"},
        headers=creator_headers,
    )
    assert r.status_code == 422


def test_presign_rejects_executable_extension_regardless_of_content_type(
    client, s3, creator_headers
):
    """Content-Type is client-chosen, so the extension is checked too."""
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "totally-a-video.mp4.exe",
              "content_type": "video/mp4"},
        headers=creator_headers,
    )
    assert r.status_code == 422
    assert ".exe" in r.json()["detail"]


def test_presign_rejects_disallowed_extension(client, s3, creator_headers):
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "resource", "filename": "malware.pdf.exe",
              "content_type": "application/pdf"},
        headers=creator_headers,
    )
    assert r.status_code == 422


def test_presign_requires_auth(client, s3):
    r = client.post("/v1/uploads/presigned",
                    json={"purpose": "resource", "filename": "a.pdf",
                          "content_type": "application/pdf"})
    assert r.status_code == 401


def test_presign_requires_creator(client, s3, learner_headers):
    r = client.post("/v1/uploads/presigned",
                    json={"purpose": "resource", "filename": "a.pdf",
                          "content_type": "application/pdf"},
                    headers=learner_headers)
    assert r.status_code == 403


# ── Attach: the recorded size is the truth, not the claim ────────────────────


def test_attach_records_the_storage_size_not_the_claim(
    client, s3, creator_headers, lesson_id
):
    """The client no longer gets to say how big its file was."""
    key = _put(s3, f"resource/{'a' * 32}-notes.pdf", size=4096)

    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "notes.pdf"},
                    headers=creator_headers)
    assert r.status_code == 201, r.text
    assert r.json()["size_bytes"] == 4096


def test_attach_refuses_object_over_the_cap_and_deletes_it(
    client, s3, creator_headers, lesson_id, monkeypatch
):
    """A raw PUT bypassing the form still cannot slip an oversize file through."""
    monkeypatch.setattr(config_module.settings, "max_resource_bytes", 1024)
    key = _put(s3, f"resource/{'b' * 32}-huge.pdf", size=99_999)

    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "huge.pdf"},
                    headers=creator_headers)
    assert r.status_code == 422
    assert "too large" in r.json()["detail"].lower()
    assert key in s3.deleted  # not left to cost storage


def test_attach_refuses_empty_object(client, s3, creator_headers, lesson_id):
    key = _put(s3, f"resource/{'c' * 32}-empty.pdf", size=0)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "empty.pdf"},
                    headers=creator_headers)
    assert r.status_code == 422
    assert key in s3.deleted


def test_attach_refuses_object_that_was_never_uploaded(
    client, s3, creator_headers, lesson_id
):
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource",
                          "storage_key": f"resource/{'d' * 32}-ghost.pdf",
                          "filename": "ghost.pdf"},
                    headers=creator_headers)
    assert r.status_code == 422
    assert "did not complete" in r.json()["detail"].lower()


def test_attach_still_rejects_key_from_another_purpose(
    client, s3, creator_headers, lesson_id
):
    _put(s3, f"video/{'e' * 32}-clip.mp4", size=100)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource",
                          "storage_key": f"video/{'e' * 32}-clip.mp4"},
                    headers=creator_headers)
    assert r.status_code == 422


def test_attach_recovers_filename_from_key_when_omitted(
    client, s3, creator_headers, lesson_id
):
    key = _put(s3, f"resource/{'f' * 32}-quarterly-notes.pdf", size=100)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key},
                    headers=creator_headers)
    assert r.json()["filename"] == "quarterly-notes.pdf"


# ── Scanning ─────────────────────────────────────────────────────────────────


def test_clean_scan_is_recorded(client, s3, clamav, creator_headers, lesson_id):
    clamav("stream: OK")
    key = _put(s3, f"resource/{'1' * 32}-safe.pdf", size=2048)

    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "safe.pdf"},
                    headers=creator_headers)
    assert r.status_code == 201
    assert r.json()["scan_status"] == "clean"


def test_infected_file_is_refused_and_deleted(
    client, s3, clamav, creator_headers, lesson_id
):
    server = clamav("stream: Win.Test.EICAR_HDB-1 FOUND")
    key = _put(s3, f"resource/{'2' * 32}-bad.pdf", size=2048)

    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "bad.pdf"},
                    headers=creator_headers)
    assert r.status_code == 422
    assert "virus scanner" in r.json()["detail"].lower()
    assert "EICAR" in r.json()["detail"]
    assert key in s3.deleted
    assert server.received  # the bytes really were sent


def test_scanner_receives_the_actual_bytes(client, s3, clamav, creator_headers, lesson_id):
    """Truncated sends would hang clamd; check the framing is complete."""
    server = clamav("stream: OK")
    key = _put(s3, f"resource/{'3' * 32}-payload.pdf", size=3000)

    client.post(f"/v1/lessons/{lesson_id}/assets",
                json={"kind": "resource", "storage_key": key,
                      "filename": "payload.pdf"},
                headers=creator_headers)
    assert len(server.received) == 3000


def test_unscanned_when_no_scanner_configured(
    client, s3, creator_headers, lesson_id
):
    """Not-scanned must not be recorded as clean."""
    key = _put(s3, f"resource/{'4' * 32}-plain.pdf", size=100)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "plain.pdf"},
                    headers=creator_headers)
    assert r.json()["scan_status"] == "unscanned"


def test_scanner_outage_blocks_when_strict(
    client, s3, monkeypatch, creator_headers, lesson_id
):
    """A configured-but-unreachable scanner can be made to refuse uploads."""
    monkeypatch.setattr(config_module.settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(config_module.settings, "clamav_port", 1)  # nothing there
    monkeypatch.setattr(config_module.settings, "upload_scanning_required", True)

    key = _put(s3, f"resource/{'5' * 32}-outage.pdf", size=100)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "outage.pdf"},
                    headers=creator_headers)
    assert r.status_code == 422
    assert "viruses" in r.json()["detail"].lower()


def test_scanner_outage_permits_when_lenient(
    client, s3, monkeypatch, creator_headers, lesson_id
):
    monkeypatch.setattr(config_module.settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(config_module.settings, "clamav_port", 1)
    monkeypatch.setattr(config_module.settings, "upload_scanning_required", False)

    key = _put(s3, f"resource/{'6' * 32}-degraded.pdf", size=100)
    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "degraded.pdf"},
                    headers=creator_headers)
    assert r.status_code == 201
    assert r.json()["scan_status"] == "unscanned"


def test_oversized_file_is_not_silently_treated_as_clean(
    client, s3, monkeypatch, clamav, creator_headers, lesson_id
):
    """A file too big to scan must be marked failed, not clean."""
    server = clamav("stream: OK")
    key = _put(s3, f"resource/{'7' * 32}-big.pdf", size=100)
    monkeypatch.setattr(upload_service, "MAX_SCAN_BYTES", 50)

    r = client.post(f"/v1/lessons/{lesson_id}/assets",
                    json={"kind": "resource", "storage_key": key,
                          "filename": "big.pdf"},
                    headers=creator_headers)
    assert r.status_code == 201
    assert r.json()["scan_status"] == "failed"
    assert not server.received  # never sent, so never scanned


# ── Scanner protocol parsing ─────────────────────────────────────────────────


def test_parse_recognises_found():
    verdict, detail = scanner._parse("stream: Eicar-Signature FOUND")
    assert verdict is scanner.Verdict.INFECTED
    assert "Eicar" in detail


def test_parse_recognises_ok():
    assert scanner._parse("stream: OK")[0] is scanner.Verdict.CLEAN


def test_parse_treats_unknown_reply_as_error():
    verdict, detail = scanner._parse("INSTREAM size limit exceeded")
    assert verdict is scanner.Verdict.ERROR
    assert "limit" in detail


def test_scan_without_configured_scanner():
    verdict, detail = scanner.scan_bytes(b"anything")
    assert verdict is scanner.Verdict.UNAVAILABLE
    assert "no scanner" in detail


def test_empty_reply_is_an_error_not_clean():
    assert scanner._parse("")[0] is scanner.Verdict.ERROR


# ── Key safety ───────────────────────────────────────────────────────────────


def test_is_server_generated_only_matches_our_keys():
    assert storage.is_server_generated(f"video/{'0' * 32}-clip.mp4")
    assert not storage.is_server_generated("backups/daily.dump")
    assert not storage.is_server_generated("video/short-id.mp4")
    assert not storage.is_server_generated("etc/passwd")


# ── Orphan sweep ─────────────────────────────────────────────────────────────


def test_orphan_sweep_finds_unattached_objects(
    client, s3, creator_headers, lesson_id, db_session
):
    from datetime import datetime, timedelta, timezone

    from scripts.cleanup_orphans import find_orphans

    attached = _put(s3, f"resource/{'8' * 32}-kept.pdf", size=100)
    client.post(f"/v1/lessons/{lesson_id}/assets",
                json={"kind": "resource", "storage_key": attached,
                      "filename": "kept.pdf"},
                headers=creator_headers)

    old = datetime.now(timezone.utc) - timedelta(days=3)
    abandoned = f"video/{'9' * 32}-abandoned.mp4"
    fresh = f"video/{'a' * 32}-inflight.mp4"
    s3.objects[abandoned] = {"ContentLength": 1, "LastModified": old}
    s3.objects[fresh] = {"ContentLength": 1, "LastModified": datetime.now(timezone.utc)}

    expired, kept_fresh = find_orphans(db_session, s3, "talyn-test", max_age_hours=24)

    assert abandoned in [k for k, _ in expired]
    assert attached not in [k for k, _ in expired]      # still referenced
    assert fresh in [k for k, _ in kept_fresh]          # too recent


def test_orphan_sweep_ignores_foreign_objects(
    client, s3, creator_headers, lesson_id, db_session
):
    """A backup sharing the bucket must survive the sweep."""
    from datetime import datetime, timedelta, timezone

    from scripts.cleanup_orphans import find_orphans

    foreign = "backups/database-2026-10-01.dump"
    s3.objects[foreign] = {
        "ContentLength": 1,
        "LastModified": datetime.now(timezone.utc) - timedelta(days=30),
    }
    expired, _ = find_orphans(db_session, s3, "talyn-test", max_age_hours=1)
    assert foreign not in [k for k, _ in expired]


def test_orphan_sweep_keeps_objects_with_unknown_age(
    client, s3, creator_headers, lesson_id, db_session
):
    """No timestamp means we cannot prove it is old, so we keep it."""
    from scripts.cleanup_orphans import find_orphans

    undated = f"video/{'b' * 32}-undated.mp4"
    s3.objects[undated] = {"ContentLength": 1, "LastModified": None}

    expired, kept = find_orphans(db_session, s3, "talyn-test", max_age_hours=0)
    assert undated not in [k for k, _ in expired]
    assert undated in [k for k, _ in kept]

def test_referenced_keys_returns_plain_strings(client, s3, creator_headers, lesson_id, db_session):
    """db.query(Column) yields Rows, not values.

    A set of Rows never matches a plain key string, so every attached file
    would look orphaned and this sweep would delete live course content.
    """
    from scripts.cleanup_orphans import referenced_keys

    attached = _put(s3, f"resource/{'c' * 32}-attached.pdf", size=100)
    client.post(f"/v1/lessons/{lesson_id}/assets",
                json={"kind": "resource", "storage_key": attached,
                      "filename": "attached.pdf"},
                headers=creator_headers)

    keys = referenced_keys(db_session)
    assert all(isinstance(k, str) for k in keys), (
        f"non-string keys mean the sweep cannot match: {keys}"
    )
    assert attached in keys


def test_referenced_keys_includes_thumbnail_and_profile(
    client, s3, creator_headers, db_session
):
    from scripts.cleanup_orphans import referenced_keys

    thumb = _put(s3, f"thumbnail/{'d' * 32}-cover.png", size=100,
                 content_type="image/png")
    profile_img = _put(s3, f"profile_image/{'e' * 32}-face.png", size=100,
                       content_type="image/png")

    course = client.post("/v1/courses", json={
        "title": "Sweep", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": thumb,
    }, headers=creator_headers).json()
    client.put("/v1/me/creator/profile", json={
        "display_name": "Sweep", "bio": "b", "image_key": profile_img,
    }, headers=creator_headers)

    keys = referenced_keys(db_session)
    assert thumb in keys
    assert profile_img in keys


def test_sweep_would_not_delete_an_attached_thumbnail(
    client, s3, creator_headers, db_session
):
    from datetime import datetime, timedelta, timezone

    from scripts.cleanup_orphans import find_orphans

    thumb = _put(s3, f"thumbnail/{'f' * 32}-cover.png", size=100,
                 content_type="image/png")
    client.post("/v1/courses", json={
        "title": "Keep Cover", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": thumb,
    }, headers=creator_headers)
    s3.objects[thumb]["LastModified"] = (
        datetime.now(timezone.utc) - timedelta(days=10)
    )

    expired, _ = find_orphans(db_session, s3, "talyn-test", max_age_hours=1)
    assert thumb not in [k for k, _ in expired], (
        "an attached thumbnail must survive the sweep"
    )


# -- Thumbnails and profile images are not lesson assets -----------------------
# They are attached by PATCH/PUT on the course or profile, so they need their
# own verification or an unchecked file reaches every discovery page.


def test_thumbnail_is_verified_before_being_set(client, s3, creator_headers):
    course = client.post("/v1/courses", json={
        "title": "Thumb", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=creator_headers).json()

    good = _put(s3, f"thumbnail/{'1' * 32}-cover.png", size=2048,
                content_type="image/png")
    ok = client.patch(f"/v1/courses/{course['id']}",
                      json={"thumbnail_key": good}, headers=creator_headers)
    assert ok.status_code == 200

    missing = f"thumbnail/{'2' * 32}-ghost.png"
    bad = client.patch(f"/v1/courses/{course['id']}",
                       json={"thumbnail_key": missing}, headers=creator_headers)
    assert bad.status_code == 422


def test_thumbnail_over_the_cap_is_refused(client, s3, creator_headers, monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "max_thumbnail_bytes", 1024)
    course = client.post("/v1/courses", json={
        "title": "Thumb2", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=creator_headers).json()

    big = _put(s3, f"thumbnail/{'3' * 32}-huge.png", size=50_000,
               content_type="image/png")
    r = client.patch(f"/v1/courses/{course['id']}",
                     json={"thumbnail_key": big}, headers=creator_headers)
    assert r.status_code == 422
    assert big in s3.deleted


def test_thumbnail_cannot_be_a_video_key(client, s3, creator_headers):
    course = client.post("/v1/courses", json={
        "title": "Thumb3", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": "t.png",
    }, headers=creator_headers).json()
    video = _put(s3, f"video/{'4' * 32}-clip.mp4", size=100,
                 content_type="video/mp4")
    r = client.patch(f"/v1/courses/{course['id']}",
                     json={"thumbnail_key": video}, headers=creator_headers)
    assert r.status_code == 422
    assert "different use" in r.json()["detail"].lower()


def test_patching_a_course_without_a_thumbnail_does_not_reverify(
    client, s3, creator_headers
):
    """Editing the title must not fail because the existing thumbnail is gone."""
    thumb = _put(s3, f"thumbnail/{'5' * 32}-cover.png", size=100,
                 content_type="image/png")
    course = client.post("/v1/courses", json={
        "title": "Thumb4", "description": "d", "category": "Design",
        "outcomes": ["o"], "target_audience": "a", "thumbnail_key": thumb,
    }, headers=creator_headers).json()
    s3.objects.pop(thumb)  # simulate storage being cleaned up separately

    r = client.patch(f"/v1/courses/{course['id']}",
                     json={"title": "Renamed"}, headers=creator_headers)
    assert r.status_code == 200


def test_profile_image_is_verified_before_going_public(client, s3, creator_headers):
    good = _put(s3, f"profile_image/{'6' * 32}-face.png", size=100,
                content_type="image/png")
    ok = client.put("/v1/me/creator/profile", json={
        "display_name": "Verified", "bio": "b", "image_key": good,
    }, headers=creator_headers)
    assert ok.status_code == 200

    bad = client.put("/v1/me/creator/profile", json={
        "display_name": "Verified", "bio": "b",
        "image_key": f"profile_image/{'7' * 32}-missing.png",
    }, headers=creator_headers)
    assert bad.status_code == 422


def test_profile_without_an_image_still_works(client, creator_headers):
    """Signup calls this endpoint with just a name; that must not 422."""
    r = client.put("/v1/me/creator/profile",
                   json={"display_name": "No Photo", "bio": "b"},
                   headers=creator_headers)
    assert r.status_code == 200
    assert r.json()["display_name"] == "No Photo"