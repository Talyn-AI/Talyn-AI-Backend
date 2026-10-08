"""The monetization loop: analyze (free) → purchase (₦2,500) → schedule.

The coach is faked at the client boundary and storage at the boto boundary,
so these tests exercise the real orchestration — extraction, settlement
branching, lazy generation, day completion — with no network and no money.
"""
from datetime import datetime, timezone

import pytest

from app import config as config_module
from app.models import LearnerMaterial, MaterialAnalysis, Payment, StudySchedule
from app.services import coach_client as cc
from app.services import storage


class FakeS3:
    def __init__(self):
        self.objects: dict = {}
        self.deleted: list[str] = []

    def generate_presigned_post(self, *a, **k):
        raise NotImplementedError(
            "R2 does not implement POST Object; the app mints PUT URLs")

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        key = (Params or {}).get("Key", "")
        return f"https://talyn-test.s3.test/{key}?sig={op}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404 Not Found")
        return self.objects[Key]

    def get_object(self, Bucket, Key):
        import io

        record = self.objects[Key]
        return {"Body": io.BytesIO(record.get("Body", b""))}

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


@pytest.fixture
def coach(monkeypatch):
    """Fake the coach at the client boundary (both calls)."""
    monkeypatch.setattr(cc, "analyze_material", lambda uid, text, fn: {
        "topics": ["Photosynthesis", "Chlorophyll"],
        "objectives": ["Explain the light reactions"],
        "estimated_minutes": 120,
        "summary": "A short primer on photosynthesis.",
    })

    def _schedule(uid, text, topics, objectives, days, difficulty, purpose=""):
        assert "Photosynthesis" in text
        return {
            "title": "Photosynthesis in 14 days",
            "days": [
                {"day": n, "title": f"Day {n}",
                 "objectives": ["Learn"], "tasks": ["Read"]}
                for n in range(1, days + 1)
            ],
        }

    monkeypatch.setattr(cc, "generate_schedule", _schedule)


TEXT_BODY = (
    b"Photosynthesis converts light energy into chemical energy. "
    b"Chlorophyll absorbs red and blue wavelengths. " * 20
)


def _upload(client, headers, s3, filename="primer.txt",
            content_type="text/plain", body=None, size=2048):
    key = client.post("/v1/me/materials/presigned", headers=headers, json={
        "filename": filename, "content_type": content_type,
        "size_bytes": size,
    }).json()["storage_key"]
    s3.objects[key] = {
        "ContentLength": size,
        "ContentType": content_type,
        "Body": body if body is not None else b"x" * min(size, 4096),
        "LastModified": datetime.now(timezone.utc),
    }
    r = client.post("/v1/me/materials", headers=headers, json={
        "storage_key": key,
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _analyze(client, headers, material_id):
    r = client.post(f"/v1/me/materials/{material_id}/analyze", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _plan(client, headers, material_id, purpose="exam", days=14):
    r = client.post(f"/v1/me/materials/{material_id}/plan", headers=headers,
                    json={"purpose": purpose, "days": days})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def material_id(client, s3, learner_headers):
    return _upload(client, learner_headers, s3, body=TEXT_BODY)


@pytest.fixture
def learner2(client, onboard):
    client.post("/v1/auth/register", json={
        "email": "money-two@example.com", "password": "password123",
        "learner_name": "Two",
    })
    token = client.post("/v1/auth/login", json={
        "email": "money-two@example.com", "password": "password123",
    }).json()["access_token"]
    onboard("money-two@example.com")
    return {"Authorization": f"Bearer {token}"}


# ── Analyze ──────────────────────────────────────────────────────────────────


def test_analyze_returns_and_stores_the_preview(client, learner_headers,
                                                material_id, coach, db_session):
    r = client.post(f"/v1/me/materials/{material_id}/analyze",
                    headers=learner_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["topics"] == ["Photosynthesis", "Chlorophyll"]
    assert body["estimated_minutes"] == 120

    row = db_session.query(MaterialAnalysis).one()
    assert row.material_id == material_id


def test_analyze_replaces_the_previous_preview(client, learner_headers,
                                               material_id, coach, db_session):
    assert client.post(f"/v1/me/materials/{material_id}/analyze",
                       headers=learner_headers).status_code == 200
    assert client.post(f"/v1/me/materials/{material_id}/analyze",
                       headers=learner_headers).status_code == 200
    assert db_session.query(MaterialAnalysis).count() == 1


def test_analyze_someone_elses_material_is_404(client, learner2, material_id,
                                              coach):
    assert client.post(f"/v1/me/materials/{material_id}/analyze",
                       headers=learner2).status_code == 404


def test_analyze_without_a_configured_coach_is_503(client, learner_headers,
                                                   material_id, monkeypatch):
    monkeypatch.setattr(config_module.settings, "coach_base_url", "")
    r = client.post(f"/v1/me/materials/{material_id}/analyze",
                    headers=learner_headers)
    assert r.status_code == 503


# ── Purchase ─────────────────────────────────────────────────────────────────


def test_purchase_needs_an_analysis_first(client, learner_headers, material_id):
    r = client.post(f"/v1/me/materials/{material_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 409
    assert "Analyze" in r.json()["detail"]


def test_stub_purchase_unlocks_inline(client, learner_headers, material_id,
                                      coach, db_session, smtp):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    r = client.post(f"/v1/me/materials/{material_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unlocked"] is True
    assert body["amount_naira"] == 2500
    assert body["checkout_url"] is None

    payment = db_session.query(Payment).one()
    assert payment.material_id == material_id
    assert payment.course_id is None
    assert payment.status == "success"
    assert any("receipt" in (m["Subject"] or "").lower() for m in smtp.sent)


def test_purchase_twice_is_409(client, learner_headers, material_id, coach):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    assert client.post(f"/v1/me/materials/{material_id}/purchase",
                       headers=learner_headers).status_code == 200
    r = client.post(f"/v1/me/materials/{material_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 409


def test_payment_status_tracks_the_flow(client, learner_headers, material_id,
                                        coach):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    assert client.get(f"/v1/me/materials/{material_id}/payment",
                      headers=learner_headers).status_code == 404
    client.post(f"/v1/me/materials/{material_id}/purchase",
                headers=learner_headers)
    body = client.get(f"/v1/me/materials/{material_id}/payment",
                      headers=learner_headers).json()
    assert body["unlocked"] is True
    assert body["status"] == "success"


# ── Schedule ─────────────────────────────────────────────────────────────────


def test_schedule_is_402_before_payment(client, learner_headers, material_id,
                                        coach):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    r = client.get(f"/v1/me/materials/{material_id}/schedule",
                   headers=learner_headers)
    assert r.status_code == 402


def test_schedule_generates_once_paid(client, learner_headers, material_id,
                                      coach, db_session):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    client.post(f"/v1/me/materials/{material_id}/purchase",
                headers=learner_headers)

    first = client.get(f"/v1/me/materials/{material_id}/schedule",
                       headers=learner_headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["days"]) == 14
    assert [d["day"] for d in body["days"]] == list(range(1, 15))
    assert body["days_total"] == 14
    assert body["days_completed"] == 0
    assert body["completion_percent"] == 0.0

    again = client.get(f"/v1/me/materials/{material_id}/schedule",
                       headers=learner_headers)
    assert again.json()["id"] == body["id"]
    assert db_session.query(StudySchedule).count() == 1


def test_complete_a_day(client, learner_headers, material_id, coach):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    client.post(f"/v1/me/materials/{material_id}/purchase",
                headers=learner_headers)
    client.get(f"/v1/me/materials/{material_id}/schedule",
               headers=learner_headers)

    r = client.post(f"/v1/me/materials/{material_id}/days/1/complete",
                    headers=learner_headers)
    assert r.status_code == 200
    assert r.json() == {"completed": True, "day": 1}
    # Idempotent.
    assert client.post(f"/v1/me/materials/{material_id}/days/1/complete",
                       headers=learner_headers).status_code == 200

    body = client.get(f"/v1/me/materials/{material_id}/schedule",
                      headers=learner_headers).json()
    assert body["days_completed"] == 1
    assert body["days"][0]["completed"] is True


def test_complete_an_unknown_day_is_404(client, learner_headers, material_id,
                                        coach):
    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    _plan(client, learner_headers, material_id)
    client.post(f"/v1/me/materials/{material_id}/purchase",
                headers=learner_headers)
    client.get(f"/v1/me/materials/{material_id}/schedule",
               headers=learner_headers)
    assert client.post(f"/v1/me/materials/{material_id}/days/99/complete",
                       headers=learner_headers).status_code == 404


def test_schedule_is_per_learner(client, learner2, material_id, coach):
    assert client.get(f"/v1/me/materials/{material_id}/schedule",
                      headers=learner2).status_code == 404


# ── Purpose and timeline ───────────────────────────────────────────────────────


def test_plan_records_intent_and_reads_back(client, learner_headers,
                                           material_id, coach):
    _analyze(client, learner_headers, material_id)
    body = _plan(client, learner_headers, material_id,
                 purpose="final exam", days=2)
    assert body["purpose"] == "final exam"
    assert body["timeline_days"] == 2
    assert body["topics"] == ["Photosynthesis", "Chlorophyll"]

    reread = client.get(f"/v1/me/materials/{material_id}/analysis",
                        headers=learner_headers).json()
    assert reread["purpose"] == "final exam"
    assert reread["timeline_days"] == 2


def test_plan_needs_an_analysis_first(client, learner_headers, material_id):
    r = client.post(f"/v1/me/materials/{material_id}/plan",
                    headers=learner_headers,
                    json={"purpose": "exam", "days": 7})
    assert r.status_code == 409


def test_plan_validates_its_inputs(client, learner_headers, material_id, coach):
    _analyze(client, learner_headers, material_id)
    base = f"/v1/me/materials/{material_id}/plan"
    assert client.post(base, headers=learner_headers,
                       json={"purpose": "", "days": 7}).status_code == 422
    assert client.post(base, headers=learner_headers,
                       json={"purpose": "exam", "days": 0}).status_code == 422
    assert client.post(base, headers=learner_headers,
                       json={"purpose": "exam", "days": 31}).status_code == 422
    assert client.post(base, headers=learner_headers,
                       json={"purpose": "x" * 101, "days": 7}).status_code == 422


def test_purchase_needs_a_plan_not_just_an_analysis(client, learner_headers,
                                                    material_id, coach):
    _analyze(client, learner_headers, material_id)
    r = client.post(f"/v1/me/materials/{material_id}/purchase",
                    headers=learner_headers)
    assert r.status_code == 409
    assert "purpose and timeline" in r.json()["detail"]


def test_reanalyze_preserves_intent(client, learner_headers, material_id,
                                    coach, db_session):
    _analyze(client, learner_headers, material_id)
    _plan(client, learner_headers, material_id, purpose="interview", days=5)
    _analyze(client, learner_headers, material_id)

    analysis = db_session.query(MaterialAnalysis).one()
    assert analysis.purpose == "interview"
    assert analysis.timeline_days == 5


def test_schedule_honours_a_two_day_timeline(client, learner_headers,
                                             material_id, coach):
    _analyze(client, learner_headers, material_id)
    _plan(client, learner_headers, material_id, purpose="exam in 2 days",
          days=2)
    client.post(f"/v1/me/materials/{material_id}/purchase",
                headers=learner_headers)
    body = client.get(f"/v1/me/materials/{material_id}/schedule",
                      headers=learner_headers).json()
    assert body["days_total"] == 2
    assert [d["day"] for d in body["days"]] == [1, 2]
    assert body["purpose"] == "exam in 2 days"


def test_changing_the_plan_before_purchase_reshapes(client, learner_headers,
                                                   material_id, coach):
    _analyze(client, learner_headers, material_id)
    _plan(client, learner_headers, material_id, purpose="exam", days=14)
    body = _plan(client, learner_headers, material_id, purpose="interview",
                 days=7)
    assert body["purpose"] == "interview"
    assert body["timeline_days"] == 7


# ── Settlement ───────────────────────────────────────────────────────────────


def test_webhook_settle_for_materials_grants_no_enrollment(
    client, learner_headers, material_id, coach, db_session
):
    """settle_payment branches: material money flips status and receipts,
    but never fabricates a course enrollment."""
    from sqlalchemy import select

    from app.models import Enrollment
    from app.services import payments as pay

    client.post(f"/v1/me/materials/{material_id}/analyze",
                headers=learner_headers)
    # Pending row, as the purchase endpoint would leave it on Paystack.
    payment = Payment(
        user_id=db_session.query(LearnerMaterial).one().user_id,
        course_id=None,
        material_id=material_id,
        amount_naira=2500,
        currency="NGN",
        status="pending",
        provider="paystack",
        reference="path-ref-1",
    )
    db_session.add(payment)
    db_session.commit()

    settled = pay.settle_payment(db_session, payment, data={
        "amount": 250000, "currency": "NGN", "channel": "card",
        "paid_at": "2026-10-02T10:00:00Z",
    }, source="webhook")
    assert settled is True
    assert payment.status == "success"
    assert db_session.scalar(select(Enrollment).where(
        Enrollment.user_id == payment.user_id)) is None

    # And the unlock is visible to the learner.
    assert pay.path_paid_for(
        db_session, payment.user_id, material_id) is not None


def test_payment_check_rejects_two_items(client, learner_headers, material_id,
                                         creator_headers, db_session):
    from sqlalchemy.exc import IntegrityError

    cid = client.post("/v1/courses", json={
        "title": "Check Course", "description": "For the CHECK test",
        "category": "Design", "outcomes": ["Learn"],
        "target_audience": "Everyone",
    }, headers=creator_headers).json()["id"]
    db_session.add(Payment(
        user_id=db_session.query(LearnerMaterial).one().user_id,
        course_id=cid,
        material_id=material_id,
        amount_naira=2500,
        status="pending",
        provider="paystack",
        reference="bad-ref-1",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
