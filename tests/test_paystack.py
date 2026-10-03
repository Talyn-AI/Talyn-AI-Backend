"""Paystack checkout: redirect, verify, and signed-webhook settlement.

No network. The provider is faked at the httpx transport boundary so the
tests exercise real serialization, real signature math, and real DB writes.
"""
import pytest
import hashlib
import hmac
import json

import httpx
import pytest

from app import config as config_module
from app.services import payments as pay

SECRET = "sk_test_fixture_key_no_real_money_here"


@pytest.fixture
def paystack_on(monkeypatch):
    """Enable the real provider against a stubbed transport.

    Installs a default handler so a test only overrides it when it cares
    what the provider answers. Always mocked: a test that forgot to stub
    would otherwise reach the real network and hang.
    """
    monkeypatch.setattr(config_module.settings, "paystack_secret_key", SECRET)
    monkeypatch.setattr(config_module.settings, "paystack_api_url", "https://pay.test")
    monkeypatch.setattr(config_module.settings, "payment_return_url",
                        "http://localhost:3000/checkout/callback")
    _patch_provider(
        monkeypatch,
        lambda req: _ok({"authorization_url": "https://checkout.paystack.test/mock"}),
    )


def _patch_provider(monkeypatch, handler):
    """Serve the payments service from `handler` instead of the network.

    Patches the service's own client factory, not httpx.Client globally —
    FastAPI's TestClient is an httpx.Client subclass and must keep working.
    """
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(pay, "_client", lambda: _TransportBoundClient(calls, handle))
    return calls


class _TransportBoundClient:
    """Minimal context-manager client that answers via `handle`."""

    def __init__(self, calls, handle):
        self._calls = calls
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, path, json=None):
        request = httpx.Request("POST", f"https://pay.test{path}",
                                json=json,
                                headers={"Content-Type": "application/json"})
        return self._handle(request)

    def get(self, path):
        return self._handle(httpx.Request("GET", f"https://pay.test{path}"))


def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": True, "message": "ok", "data": data})


def _charge_success(*, reference: str, kobo: int) -> dict:
    return {
        "reference": reference,
        "status": "success",
        "amount": kobo,
        "currency": "NGN",
        "channel": "card",
        "paid_at": "2026-09-30T12:00:00.000Z",
    }


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


# ── Unit: signature ──────────────────────────────────────────────────────────


def test_signature_accepts_valid_body(paystack_on):
    body = b'{"event":"charge.success"}'
    assert pay.signature_matches(body, _sign(body))


def test_signature_rejects_tampered_body(paystack_on):
    body = b'{"event":"charge.success"}'
    assert not pay.signature_matches(body + b" ", _sign(body))


def test_signature_rejects_wrong_secret(paystack_on):
    body = b'{"event":"charge.success"}'
    assert not pay.signature_matches(body, _sign(body, "sk_test_wrong"))


def test_signature_rejects_missing_header(paystack_on):
    assert not pay.signature_matches(b"{}", "")


def test_signature_fails_closed_without_secret(monkeypatch):
    monkeypatch.setattr(config_module.settings, "paystack_secret_key", "")
    assert not pay.signature_matches(b"{}", "anything")


def test_webhook_secret_overrides_api_key(paystack_on, monkeypatch):
    monkeypatch.setattr(config_module.settings, "paystack_webhook_secret", "hook_only")
    body = b"{}"
    assert pay.signature_matches(body, _sign(body, "hook_only"))
    assert not pay.signature_matches(body, _sign(body, SECRET))


# ── Unit: amount / currency guards ───────────────────────────────────────────


class _FakePayment:
    def __init__(self, amount_naira=12000):
        self.amount_naira = amount_naira


def test_amount_matches_kobo_conversion():
    assert pay.amount_matches(_FakePayment(12000), {"amount": 1_200_000})
    assert not pay.amount_matches(_FakePayment(12000), {"amount": 12000})
    assert not pay.amount_matches(_FakePayment(12000), {"amount": 1_200_001})


def test_amount_matches_rejects_underpayment():
    """A real charge for less than the price must not unlock content."""
    assert not pay.amount_matches(_FakePayment(12000), {"amount": 100_000})


def test_amount_matches_rejects_garbage():
    assert not pay.amount_matches(_FakePayment(12000), {"amount": "lots"})
    assert not pay.amount_matches(_FakePayment(12000), {})


def test_currency_matches():
    assert pay.currency_matches({"currency": "NGN"})
    assert not pay.currency_matches({"currency": "USD"})
    assert not pay.currency_matches({})


def test_purchase_creates_pending_payment_and_returns_url(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    calls = _patch_provider(monkeypatch, lambda req: _ok({
        "authorization_url": "https://checkout.paystack.test/abc123",
        "reference": "ignored",
    }))

    r = client.post(f"/v1/courses/{paid_course_id}/purchase",
                    headers=paystack_learner_headers)
    assert r.status_code == 200, r.text
    body = r.json()

    # Nothing is unlocked until the money is confirmed.
    assert body["enrolled"] is False
    assert body["checkout_url"] == "https://checkout.paystack.test/abc123"
    assert body["payment"]["status"] == "pending"
    assert body["payment"]["provider"] == "paystack"
    assert body["payment"]["amount_naira"] == 12000

    # Amount sent to Paystack is in kobo (12000 Naira -> 1.2M kobo).
    init = [c for c in calls if c.url.path.endswith("/transaction/initialize")][0]
    sent = json.loads(init.content)
    assert sent["amount"] == 1_200_000
    assert sent["currency"] == "NGN"
    assert sent["reference"] == body["payment"]["reference"]
    assert sent["callback_url"].startswith("http://localhost:3000/checkout/callback")

    # Access is still gated.
    rows = client.get("/v1/me/enrollments", headers=paystack_learner_headers).json()
    assert rows == []


def test_purchase_reuses_in_flight_payment(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    calls = _patch_provider(monkeypatch, lambda req: _ok({
        "authorization_url": "https://checkout.paystack.test/reuse",
    }))
    first = client.post(f"/v1/courses/{paid_course_id}/purchase",
                        headers=paystack_learner_headers).json()
    second = client.post(f"/v1/courses/{paid_course_id}/purchase",
                         headers=paystack_learner_headers).json()

    assert second["payment"]["reference"] == first["payment"]["reference"]
    # Only one transaction was actually initialized at the provider.
    assert len([c for c in calls if c.url.path.endswith("/initialize")]) == 1


def test_purchase_cleans_up_when_provider_fails(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    _patch_provider(monkeypatch, lambda req: httpx.Response(
        500, json={"status": False, "message": "Server error"}))

    r = client.post(f"/v1/courses/{paid_course_id}/purchase",
                    headers=paystack_learner_headers)
    assert r.status_code == 502
    assert "checkout" in r.json()["detail"].lower()

    # No orphan pending payment left behind.
    status = client.get(f"/v1/courses/{paid_course_id}/payment",
                        headers=paystack_learner_headers)
    assert status.status_code == 404


# ── Verify: the redirect-back path ───────────────────────────────────────────


# ── Purchase returns a checkout URL, unlocks nothing ─────────────────────────


def _register(client, email, name, is_creator=False):
    client.post("/v1/auth/register",
                json={"email": email, "password": "secret12345",
                      "learner_name": name, "is_creator": is_creator})
    r = client.post("/v1/auth/login",
                    json={"email": email, "password": "secret12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def pay_creator_headers(client):
    return _register(client, "paystack-creator@example.com", "PayCreator",
                     is_creator=True)


@pytest.fixture()
def paystack_learner_headers(client):
    return _register(client, "paystack-learner@example.com", "PayLearner")


@pytest.fixture()
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


def _pending(client, headers, course_id):
    """Start a purchase and return the created payment reference."""
    return client.post(f"/v1/courses/{course_id}/purchase",
                       headers=headers).json()


def test_verify_confirms_payment_and_enrolls(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok(
        _charge_success(reference=ref, kobo=1_200_000)))

    r = client.post(f"/v1/courses/{paid_course_id}/verify",
                    params={"reference": ref}, headers=paystack_learner_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["enrolled"] is True

    # Enrolled with purchase-level access, which is what unlocks paid lessons.
    rows = client.get("/v1/me/enrollments", headers=paystack_learner_headers).json()
    assert len(rows) == 1
    assert rows[0]["course_id"] == paid_course_id


def test_verify_rejects_abandoned_checkout(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok({
        "reference": ref, "status": "abandoned", "amount": 1_200_000,
        "currency": "NGN",
    }))

    r = client.post(f"/v1/courses/{paid_course_id}/verify",
                    params={"reference": ref}, headers=paystack_learner_headers)
    assert r.status_code == 409
    # Still locked out.
    assert client.get("/v1/me/enrollments", headers=paystack_learner_headers).json() == []


def test_verify_rejects_underpayment(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    """Confirmed charge for the wrong amount must not unlock content."""
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok(
        _charge_success(reference=ref, kobo=100)))

    r = client.post(f"/v1/courses/{paid_course_id}/verify",
                    params={"reference": ref}, headers=paystack_learner_headers)
    assert r.status_code == 409
    assert client.get("/v1/me/enrollments", headers=paystack_learner_headers).json() == []


def test_verify_cannot_use_another_learners_reference(
    client, paid_course_id, paystack_on, monkeypatch, pay_creator_headers
):
    """A reference is only honoured for the learner who created it."""
    session = _pending(client, pay_creator_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok(
        _charge_success(reference=ref, kobo=1_200_000)))

    attacker = _register(client, "mallory@example.com", "Mallory")

    r = client.post(f"/v1/courses/{paid_course_id}/verify",
                    params={"reference": ref}, headers=attacker)
    # Either rejected outright or resolved to nothing of hers — never enrolled.
    assert r.status_code in (404, 409)
    assert client.get("/v1/me/enrollments", headers=attacker).json() == []


def test_verify_is_idempotent(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok(
        _charge_success(reference=ref, kobo=1_200_000)))

    first = client.post(f"/v1/courses/{paid_course_id}/verify",
                        params={"reference": ref}, headers=paystack_learner_headers)
    second = client.post(f"/v1/courses/{paid_course_id}/verify",
                         params={"reference": ref}, headers=paystack_learner_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["enrolled"] is True
    # One enrollment, not two.
    assert len(client.get("/v1/me/enrollments",
                          headers=paystack_learner_headers).json()) == 1


def test_payment_status_reports_pending(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    _patch_provider(monkeypatch, lambda req: _ok({
        "authorization_url": "https://checkout.paystack.test/x"}))
    session = _pending(client, paystack_learner_headers, paid_course_id)

    r = client.get(f"/v1/courses/{paid_course_id}/payment",
                   headers=paystack_learner_headers)
    assert r.json()["status"] == "pending"
    assert r.json()["enrolled"] is False
    assert r.json()["reference"] == session["payment"]["reference"]


def test_verify_requires_auth(client, paid_course_id, paystack_on):
    assert client.post(f"/v1/courses/{paid_course_id}/verify").status_code == 401


# ── Webhook ──────────────────────────────────────────────────────────────────


def test_webhook_settles_payment(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]

    body = json.dumps({
        "event": "charge.success",
        "data": _charge_success(reference=ref, kobo=1_200_000),
    }).encode()

    r = client.post("/v1/payments/webhooks/paystack", content=body,
                    headers={"x-paystack-signature": _sign(body),
                             "content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True, "settled": True}

    assert len(client.get("/v1/me/enrollments",
                          headers=paystack_learner_headers).json()) == 1


def test_webhook_rejects_unsigned_request(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    _pending(client, paystack_learner_headers, paid_course_id)
    body = json.dumps({"event": "charge.success",
                       "data": {"reference": "tln_x", "status": "success",
                                "amount": 1_200_000, "currency": "NGN"}}).encode()

    r = client.post("/v1/payments/webhooks/paystack", content=body,
                    headers={"content-type": "application/json"})
    assert r.status_code == 401
    assert client.get("/v1/me/enrollments", headers=paystack_learner_headers).json() == []


def test_webhook_rejects_forged_signature(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    _pending(client, paystack_learner_headers, paid_course_id)
    body = json.dumps({"event": "charge.success",
                       "data": {"reference": "tln_x", "status": "success",
                                "amount": 1_200_000, "currency": "NGN"}}).encode()

    r = client.post("/v1/payments/webhooks/paystack", content=body,
                    headers={"x-paystack-signature": _sign(body, "sk_live_stolen"),
                             "content-type": "application/json"})
    assert r.status_code == 401


def test_webhook_ignores_unknown_reference(client, paystack_on):
    body = json.dumps({"event": "charge.success",
                       "data": _charge_success(reference="tln_unknown",
                                               kobo=1_200_000)}).encode()
    r = client.post("/v1/payments/webhooks/paystack", content=body,
                    headers={"x-paystack-signature": _sign(body),
                             "content-type": "application/json"})
    # Acknowledge so Paystack stops retrying, but grant nothing.
    assert r.status_code == 200
    assert r.json()["settled"] is False


def test_webhook_ignores_failed_charge(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    body = json.dumps({
        "event": "charge.failed",
        "data": {"reference": ref, "status": "failed", "amount": 1_200_000,
                 "currency": "NGN"},
    }).encode()

    r = client.post("/v1/payments/webhooks/paystack", content=body,
                    headers={"x-paystack-signature": _sign(body),
                             "content-type": "application/json"})
    assert r.status_code == 200
    assert r.json()["settled"] is False
    assert client.get("/v1/me/enrollments", headers=paystack_learner_headers).json() == []


def test_webhook_and_verify_do_not_double_grant(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    """Both paths can land for one payment; access is granted exactly once."""
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    _patch_provider(monkeypatch, lambda req: _ok(
        _charge_success(reference=ref, kobo=1_200_000)))

    body = json.dumps({"event": "charge.success",
                       "data": _charge_success(reference=ref,
                                               kobo=1_200_000)}).encode()
    hook = client.post("/v1/payments/webhooks/paystack", content=body,
                       headers={"x-paystack-signature": _sign(body),
                                "content-type": "application/json"})
    verify = client.post(f"/v1/courses/{paid_course_id}/verify",
                         params={"reference": ref}, headers=paystack_learner_headers)
    hook_again = client.post("/v1/payments/webhooks/paystack", content=body,
                             headers={"x-paystack-signature": _sign(body),
                                      "content-type": "application/json"})

    assert hook.json()["settled"] is True
    assert verify.json()["enrolled"] is True
    assert hook_again.json()["settled"] is False
    assert len(client.get("/v1/me/enrollments",
                          headers=paystack_learner_headers).json()) == 1


def test_webhook_survives_closed_browser(
    client, paystack_learner_headers, paid_course_id, paystack_on, monkeypatch
):
    """The learner never returns from Paystack; the webhook still settles."""
    session = _pending(client, paystack_learner_headers, paid_course_id)
    ref = session["payment"]["reference"]
    body = json.dumps({"event": "charge.success",
                       "data": _charge_success(reference=ref,
                                               kobo=1_200_000)}).encode()

    client.post("/v1/payments/webhooks/paystack", content=body,
                headers={"x-paystack-signature": _sign(body),
                         "content-type": "application/json"})

    status = client.get(f"/v1/courses/{paid_course_id}/payment",
                        headers=paystack_learner_headers).json()
    assert status["enrolled"] is True
    assert status["status"] == "success"


# ── Config guard ─────────────────────────────────────────────────────────────


def _prod_settings(**overrides):
    """A prod-shaped Settings instance, independent of the local .env.

    Built rather than monkeypatched so the assertions depend only on the
    values passed here and not on whatever SECRET_KEY the developer's machine
    happens to have.
    """
    from app.config import Settings

    base = {
        "environment": "prod",
        "database_url": "postgresql+psycopg://x/y",
        # Long enough to clear the strength check, so these tests exercise the
        # payment guard and not the secret-length guard.
        "secret_key": "a-strong-random-value-with-more-than-32-chars",
        "paystack_secret_key": "",
        "allow_stub_payments": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_non_dev_refuses_to_boot_without_paystack():
    with pytest.raises(RuntimeError, match="PAYSTACK_SECRET_KEY"):
        _prod_settings().ensure_production_secrets()


def test_non_dev_allows_explicit_stub_opt_in():
    _prod_settings(allow_stub_payments=True).ensure_production_secrets()


def test_non_dev_boots_with_a_real_paystack_key():
    _prod_settings(paystack_secret_key="sk_live_x").ensure_production_secrets()


def test_short_secret_still_caught_before_payments():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _prod_settings(secret_key="too-short").ensure_production_secrets()


def test_dev_allows_missing_paystack(monkeypatch):
    monkeypatch.setattr(config_module.settings, "environment", "dev")
    monkeypatch.setattr(config_module.settings, "paystack_secret_key", "")
    config_module.settings.ensure_production_secrets()  # must not raise


def test_callback_url_points_at_frontend(paystack_on):
    url = config_module.settings.payment_callback_url(7)
    assert url.startswith("http://localhost:3000/checkout/callback")
    assert "course_id=7" in url
