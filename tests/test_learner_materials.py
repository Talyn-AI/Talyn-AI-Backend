"""Learner library: private study materials.

Storage is faked at the boto boundary (same FakeS3 shape as the upload
hardening suite), so these tests exercise the real presign/claim/quota logic
and the real DB writes with no network.
"""
from datetime import datetime, timezone

import pytest

from app import config as config_module
from app.models import LearnerMaterial
from app.services import storage


class FakeS3:
    def __init__(self):
        self.objects: dict = {}
        self.deleted: list[str] = []

    def generate_presigned_post(self, Bucket, Key, Conditions, Fields=None,
                                ExpiresIn=None):
        return {
            "url": f"https://{Bucket}.s3.test/{Key}",
            "fields": {"key": Key, "policy": "signed", **(Fields or {})},
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404 Not Found")
        return self.objects[Key]

    def generate_presigned_url(self, *a, **k):
        return "https://s3.test/download?signature=fake"

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: (fake, "talyn-test"))
    monkeypatch.setattr(config_module.settings, "s3_bucket", "talyn-test")
    return fake


def _put(s3, key, size=2048, content_type="application/pdf"):
    s3.objects[key] = {
        "ContentLength": size,
        "ContentType": content_type,
        "Body": b"x" * min(size, 4096),
        "LastModified": datetime.now(timezone.utc),
    }
    return key


def _presign(client, headers, filename="notes.pdf",
             content_type="application/pdf", size=2048):
    r = client.post("/v1/me/materials/presigned", headers=headers, json={
        "filename": filename, "content_type": content_type,
        "size_bytes": size,
    })
    assert r.status_code == 201, r.text
    return r.json()["storage_key"]


def _claim(client, headers, key):
    return client.post("/v1/me/materials", headers=headers, json={
        "storage_key": key,
    })


@pytest.fixture
def learner2(client, onboard):
    """A second learner, for ownership tests."""
    client.post("/v1/auth/register", json={
        "email": "library-two@example.com", "password": "password123",
        "learner_name": "Two",
    })
    token = client.post("/v1/auth/login", json={
        "email": "library-two@example.com", "password": "password123",
    }).json()["access_token"]
    onboard("library-two@example.com")
    return {"Authorization": f"Bearer {token}"}


# ── Presign ──────────────────────────────────────────────────────────────────


def test_learner_can_presign_a_document(client, s3, learner_headers):
    key = _presign(client, learner_headers)
    assert key.startswith("material/")


def test_presign_rejects_an_executable(client, s3, learner_headers):
    r = client.post("/v1/me/materials/presigned", headers=learner_headers, json={
        "filename": "notes.exe", "content_type": "application/pdf",
        "size_bytes": 100,
    })
    assert r.status_code == 422


def test_presign_rejects_a_video(client, s3, learner_headers):
    """Video stays creator-only: the type decision, not just the size one."""
    r = client.post("/v1/me/materials/presigned", headers=learner_headers, json={
        "filename": "lecture.mp4", "content_type": "video/mp4",
        "size_bytes": 100,
    })
    assert r.status_code == 422


def test_presign_needs_auth(client, s3):
    r = client.post("/v1/me/materials/presigned", json={
        "filename": "notes.pdf", "content_type": "application/pdf",
    })
    assert r.status_code == 401


def test_presign_needs_onboarding(client, s3):
    """Unverified accounts cannot start storing files."""
    client.post("/v1/auth/register", json={
        "email": "fresh@example.com", "password": "password123",
        "learner_name": "Fresh",
    })
    token = client.post("/v1/auth/login", json={
        "email": "fresh@example.com", "password": "password123",
    }).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.post("/v1/me/materials/presigned", headers=h, json={
        "filename": "notes.pdf", "content_type": "application/pdf",
    })
    assert r.status_code == 409


# ── Claim ────────────────────────────────────────────────────────────────────


def test_claim_records_the_providers_size_not_the_claim(client, s3,
                                                        learner_headers,
                                                        db_session):
    key = _presign(client, learner_headers, size=2048)
    _put(s3, key, size=9999)

    r = _claim(client, learner_headers, key)
    assert r.status_code == 201, r.text
    assert r.json()["size_bytes"] == 9999
    assert db_session.query(LearnerMaterial).one().size_bytes == 9999


def test_claim_of_an_unfinished_upload_is_422(client, s3, learner_headers):
    key = _presign(client, learner_headers)
    r = _claim(client, learner_headers, key)
    assert r.status_code == 422
    assert "did not complete" in r.json()["detail"]


def test_claim_rejects_a_key_minted_for_another_purpose(
    client, s3, learner_headers, creator_headers
):
    """A creator video key cannot be claimed into the learner library."""
    r = client.post("/v1/uploads/presigned", headers=creator_headers, json={
        "purpose": "video", "filename": "lecture.mp4",
        "content_type": "video/mp4", "size_bytes": 100,
    })
    assert r.status_code == 201, r.text
    video_key = r.json()["storage_key"]
    assert video_key.startswith("video/")

    r = _claim(client, learner_headers, video_key)
    assert r.status_code == 422
    assert "different use" in r.json()["detail"]


def test_claiming_twice_is_a_409_not_a_second_row(
    client, s3, learner_headers, db_session
):
    key = _presign(client, learner_headers)
    _put(s3, key)
    assert _claim(client, learner_headers, key).status_code == 201
    assert _claim(client, learner_headers, key).status_code == 409
    assert db_session.query(LearnerMaterial).count() == 1


def test_library_quota_is_enforced_at_claim(
    client, s3, learner_headers, db_session, monkeypatch
):
    monkeypatch.setattr(
        config_module.settings, "max_learner_library_bytes", 3000
    )
    first = _presign(client, learner_headers)
    _put(s3, first, size=2000)
    assert _claim(client, learner_headers, first).status_code == 201

    # Declares 100 bytes but lands 2000: the presign check passes on the
    # claim, the claim check fails on the truth — which is why both exist.
    second = _presign(client, learner_headers, size=100)
    _put(s3, second, size=2000)
    r = _claim(client, learner_headers, second)
    assert r.status_code == 422
    assert "library is full" in r.json()["detail"]
    assert db_session.query(LearnerMaterial).count() == 1


# ── Reading and deleting ─────────────────────────────────────────────────────


def test_library_lists_newest_first(client, s3, learner_headers):
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _put(s3, _presign(client, learner_headers, filename=name))
    for key in list(s3.objects):
        assert _claim(client, learner_headers, key).status_code == 201

    names = [m["filename"] for m in
             client.get("/v1/me/materials", headers=learner_headers).json()]
    assert names == ["c.pdf", "b.pdf", "a.pdf"]


def test_one_learners_library_is_invisible_to_another(
    client, s3, learner_headers, learner2
):
    _put(s3, _presign(client, learner_headers))
    for key in list(s3.objects):
        _claim(client, learner_headers, key)

    assert client.get("/v1/me/materials", headers=learner2).json() == []


def test_download_url_is_owner_only(client, s3, learner_headers, learner2):
    _put(s3, _presign(client, learner_headers))
    for key in list(s3.objects):
        _claim(client, learner_headers, key)
    (key,) = list(s3.objects)

    own = client.get("/v1/files/url", params={"key": key},
                     headers=learner_headers)
    assert own.status_code == 200
    assert own.json()["download_url"].startswith("https://")

    other = client.get("/v1/files/url", params={"key": key}, headers=learner2)
    assert other.status_code == 403

    anon = client.get("/v1/files/url", params={"key": key})
    assert anon.status_code == 401


def test_delete_removes_the_row_but_lists_nothing_else(
    client, s3, learner_headers, learner2, db_session
):
    _put(s3, _presign(client, learner_headers))
    for key in list(s3.objects):
        _claim(client, learner_headers, key)
    mid = db_session.query(LearnerMaterial).one().id

    assert client.delete(
        f"/v1/me/materials/{mid}", headers=learner2).status_code == 404
    assert client.delete(
        f"/v1/me/materials/{mid}", headers=learner_headers).status_code == 200
    assert db_session.query(LearnerMaterial).count() == 0
