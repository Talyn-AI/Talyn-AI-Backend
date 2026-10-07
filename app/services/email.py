"""Transactional email, over SMTP or the Resend HTTPS API.

SMTP works with any provider that speaks it: Resend, Postmark, SendGrid,
SES, Mailgun, Gmail. Provider-specific API keys go in
SMTP_USERNAME/SMTP_PASSWORD.

The Resend API path exists because some hosts block SMTP ports outright
(Render's free tier blocks 25/465/587) while HTTPS is always open. Set
RESEND_API_KEY and it is used instead of SMTP — same templates, same
logging, only the transport changes.

Three deliberate choices:

  * **HTML plus a plain-text alternative.** Mail clients are a zoo; the text
    part is what shows up in the ones that refuse HTML.
  * **Every attempt is logged**, including failures. Email is the most
    commonly silent failure in a stack like this, and "no complaints" is not
    the same as "delivered".
  * **Sending never raises into business logic.** A failed receipt must not
    roll back a paid enrollment; a failed welcome must not block signup. The
    caller decides what is fatal (password reset is) and what is not
    (everything else).

Templates are inline rather than in files so the whole email — subject,
copy, links — is readable in one place and cannot drift from the code that
decides which links to include.
"""
import html
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_log import FAILED, SENT, SKIPPED, EmailLog

log = logging.getLogger("talyn.email")

RESEND_API_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    """Sending failed (misconfigured or upstream error)."""


# ── Brand ────────────────────────────────────────────────────────────────────
# Matches the web app: ink #241F1C, ember #E8794F. Kept as literals because
# email clients strip <style> blocks and external stylesheets.

_INK = "#241F1C"
_EMBER = "#E8794F"
_MUTED = "#6B625C"
_BORDER = "#E8E2DC"


def is_configured() -> bool:
    """True when any email provider is configured (Resend API or SMTP)."""
    return bool(settings.resend_api_key or settings.smtp_host)


def _smtp_settings() -> dict:
    return {
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "username": settings.smtp_username,
        "password": settings.smtp_password,
        "sender": settings.smtp_from,
        # Port 465 is implicit TLS; 587 is STARTTLS. Getting this wrong is the
        # single most common SMTP setup mistake, so infer rather than trust.
        "use_ssl": settings.smtp_port == 465,
        "use_starttls": settings.smtp_port != 465 and settings.smtp_port != 25,
    }


# ── Message building ─────────────────────────────────────────────────────────


def _layout(heading: str, intro: str, blocks: list[str], cta_label: str = "",
            cta_url: str = "", footnote: str = "") -> str:
    """Wrap content in the shared shell: logo, ink on white, ember CTA."""
    button = ""
    if cta_label and cta_url:
        button = (
            f'<p style="margin:28px 0;">'
            f'<a href="{html.escape(cta_url)}" '
            f'style="background:{_EMBER};color:#fff;text-decoration:none;'
            f'padding:13px 26px;border-radius:999px;font-weight:600;'
            f'display:inline-block;">{html.escape(cta_label)}</a></p>'
            # The link has to survive clients that strip the anchor.
            f'<p style="font-size:12px;color:{_MUTED};word-break:break-all;">'
            f'{html.escape(cta_url)}</p>'
        )

    sections = "".join(
        f'<div style="margin:0 0 18px;">{b}</div>' for b in blocks
    )
    foot = (
        f'<p style="font-size:12px;color:{_MUTED};margin-top:32px;">'
        f'{html.escape(footnote)}</p>' if footnote else ""
    )

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#FAF8F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#FAF8F6;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="max-width:560px;background:#fff;border:1px solid {_BORDER};
              border-radius:16px;padding:32px;">
  <tr><td>
    <p style="margin:0 0 24px;font-size:20px;font-weight:700;color:{_INK};">
      Talyn
    </p>
    <h1 style="margin:0 0 12px;font-size:22px;line-height:1.3;color:{_INK};
               font-weight:700;">{html.escape(heading)}</h1>
    <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:{_INK};">
      {html.escape(intro)}
    </p>
    {sections}
    {button}
    {foot}
  </td></tr>
</table>
<p style="font-size:12px;color:{_MUTED};margin-top:16px;">
  You are receiving this because you have a Talyn account.
</p>
</td></tr>
</table>
</body></html>"""


def _para(text: str) -> str:
    return (
        f'<p style="margin:0;font-size:15px;line-height:1.6;color:{_INK};">'
        f"{text}</p>"
    )


def _code_block(code: str) -> str:
    """A typed-in code, displayed large. The code is server-generated digits,
    so there is nothing to escape — and nothing to inject."""
    return (
        f'<p style="margin:4px 0 8px;padding:18px 16px;background:#FDF4F0;'
        f'border-left:3px solid {_EMBER};border-radius:0 8px 8px 0;'
        f'font-size:30px;font-weight:700;letter-spacing:10px;'
        f'color:{_INK};">{code}</p>'
    )


def _note(text: str) -> str:
    return (
        f'<p style="margin:0;padding:14px 16px;background:#FDF4F0;'
        f'border-left:3px solid {_EMBER};border-radius:0 8px 8px 0;'
        f'font-size:14px;line-height:1.6;color:{_INK};">{html.escape(text)}</p>'
    )


def _list(items: list[str]) -> str:
    rows = "".join(
        f'<li style="margin:0 0 8px;font-size:15px;line-height:1.5;color:{_INK};">'
        f"{html.escape(i)}</li>"
        for i in items
    )
    return f'<ul style="margin:0;padding-left:20px;">{rows}</ul>'


# ── Templates ────────────────────────────────────────────────────────────────
# Each returns (subject, html, text).

TEMPLATE_WELCOME = "welcome"
TEMPLATE_PASSWORD_RESET = "password_reset"
TEMPLATE_RECEIPT = "purchase_receipt"
TEMPLATE_PATH_RECEIPT = "path_receipt"
TEMPLATE_VERIFICATION = "email_verification"


def welcome_email(name: str) -> tuple[str, str, str]:
    subject = "Welcome to Talyn"
    html = _layout(
        "Welcome, " + name,
        "Your account is ready. Everything on Talyn is built around one thing: "
        "helping you actually finish what you start.",
        [
            _para("A few things worth doing first:"),
            _list([
                "Pick a course from Discover and start the first lesson.",
                "Set a daily goal on your Study Plan so streaks build themselves.",
                "Ask the Coach anything — it already knows your progress.",
            ]),
        ],
        cta_label="Browse courses",
        cta_url=f"{settings.frontend_url}/discover",
        footnote="Not seeing this in your inbox? Check your spam folder.",
    )
    text = (
        f"Welcome, {name}\n\n"
        "Your Talyn account is ready.\n\n"
        "A few things worth doing first:\n"
        "  - Pick a course from Discover and start the first lesson.\n"
        "  - Set a daily goal on your Study Plan so streaks build themselves.\n"
        "  - Ask the Coach anything — it already knows your progress.\n\n"
        f"Browse courses: {settings.frontend_url}/discover\n\n"
        "Not seeing this in your inbox? Check your spam folder."
    )
    return subject, html, text


def password_reset_email(code: str) -> tuple[str, str, str]:
    """Reset email carrying the code. No link: verification is typed in."""
    minutes = 15
    subject = "Reset your Talyn password"
    html = _layout(
        "Reset your password",
        f"Enter this code to choose a new password. It works once and "
        f"expires in {minutes} minutes.",
        [
            _code_block(code),
        ],
        footnote=(
            "If you did not ask for this, ignore this email. Your password "
            "stays as it is."
        ),
    )
    text = (
        "Reset your Talyn password\n\n"
        f"Your code: {code}\n\n"
        f"Enter it to choose a new password. It works once and expires in "
        f"{minutes} minutes.\n\n"
        "If you did not ask for this, ignore this email — your password "
        "stays as it is."
    )
    return subject, html, text


def purchase_receipt_email(
    name: str, course_title: str, amount_naira: int, reference: str
) -> tuple[str, str, str]:
    subject = f"Your receipt — {course_title}"
    amount = f"₦{amount_naira:,}"
    html = _layout(
        "Payment received",
        f"Thanks, {name}. You're enrolled in {course_title} and the full "
        f"content is unlocked.",
        [
            _list([
                f"Course: {course_title}",
                f"Amount paid: {amount}",
                f"Reference: {reference}",
            ]),
        ],
        cta_label="Start learning",
        cta_url=f"{settings.frontend_url}/discover",
        footnote="Keep this reference for any billing questions.",
    )
    text = (
        f"Payment received\n\n"
        f"Thanks, {name}. You're enrolled in {course_title}.\n\n"
        f"  Course:    {course_title}\n"
        f"  Amount:    {amount}\n"
        f"  Reference: {reference}\n\n"
        f"Start learning: {settings.frontend_url}/discover\n\n"
        "Keep this reference for any billing questions."
    )
    return subject, html, text


def path_receipt_email(
    name: str, material_title: str, amount_naira: int, reference: str
) -> tuple[str, str, str]:
    """Receipt for a study-schedule unlock. The schedule is permanent: the
    14 days shape the plan, never gate it, so the email says so plainly.
    """
    subject = f"Your receipt — {material_title}"
    amount = f"₦{amount_naira:,}"
    start_url = f"{settings.frontend_url}/dashboard"
    html = _layout(
        "Payment received",
        f"Thanks, {name}. Your 14-day study schedule for {material_title} "
        f"is unlocked — and it is yours to keep, no expiry.",
        [
            _list([
                f"Material: {material_title}",
                f"Amount paid: {amount}",
                f"Reference: {reference}",
            ]),
        ],
        cta_label="Start learning",
        cta_url=start_url,
        footnote="Keep this reference for any billing questions.",
    )
    text = (
        f"Payment received\n\n"
        f"Thanks, {name}. Your 14-day study schedule for {material_title} "
        f"is unlocked, and yours to keep.\n\n"
        f"  Material: {material_title}\n"
        f"  Amount:    {amount}\n"
        f"  Reference: {reference}\n\n"
        f"Start learning: {start_url}\n\n"
        "Keep this reference for any billing questions."
    )
    return subject, html, text


def verification_code_email(code: str) -> tuple[str, str, str]:
    """Signup verification carrying the code. No link: verification is typed in."""
    minutes = 15
    subject = "Confirm your email address"
    html = _layout(
        "Confirm your email",
        f"Enter this code to confirm your address. It works once and "
        f"expires in {minutes} minutes.",
        [
            _code_block(code),
        ],
        footnote=(
            "If you did not sign up for Talyn, ignore this email — nothing "
            "happens and no account was created."
        ),
    )
    text = (
        "Confirm your email address\n\n"
        f"Your code: {code}\n\n"
        f"Enter it to confirm your address. It works once and expires in "
        f"{minutes} minutes.\n\n"
        "If you did not sign up for Talyn, ignore this email — nothing "
        "happens and no account was created."
    )
    return subject, html, text


# ── Sending ──────────────────────────────────────────────────────────────────


def send(
    db: Session,
    *,
    to_email: str,
    template: str,
    message: tuple[str, str, str],
    user_id: int | None = None,
) -> bool:
    """Send one message and log the outcome. Never raises EmailError.

    Returns True only when the provider accepted the message. Business
    logic decides whether that matters — see the module docstring.
    """
    subject, html_body, text_body = message

    if not is_configured():
        log.warning("email not configured; skipped %s to %s", template, to_email)
        _record(db, user_id, to_email, template, subject, SKIPPED,
                "No email provider configured (RESEND_API_KEY or SMTP_HOST)")
        return False

    if settings.resend_api_key:
        try:
            _send_via_resend(to_email, subject, html_body, text_body)
        except EmailError as e:
            # Logged, not raised: a failed receipt must not undo a paid purchase.
            log.error("email send failed (%s to %s): %s", template, to_email, e)
            _record(db, user_id, to_email, template, subject, FAILED,
                    str(e)[:500])
            return False
        _record(db, user_id, to_email, template, subject, SENT, None)
        return True

    cfg = _smtp_settings()
    message_out = EmailMessage()
    message_out["From"] = cfg["sender"]
    message_out["To"] = to_email
    message_out["Subject"] = subject
    # Order matters: the plain-text part must come first or some clients
    # render the HTML as an attachment.
    message_out.set_content(text_body)
    message_out.add_alternative(html_body, subtype="html")

    try:
        if cfg["use_ssl"]:
            with smtplib.SMTP_SSL(
                cfg["host"], cfg["port"], timeout=15
            ) as smtp:
                _authenticate_and_send(smtp, cfg, message_out)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
                if cfg["use_starttls"]:
                    smtp.starttls()
                _authenticate_and_send(smtp, cfg, message_out)
    except (smtplib.SMTPException, OSError) as e:
        # Logged, not raised: a failed receipt must not undo a paid purchase.
        log.error("email send failed (%s to %s): %s", template, to_email, e)
        _record(db, user_id, to_email, template, subject, FAILED, str(e)[:500])
        return False

    _record(db, user_id, to_email, template, subject, SENT, None)
    return True


def _send_via_resend(
    to_email: str, subject: str, html_body: str, text_body: str
) -> None:
    """Deliver through the Resend HTTPS API. Raises EmailError on failure.

    The sender address must be verified in the Resend dashboard — Resend
    rejects unverified senders, and test-mode keys only deliver to the
    account's own address.
    """
    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.smtp_from,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise EmailError(f"Resend request failed: {e}") from e
    if response.status_code >= 400:
        raise EmailError(
            f"Resend rejected the message "
            f"({response.status_code}): {response.text[:200]}"
        )


def _authenticate_and_send(smtp: Any, cfg: dict, message: EmailMessage) -> None:
    if cfg["username"]:
        smtp.login(cfg["username"], cfg["password"])
    smtp.send_message(message)


def _record(
    db: Session,
    user_id: int | None,
    to_email: str,
    template: str,
    subject: str,
    status: str,
    error: str | None,
) -> None:
    """Persist the attempt. Never raises — losing the audit row must not turn
    a failed send into a failed request."""
    try:
        db.add(EmailLog(
            user_id=user_id,
            to_email=to_email,
            template=template,
            subject=subject[:200],
            status=status,
            error=error,
        ))
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        log.exception("could not write email log for %s", template)


def send_or_raise(
    db: Session,
    *,
    to_email: str,
    template: str,
    message: tuple[str, str, str],
    user_id: int | None = None,
) -> None:
    """Send, and raise EmailError on failure.

    Only for flows where an undelivered email leaves the user stuck — today
    that is password reset and nothing else.
    """
    if not is_configured():
        _record(db, user_id, to_email, template, message[0], SKIPPED,
                "No email provider configured (RESEND_API_KEY or SMTP_HOST)")
        raise EmailError(
            "Email is not configured (RESEND_API_KEY or SMTP_HOST)")
    if not send(db, to_email=to_email, template=template, message=message,
                user_id=user_id):
        raise EmailError("Email could not be sent")


def send_password_reset_email(to_email: str, code: str) -> None:
    """Backwards-compatible wrapper kept for existing callers."""
    subject, html_body, text_body = password_reset_email(code)
    if not is_configured():
        raise EmailError(
            "Email is not configured (RESEND_API_KEY or SMTP_HOST)")
    if settings.resend_api_key:
        _send_via_resend(to_email, subject, html_body, text_body)
        return
    cfg = _smtp_settings()
    message = EmailMessage()
    message["From"] = cfg["sender"]
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
            if cfg["use_starttls"]:
                smtp.starttls()
            _authenticate_and_send(smtp, cfg, message)
    except (smtplib.SMTPException, OSError) as e:
        raise EmailError(f"SMTP send failed: {e}") from e
