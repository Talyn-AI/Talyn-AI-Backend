"""Tests for uploads, lesson assets, thumbnails, and file access URLs.

These cover the request/response contract. The size caps, verified sizes,
malware scanning and orphan sweep live in test_upload_hardening.py.
"""
import pytest

from app.services import storage as storage_module


def _register(client, email, name, is_creator=False):
    client.post(
        "/v1/auth/register",
        json={"email": email, "password": "secret12345",
              "learner_name": name, "is_creator": is_creator},
    )
    r = client.post("/v1/auth/login",
                    json={"email": email, "password": "secret12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def creator_headers(client, onboard):
    headers = _register(client, "up@example.com", "Up", is_creator=True)
    onboard(headers)
    return headers

@pytest.fixture()
def learner_headers(client, onboard):
    headers = _register(client, "uplearner@example.com", "UpLearner")
    onboard(headers)
    return headers

@pytest.fixture()
def course_id(client, creator_headers):
    return client.post(
        "/v1/courses",
        json={"title": "VCourse", "description": "d", "category": "C",
              "outcomes": ["O"], "target_audience": "A",
              "thumbnail_key": "thumbs/v.png"},
        headers=creator_headers,
    ).json()["id"]


@pytest.fixture()
def lesson_id(client, creator_headers, course_id):
    mid = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    return client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": mid, "title": "L", "topic": "T", "content": "c"},
        headers=creator_headers).json()["id"]


@pytest.fixture()
def storage_mock(monkeypatch):
    """Pretend S3 exists.

    Uploads are now a presigned POST (not a PUT URL) and attaching a file
    reads the real size back from storage, so the fake has to answer head
    requests for the keys these tests attach.
    """
    monkeypatch.setattr(
        storage_module, "presigned_upload",
        lambda key, content_type, max_bytes: {
            "upload_url": f"https://s3.test/{key}",
            "fields": {"key": key, "policy": "signed"},
            "storage_key": key,
            "expires_in": 900,
            "max_bytes": max_bytes,
            "method": "POST",
        },
    )
    monkeypatch.setattr(
        storage_module, "presigned_download_url",
        lambda key: f"https://s3.test/{key}?sig=x",
    )
    # Any key these tests attach "exists" at a plausible size.
    monkeypatch.setattr(
        storage_module, "head_object",
        lambda key: {"ContentLength": 2048, "ContentType": "video/mp4"},
    )
    monkeypatch.setattr(storage_module, "delete_object", lambda key: True)
    monkeypatch.setattr(storage_module, "download_bytes",
                        lambda key, max_bytes: b"")


# ── Presigned uploads ─────────────────────────────────────────────────────────

def test_presign_flow(client, creator_headers, storage_mock):
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "intro.mp4",
              "content_type": "video/mp4"},
        headers=creator_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["storage_key"].startswith("video/")
    assert body["upload_url"].startswith("https://s3.test/video/")
    # A POST form, not a bare PUT URL: this is what carries the size cap.
    assert body["method"] == "POST"
    assert body["max_bytes"] > 0
    assert "policy" in body["fields"]


def test_presign_rejects_bad_purpose_and_type(client, creator_headers,
                                              storage_mock):
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "song", "filename": "x.mp3",
              "content_type": "audio/mp3"},
        headers=creator_headers,
    )
    assert r.status_code == 422
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "x.exe",
              "content_type": "application/x-msdownload"},
        headers=creator_headers,
    )
    assert r.status_code == 422
    assert client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "x.mp4",
              "content_type": "video/mp4"},
    ).status_code == 401


def test_presign_unconfigured_is_503(client, creator_headers, monkeypatch):
    from app import config as config_module

    # force unconfigured regardless of local .env (E2E setups configure S3)
    monkeypatch.setattr(config_module.settings, "s3_bucket", "")
    r = client.post(
        "/v1/uploads/presigned",
        json={"purpose": "video", "filename": "x.mp4",
              "content_type": "video/mp4"},
        headers=creator_headers,
    )
    assert r.status_code == 503


# ── Assets ────────────────────────────────────────────────────────────────────

def test_asset_lifecycle(client, creator_headers, lesson_id, storage_mock):
    r = client.post(
        f"/v1/lessons/{lesson_id}/assets",
        json={"kind": "video", "storage_key": "video/abc.mp4",
              "filename": "intro.mp4"},
        headers=creator_headers,
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    # Recorded size comes from storage, not from the request.
    assert r.json()["size_bytes"] == 2048

    rows = client.get(f"/v1/lessons/{lesson_id}/assets",
                      headers=creator_headers).json()
    assert len(rows) == 1

    assert client.delete(f"/v1/lessons/{lesson_id}/assets/{aid}",
                         headers=creator_headers).status_code == 200
    assert client.get(f"/v1/lessons/{lesson_id}/assets",
                      headers=creator_headers).json() == []


def test_asset_validation(client, creator_headers, lesson_id, storage_mock):
    base = f"/v1/lessons/{lesson_id}/assets"
    assert client.post(base, json={"kind": "podcast", "url": "x"},
                       headers=creator_headers).status_code == 422
    assert client.post(base, json={"kind": "link"},
                       headers=creator_headers).status_code == 422
    assert client.post(
        base, json={"kind": "video", "storage_key": "resource/x.pdf"},
        headers=creator_headers).status_code == 422
    r = client.post(
        base, json={"kind": "link", "url": "https://example.com/notes"},
        headers=creator_headers)
    assert r.status_code == 201


def test_assets_require_owner(client, learner_headers, lesson_id):
    assert client.post(f"/v1/lessons/{lesson_id}/assets",
                       json={"kind": "link", "url": "https://x.example"},
                       headers=learner_headers).status_code == 403


# ── Publish with video-only lesson ────────────────────────────────────────────

def test_video_only_lesson_publishable(client, creator_headers, course_id,
                                       storage_mock):
    mid = client.post(f"/v1/courses/{course_id}/modules", json={"title": "M"},
                      headers=creator_headers).json()["id"]
    lid = client.post(
        f"/v1/courses/{course_id}/lessons",
        json={"module_id": mid, "title": "Watch", "topic": "T"},
        headers=creator_headers).json()["id"]
    assert client.get(f"/v1/courses/{course_id}/publish-check",
                      headers=creator_headers).json()["publishable"] is False
    client.post(f"/v1/lessons/{lid}/assets",
                json={"kind": "video", "storage_key": "video/w.mp4"},
                headers=creator_headers)
    check = client.get(f"/v1/courses/{course_id}/publish-check",
                       headers=creator_headers).json()
    assert check == {"publishable": True, "errors": []}


# ── File access URLs ──────────────────────────────────────────────────────────

def test_file_url_rules(client, creator_headers, learner_headers, course_id,
                        lesson_id, storage_mock):
    client.post(f"/v1/lessons/{lesson_id}/assets",
                json={"kind": "video", "storage_key": "video/w.mp4"},
                headers=creator_headers)
    # draft: anonymous denied, owner allowed
    assert client.get("/v1/files/url?key=video/w.mp4").status_code == 401
    assert client.get("/v1/files/url?key=video/w.mp4",
                      headers=learner_headers).status_code == 403
    r = client.get("/v1/files/url?key=video/w.mp4", headers=creator_headers)
    assert r.json()["download_url"].startswith("https://s3.test/")

    # unknown key
    assert client.get("/v1/files/url?key=nope/x.mp4",
                      headers=creator_headers).status_code == 404

    # thumbnail of draft: owner only
    assert client.get("/v1/files/url?key=thumbs/v.png",
                      headers=creator_headers).status_code == 200
