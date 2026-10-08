"""Abandoned checkouts stop being recycled.

A pending row older than the TTL counts as abandoned: a new purchase mints a
fresh checkout instead of reusing it, and the status endpoints stop
reporting it. Stale rows are never mutated — if money moves late against an
old reference, the webhook and verify paths still settle it, because a
confirmed charge is a confirmed charge regardless of how long the learner
took.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import config as config_module
from app.models import Payment
from app.services import payments as pay

SECRET = "sk_test_fixture_key_no_real_money_here"


@pytest.fixture
def paystack_on(monkeypatch):
    monkeypatch.setattr(config_module.settings, "paystack_secret_key", SECRET)
    monkeypatch.setattr(config_module.settings, "paystack_api_url",
                        "https://pay.test")
    _patch_provider(
        monkeypatch,
        lambda req: _ok({"authorization_url": "https://checkout.paystack.test/mock"}),
    )


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": True, "message": "ok",
                                     "data": data})


def _patch_provider(monkeypatch, handler):
    calls: list = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    class _Bound:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json=None):
            return handle(httpx.Request(
                "POST", f"https://pay.test{path}", json=json,
                headers={"Content-Type": "application/json"}))

        def get(self, path):
            return handle(httpx.Request("GET", f"https://pay.test{path}"))

    monkeypatch.setattr(pay, "_client", lambda: _Bound())
    return calls


def _backdate(db_session, reference: str, hours: int = 25):
    row = db_session.query(Payment).filter(
        Payment.reference == reference).one()
    row.created_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db_session.commit()
    return row


# ── Courses ──────────────────────────────────────────────────────────────────


@pytest.fixture
def paid_course_id(client, pay_creator_headers, db_session):
    from app.models import Course

    cid = client.post("/v1/courses", json={
        "title": "Paid Design", "description": "Worth it",
        "category": "Design", "outcomes": ["Earn"],
        "target_audience": "All", "thumbnail_key": "t.png",
        "course_type": "paid", "price_naira": 12000,
    }, headers=pay_creator_headers).json()["id"]
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


@pytest.fixture
def pay_creator_headers(client):
    client.post("/v1/auth/register", json={
        "email": "exp-creator@example.com", "password": "password123",
        "learner_name": "Creator", "is_creator": True,
    })
    token = client.post("/v1/auth/login", json={
        "email": "exp-creator@example.com", "password": "password123",
    }).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def paystack_learner_headers(client, onboard):
    client.post("/v1/auth/register", json={
        "email": "exp-learner@example.com", "password": "password123",
        "learner_name": "Learner",
    })
    token = client.post("/v1/auth/login", json={
        "email": "exp-learner@example.com", "password": "password123",
    }).json()["access_token"]
    onboard("exp-learner@example.com")
    return {"Authorization": f"Bearer {token}"}


def test_stale_pending_is_not_reused(client, paystack_learner_headers,
                                     paid_course_id, paystack_on, db_session):
    first = client.post(f"/v1/courses/{paid_course_id}/purchase",
                        headers=paystack_learner_headers).json()
    _backdate(db_session, first["payment"]["reference"])

    second = client.post(f"/v1/courses/{paid_course_id}/purchase",
                         headers=paystack_learner_headers).json()
    assert second["payment"]["reference"] != first["payment"]["reference"]

    # The old row is untouched: still pending, never mutated by the new sale.
    old = db_session.query(Payment).filter(
        Payment.reference == first["payment"]["reference"]).one()
    assert old.status == "pending"


def test_fresh_pending_is_still_reused(client, paystack_learner_headers,
                                       paid_course_id, paystack_on):
    first = client.post(f"/v1/courses/{paid_course_id}/purchase",
                        headers=paystack_learner_headers).json()
    second = client.post(f"/v1/courses/{paid_course_id}/purchase",
                         headers=paystack_learner_headers).json()
    assert second["payment"]["reference"] == first["payment"]["reference"]


def test_stale_pending_is_invisible_to_status(client, paystack_learner_headers,
                                             paid_course_id, paystack_on,
                                             db_session):
    ref = client.post(f"/v1/courses/{paid_course_id}/purchase",
                      headers=paystack_learner_headers).json()["payment"]["reference"]
    _backdate(db_session, ref)

    r = client.get(f"/v1/courses/{paid_course_id}/payment",
                   headers=paystack_learner_headers)
    assert r.status_code == 404


def test_late_money_still_settles(client, paystack_learner_headers,
                                  paid_course_id, paystack_on, monkeypatch,
                                  db_session):
    ref = client.post(f"/v1/courses/{paid_course_id}/purchase",
                      headers=paystack_learner_headers).json()["payment"]["reference"]
    _backdate(db_session, ref)
    _patch_provider(monkeypatch, lambda req: _ok({
        "reference": ref, "status": "success", "amount": 1_200_000,
        "currency": "NGN", "channel": "card",
        "paid_at": "2026-09-30T12:00:00.000Z",
    }))

    r = client.post(f"/v1/courses/{paid_course_id}/verify",
                    params={"reference": ref},
                    headers=paystack_learner_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["enrolled"] is True


# ── Materials ────────────────────────────────────────────────────────────────


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


@pytest.fixture
def s3(monkeypatch):
    from app.services import storage as storage_module

    fake = FakeS3()
    monkeypatch.setattr(storage_module, "_client", lambda: (fake, "talyn-test"))
    monkeypatch.setattr(config_module.settings, "s3_bucket", "talyn-test")
    return fake


@pytest.fixture
def coach(monkeypatch):
    from app.services import coach_client as cc

    monkeypatch.setattr(cc, "analyze_material", lambda uid, text, fn: {
        "topics": ["T"], "objectives": ["O"], "estimated_minutes": 10,
        "summary": "S",
    })


@pytest.fixture
def material_id(client, s3, paystack_learner_headers, coach):
    from datetime import timezone as _tz

    key = client.post("/v1/me/materials/presigned",
                      headers=paystack_learner_headers, json={
                          "filename": "n.txt", "content_type": "text/plain",
                          "size_bytes": 64,
                      }).json()["storage_key"]
    s3.objects[key] = {
        "ContentLength": 64, "ContentType": "text/plain",
        "Body": b"Photosynthesis converts light. " * 4,
        "LastModified": datetime.now(timezone.utc),
    }
    mid = client.post("/v1/me/materials", headers=paystack_learner_headers,
                      json={"storage_key": key}).json()["id"]
    assert client.post(f"/v1/me/materials/{mid}/analyze",
                       headers=paystack_learner_headers).status_code == 200
    assert client.post(f"/v1/me/materials/{mid}/plan",
                       headers=paystack_learner_headers,
                       json={"purpose": "exam", "days": 7}).status_code == 200
    return mid


def test_material_stale_pending_mints_fresh(client, paystack_learner_headers,
                                            material_id, paystack_on,
                                            db_session):
    first = client.post(f"/v1/me/materials/{material_id}/purchase",
                        headers=paystack_learner_headers).json()
    _backdate(db_session, first["reference"])

    second = client.post(f"/v1/me/materials/{material_id}/purchase",
                         headers=paystack_learner_headers).json()
    assert second["reference"] != first["reference"]
    assert second["unlocked"] is False
    assert second["checkout_url"].startswith("https://")


def test_material_stale_pending_invisible_to_status(
        client, paystack_learner_headers, material_id, paystack_on,
        db_session):
    ref = client.post(f"/v1/me/materials/{material_id}/purchase",
                      headers=paystack_learner_headers).json()["reference"]
    _backdate(db_session, ref)

    assert client.get(f"/v1/me/materials/{material_id}/payment",
                      headers=paystack_learner_headers).status_code == 404


def test_material_late_money_unlocks(client, paystack_learner_headers,
                                     material_id, paystack_on, monkeypatch,
                                     db_session):
    ref = client.post(f"/v1/me/materials/{material_id}/purchase",
                      headers=paystack_learner_headers).json()["reference"]
    _backdate(db_session, ref)
    _patch_provider(monkeypatch, lambda req: _ok({
        "reference": ref, "status": "success", "amount": 250_000,
        "currency": "NGN", "channel": "card",
        "paid_at": "2026-09-30T12:00:00.000Z",
    }))

    r = client.post(f"/v1/me/materials/{material_id}/verify",
                    headers=paystack_learner_headers)
    assert r.status_code == 200, r.text
    assert r.json()["unlocked"] is True
