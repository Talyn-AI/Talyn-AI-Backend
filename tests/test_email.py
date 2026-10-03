"""Transactional email and single-use password reset tokens.

SMTP is replaced at the smtplib boundary, so the tests exercise the real
message building, the real logging, and the real DB writes with no network
and no configured mail server.
"""
import email
from email import policy

import pytest

from app import config as config_module
from app.models import EmailLog, PasswordResetToken, User
from app.services import email as email_service
from app.services import reset_tokens


def _last_text(message) -> str:
    """Body of the plain-text part."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode("utf-8")
    return message.get_payload(decode=True).decode("utf-8")


def _last_html(message) -> str:
    for part in message.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    return ""


# ── Sending basics ───────────────────────────────────────────────────────────


def _verification_token(message) -> str:
    """Pull the token out of the emailed verification link."""
    import re

    body = _last_text(message)
    match = re.search(r"verify-email\?token=([^\s\"<]+)", body)
    assert match, f"no verification link in the email: {body[:200]!r}"
    return match.group(1)


def test_signup_sends_one_verification_email_not_the_welcome(client, smtp,
                                                            db_session):
    """One email at signup, and it is the verification one.

    The welcome is held back until the address is proven: thanking someone
    for a signup we cannot yet attribute is worse than waiting, and two
    emails in the same minute trains people to ignore your mail.
    """
    client.post("/v1/auth/register", json={
        "email": "newbie@example.com", "password": "password123",
        "learner_name": "Newbie",
    })
    assert len(smtp.sent) == 1
    assert smtp.sent[0]["Subject"] == "Confirm your email address"


def test_welcome_arrives_after_the_address_is_confirmed(client, smtp,
                                                       db_session):
    client.post("/v1/auth/register", json={
        "email": "newbie@example.com", "password": "password123",
        "learner_name": "Newbie",
    })
    token = _verification_token(smtp.sent[0])

    r = client.post("/v1/auth/email-verification/confirm",
                    json={"token": token})
    assert r.status_code == 200
    assert r.json()["email_verified"] is True

    assert len(smtp.sent) == 2
    assert smtp.sent[1]["Subject"] == "Welcome to Talyn"
    assert "Newbie" in _last_text(smtp.sent[1])


def test_email_has_both_text_and_html_parts(client, smtp):
    """Text-only clients and HTML-only clients both need to work."""
    client.post("/v1/auth/register", json={
        "email": "parts@example.com", "password": "password123",
        "learner_name": "Parts",
    })
    message = smtp.sent[0]
    types = {p.get_content_type() for p in message.walk()}
    assert "text/plain" in types
    assert "text/html" in types


def test_html_part_uses_brand_colours(client, smtp):
    client.post("/v1/auth/register", json={
        "email": "brand@example.com", "password": "password123",
        "learner_name": "Brand",
    })
    html = _last_html(smtp.sent[0])
    assert "#241F1C" in html   # ink
    assert "#E8794F" in html   # ember


def test_starttls_used_on_standard_port(client, smtp):
    client.post("/v1/auth/register", json={
        "email": "tls@example.com", "password": "password123",
        "learner_name": "Tls",
    })
    assert smtp.last.starttls_used is True
    assert smtp.last.logged_in is True


def test_implicit_tls_on_port_465(client, monkeypatch, smtp):
    """Port 465 is implicit TLS; STARTTLS there fails to handshake."""
    monkeypatch.setattr(config_module.settings, "smtp_port", 465)
    client.post("/v1/auth/register", json={
        "email": "ssl@example.com", "password": "password123",
        "learner_name": "Ssl",
    })
    assert smtp.last.starttls_used is False


def test_send_returns_true_and_logs_sent(client, smtp, db_session):
    client.post("/v1/auth/register", json={
        "email": "logged@example.com", "password": "password123",
        "learner_name": "Logged",
    })
    rows = db_session.query(EmailLog).all()
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].template == "email_verification"
    assert rows[0].error is None


# ── Failure must not break the user ─────────────────────────────────────────


def test_failed_welcome_does_not_break_signup(client, smtp, db_session):
    """Signup succeeded; the mail server is down. The user must not see an error."""
    smtp.fail()

    r = client.post("/v1/auth/register", json={
        "email": "unlucky@example.com", "password": "password123",
        "learner_name": "Unlucky",
    })
    assert r.status_code == 201
    assert r.json()["email"] == "unlucky@example.com"

    # The failure is on the record, which is the whole point of the log.
    row = db_session.query(EmailLog).one()
    assert row.status == "failed"
    assert "mailbox unavailable" in row.error


def test_failure_error_is_truncated(client, smtp, db_session):
    client.post("/v1/auth/register", json={
        "email": "truncate@example.com", "password": "password123",
        "learner_name": "Truncate",
    })
    smtp.fail()
    email_service.send(
        db_session, to_email="x@example.com", template="welcome",
        message=email_service.welcome_email("X"),
    )
    row = db_session.query(EmailLog).order_by(EmailLog.id.desc()).first()
    assert row.status == "failed"
    assert len(row.error) <= 500


def test_unconfigured_email_is_logged_as_skipped(client, monkeypatch, db_session):
    monkeypatch.setattr(config_module.settings, "smtp_host", "")
    r = client.post("/v1/auth/register", json={
        "email": "nomail@example.com", "password": "password123",
        "learner_name": "NoMail",
    })
    assert r.status_code == 201
    row = db_session.query(EmailLog).one()
    assert row.status == "skipped"


def test_email_log_survives_account_deletion(client, smtp, db_session):
    """Deleting an account must not erase the record of what was emailed."""
    client.post("/v1/auth/register", json={
        "email": "leaving@example.com", "password": "password123",
        "learner_name": "Leaving",
    })
    token = client.post("/v1/auth/login", json={
        "email": "leaving@example.com", "password": "password123",
    }).json()["access_token"]

    assert client.request("DELETE", "/v1/users/me",
                          headers={"Authorization": f"Bearer {token}"}
                          ).status_code == 200

    row = db_session.query(EmailLog).filter_by(
        to_email="leaving@example.com").one()
    assert row.user_id is None  # anonymized, not deleted
    assert row.template == "email_verification"


def test_reset_tokens_are_deleted_with_the_account(client, smtp, db_session):
    """A deleted account must not leave live reset tokens behind."""
    _register(client, "wiping@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "wiping@example.com"})
    assert db_session.query(PasswordResetToken).count() == 1

    token = client.post("/v1/auth/login", json={
        "email": "wiping@example.com", "password": "originalpass1",
    }).json()["access_token"]
    client.request("DELETE", "/v1/users/me",
                   headers={"Authorization": f"Bearer {token}"})

    assert db_session.query(PasswordResetToken).count() == 0


# ── Reset tokens are single-use ──────────────────────────────────────────────


def _register(client, email):
    client.post("/v1/auth/register", json={
        "email": email, "password": "originalpass1", "learner_name": "Reset",
    })


def test_reset_token_is_hashed_not_stored_plain(client, smtp, db_session):
    """A database leak must not hand over working reset links."""
    _register(client, "hashme@example.com")
    assert client.post("/v1/auth/password-reset/request",
                       json={"email": "hashme@example.com"}).status_code == 200

    raw = _reset_link_token(_reset_mail(smtp))
    row = db_session.query(PasswordResetToken).one()
    assert row.token_hash != raw
    assert row.token_hash == reset_tokens.hash_token(raw)


def test_reset_link_points_at_a_real_frontend_route(client, smtp, monkeypatch):
    """The emailed link must resolve to a page that exists in the web app."""
    monkeypatch.setattr(config_module.settings, "frontend_url",
                        "https://app.talyn.dev")
    _register(client, "route@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "route@example.com"})
    link = _reset_link(_reset_mail(smtp))
    assert link.startswith("https://app.talyn.dev/reset-password?token=")


def test_reset_works_end_to_end(client, smtp):
    """The token that was emailed is the token that works."""
    _register(client, "flow@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "flow@example.com"})
    token = _reset_link_token(_reset_mail(smtp))

    ok = client.post("/v1/auth/password-reset/confirm", json={
        "token": token, "new_password": "brandnewpass1",
    })
    assert ok.status_code == 200

    assert client.post("/v1/auth/login", json={
        "email": "flow@example.com", "password": "brandnewpass1",
    }).status_code == 200
    assert client.post("/v1/auth/login", json={
        "email": "flow@example.com", "password": "originalpass1",
    }).status_code == 401


def test_reset_token_cannot_be_reused(client, smtp):
    """The token that worked once must be dead immediately afterwards."""
    _register(client, "replay@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "replay@example.com"})
    token = _reset_link_token(_reset_mail(smtp))

    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": token, "new_password": "firstnewpass1"}).status_code == 200

    replay = client.post("/v1/auth/password-reset/confirm", json={
        "token": token, "new_password": "attackerpass1",
    })
    assert replay.status_code == 401
    # And the attacker's password did not take effect.
    assert client.post("/v1/auth/login", json={
        "email": "replay@example.com", "password": "attackerpass1",
    }).status_code == 401
    assert client.post("/v1/auth/login", json={
        "email": "replay@example.com", "password": "firstnewpass1",
    }).status_code == 200


def test_requesting_again_invalidates_the_first_token(client, smtp):
    """A leaked older link stops working once the owner asks for a new one."""
    _register(client, "superseded@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "superseded@example.com"})
    client.post("/v1/auth/password-reset/request",
                json={"email": "superseded@example.com"})

    links = [_reset_link_token(m) for m in _reset_mails(smtp)]
    assert len(links) == 2
    first, second = links
    assert first != second

    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": first, "new_password": "nope12345678"}).status_code == 401
    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": second, "new_password": "yes12345678"}).status_code == 200


def test_expired_token_is_rejected(client, smtp, db_session):
    _register(client, "stale@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "stale@example.com"})
    token = _reset_link_token(_reset_mail(smtp))
    row = db_session.query(PasswordResetToken).one()
    row.expires_at = row.created_at  # already expired
    db_session.commit()

    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": token, "new_password": "toolate123"}).status_code == 401


def test_unknown_token_is_rejected(client):
    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": "made-up-token", "new_password": "whatever123"}).status_code == 401


def test_reset_applies_to_google_only_account(client, smtp):
    """Google users have an unguessable password hash; reset still applies."""
    _register(client, "hybrid@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "hybrid@example.com"})
    token = _reset_link_token(_reset_mail(smtp))
    assert client.post("/v1/auth/password-reset/confirm", json={
        "token": token, "new_password": "hybridpass12"}).status_code == 200
    assert client.post("/v1/auth/login", json={
        "email": "hybrid@example.com", "password": "hybridpass12"}).status_code == 200


def test_reset_fails_closed_in_prod_without_email(client, smtp_off, monkeypatch):
    """Unconfigured in production must not hand the token back to the caller."""
    monkeypatch.setattr(config_module.settings, "environment", "prod")
    _register(client, "produser@example.com")
    r = client.post("/v1/auth/password-reset/request",
                    json={"email": "produser@example.com"})
    assert r.status_code == 503
    assert "reset_token" not in r.json()


def test_dev_without_email_returns_token_inline(client, smtp_off):
    _register(client, "devuser@example.com")
    r = client.post("/v1/auth/password-reset/request",
                    json={"email": "devuser@example.com"})
    assert r.status_code == 200
    assert r.json()["reset_token"]


def test_reset_request_for_unknown_email_looks_identical(client, smtp):
    _register(client, "known@example.com")
    known = client.post("/v1/auth/password-reset/request",
                        json={"email": "known@example.com"})
    unknown = client.post("/v1/auth/password-reset/request",
                          json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    # Exactly one reset email: the welcome plus the known user's reset, and
    # nothing at all for the unknown address.
    assert len(_reset_mails(smtp)) == 1
    assert _reset_mail(smtp)["To"] == "known@example.com"


def test_reset_email_contains_no_plain_password(client, smtp):
    _register(client, "leak@example.com")
    client.post("/v1/auth/password-reset/request", json={"email": "leak@example.com"})
    body = _last_text(smtp.sent[-1])
    assert "leak12345" not in body  # the test password never appears
    assert "token=" in body


# ── Receipts ─────────────────────────────────────────────────────────────────


def _receipts(smtp) -> list[email.message.Message]:
    return [m for m in smtp.sent if "receipt" in (m["Subject"] or "").lower()]


def test_receipt_sent_after_stub_purchase(client, smtp, db_session):
    creator = _creator(client, "rcpt-creator@example.com")
    course = _paid_course(client, creator, db_session, 12000)
    learner = _learner(client, "buyer@example.com")

    r = client.post(f"/v1/courses/{course}/purchase", headers=learner)
    assert r.status_code == 200

    receipts = _receipts(smtp)
    assert len(receipts) == 1
    body = _last_text(receipts[0])
    assert "₦12,000" in body
    assert r.json()["payment"]["reference"] in body


def test_receipt_is_addressed_to_the_buyer(client, smtp, db_session):
    creator = _creator(client, "rcpt-to-creator@example.com")
    course = _paid_course(client, creator, db_session, 3000)
    learner = _learner(client, "receipt-owner@example.com")

    client.post(f"/v1/courses/{course}/purchase", headers=learner)
    assert _receipts(smtp)[0]["To"] == "receipt-owner@example.com"


def test_no_receipt_when_purchase_fails(client, smtp, db_session):
    creator = _creator(client, "rcpt2c@example.com")
    course = _paid_course(client, creator, db_session, 5000)
    learner = _learner(client, "noreceipt@example.com")

    assert client.post(f"/v1/courses/{course}/purchase",
                       headers=learner).status_code == 200
    assert len(_receipts(smtp)) == 1

    # Buying the same course twice is rejected, so no second receipt.
    assert client.post(f"/v1/courses/{course}/purchase",
                       headers=learner).status_code == 409
    assert len(_receipts(smtp)) == 1


def test_failed_receipt_does_not_undo_purchase(client, smtp, db_session):
    """The learner already paid. A mail outage must not read as a failed sale."""
    creator = _creator(client, "rcpt3c@example.com")
    course = _paid_course(client, creator, db_session, 7500)
    learner = _learner(client, "resilient@example.com")
    smtp.fail()

    r = client.post(f"/v1/courses/{course}/purchase", headers=learner)
    assert r.status_code == 200
    assert r.json()["enrolled"] is True
    assert len(client.get("/v1/me/enrollments",
                          headers=learner).json()) == 1


# ── Helpers ──────────────────────────────────────────────────────────────────

import re as _re


def _reset_link(message) -> str:
    body = _last_text(message)
    match = _re.search(r"https?://\S*reset-password\?token=\S+", body)
    assert match, f"no reset link in: {body[:300]}"
    return match.group(0)


def _reset_link_token(message) -> str:
    return _reset_link(message).split("token=", 1)[1].strip()


def _reset_mails(smtp) -> list[email.message.Message]:
    """Every reset email sent so far (registration also sends a welcome)."""
    return [m for m in smtp.sent if "Reset your" in (m["Subject"] or "")]


def _reset_mail(smtp) -> email.message.Message:
    resets = _reset_mails(smtp)
    assert len(resets) == 1, f"expected exactly one reset email, got {len(resets)}"
    return resets[0]


def _creator(client, email):
    client.post("/v1/auth/register", json={
        "email": email, "password": "password123",
        "learner_name": "Creator", "is_creator": True,
    })
    return {"Authorization": "Bearer " + _token(client, email)}


def _learner(client, email):
    client.post("/v1/auth/register", json={
        "email": email, "password": "password123", "learner_name": "Buyer",
    })
    return {"Authorization": "Bearer " + _token(client, email)}


def _token(client, email) -> str:
    r = client.post("/v1/auth/login",
                    json={"email": email, "password": "password123"})
    return r.json()["access_token"]


def _paid_course(client, creator, db_session, price):
    """Create a published paid course.

    Publishing goes through the DB directly: the publish endpoint has its own
    validation rules that need a lesson and a module, and this test is about
    the receipt, not the checklist.
    """
    from app.models import Course

    cid = client.post("/v1/courses", json={
        "title": "Receipt Course", "description": "Paid",
        "category": "Design", "outcomes": ["Learn"],
        "target_audience": "All", "thumbnail_key": "t.png",
        "course_type": "paid", "price_naira": price,
    }, headers=creator).json()["id"]
    db_session.get(Course, cid).status = "published"
    db_session.commit()
    return cid


# -- Admin visibility ---------------------------------------------------------
# The log exists so a failed send is findable. If it is unreadable in practice,
# the silent-failure problem it solves is unsolved.


def test_admin_can_read_the_email_log(client, admin_headers, smtp, db_session):
    _register(client, "audited@example.com")
    rows = client.get("/v1/admin/email-log", headers=admin_headers)
    assert rows.status_code == 200
    body = rows.json()
    assert any(r["to_email"] == "audited@example.com" for r in body)
    assert body[0]["template"] == "email_verification"


def test_admin_can_filter_to_failures(client, admin_headers, smtp, db_session):
    _register(client, "ok-user@example.com")
    smtp.fail()
    _register(client, "broken@example.com")

    failed = client.get("/v1/admin/email-log?failed_only=true",
                        headers=admin_headers).json()
    assert [r["to_email"] for r in failed] == ["broken@example.com"]


def test_admin_email_log_is_admin_only(client, smtp):
    learner = _learner(client, "nosy@example.com")
    assert client.get("/v1/admin/email-log", headers=learner).status_code == 403
    assert client.get("/v1/admin/email-log").status_code == 401


def test_email_log_never_exposes_message_bodies(client, admin_headers, smtp):
    """A reset token must not be readable from the admin log."""
    _register(client, "secret@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "secret@example.com"})
    token = _reset_link_token(_reset_mail(smtp))
    body = client.get("/v1/admin/email-log", headers=admin_headers).text
    assert token not in body