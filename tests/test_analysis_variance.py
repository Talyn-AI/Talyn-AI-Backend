"""Two different uploads must send two different texts to the coach.

Regression for the "every analysis shows the same topics" report: if the
backend ever mixed up which bytes belong to which material (stale key,
shared buffer, wrong row), different documents would analyze identically.
This uploads two distinct files and asserts the coach received each one's
own words.
"""
from datetime import datetime, timezone

import pytest

from app import config as config_module
from app.services import coach_client as cc
from app.services import storage


class FakeS3:
    def __init__(self):
        self.objects: dict = {}

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        key = (Params or {}).get("Key", "")
        return f"https://talyn-test.s3.test/{key}?sig={op}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404 Not Found")
        return self.objects[Key]

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[Key].get("Body", b""))}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", lambda: (fake, "talyn-test"))
    monkeypatch.setattr(config_module.settings, "s3_bucket", "talyn-test")
    return fake


def _upload_text(client, headers, s3, filename, text: str) -> int:
    body = text.encode()
    key = client.post("/v1/me/materials/presigned", headers=headers, json={
        "filename": filename, "content_type": "text/plain",
        "size_bytes": len(body),
    }).json()["storage_key"]
    s3.objects[key] = {
        "ContentLength": len(body),
        "ContentType": "text/plain",
        "Body": body,
        "LastModified": datetime.now(timezone.utc),
    }
    r = client.post("/v1/me/materials", headers=headers, json={
        "storage_key": key,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_two_uploads_send_their_own_words(client, s3, learner_headers,
                                          monkeypatch):
    """The exact bytes each upload carried must be what analysis reads."""
    seen: dict = {}

    def _fake_analyze(user_id, document_text, filename):
        seen[filename] = document_text
        return {"topics": ["T"], "objectives": ["O"],
                "estimated_minutes": 10, "summary": "S"}

    monkeypatch.setattr(cc, "analyze_material", _fake_analyze)

    photo = _upload_text(
        client, learner_headers, s3, "photosynthesis.txt",
        "Chlorophyll absorbs red and blue wavelengths for photosynthesis. " * 10)
    thermo = _upload_text(
        client, learner_headers, s3, "thermodynamics.txt",
        "Entropy of an isolated system never decreases over time. " * 10)

    assert client.post(f"/v1/me/materials/{photo}/analyze",
                       headers=learner_headers).status_code == 200
    assert client.post(f"/v1/me/materials/{thermo}/analyze",
                       headers=learner_headers).status_code == 200

    assert set(seen) == {"photosynthesis.txt", "thermodynamics.txt"}
    assert "Chlorophyll" in seen["photosynthesis.txt"]
    assert "Entropy" in seen["thermodynamics.txt"]
    assert "Entropy" not in seen["photosynthesis.txt"]
    assert "Chlorophyll" not in seen["thermodynamics.txt"]
