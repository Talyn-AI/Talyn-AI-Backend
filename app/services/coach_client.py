"""Calls from the backend to the AI coach service.

The usual direction is reversed here: the coach always pulls learner context
from the backend, but document analysis starts from bytes only the backend
holds. So the backend extracts text, mints a short-lived learner token for
itself, and asks the coach to reason over the text.

Empty coach_base_url means the feature is unavailable: every caller fails
closed (503) rather than half-working. Timeouts are generous — schedule
generation writes thousands of tokens — but bounded, because an unbounded
call on a free-tier container is a stuck worker.
"""
import httpx

from app import config as config_module
from app.core.security import create_access_token

ANALYZE_TIMEOUT_SECONDS = 120
SCHEDULE_TIMEOUT_SECONDS = 180


class CoachError(Exception):
    """The coach could not be used; message is safe to show."""


class CoachUnavailable(CoachError):
    """The coach has no base URL configured. Fail closed (503)."""


def _base_url() -> str:
    base = (config_module.settings.coach_base_url or "").rstrip("/")
    if not base:
        raise CoachUnavailable(
            "Document analysis is unavailable right now. Please try again later."
        )
    return base


def _post(user_id: int, path: str, payload: dict, timeout: int) -> dict:
    """POST one request as the learner (server-to-server, on their behalf).

    The token proves who is asking the same way a browser call would — the
    coach loads the live learner context with it. It is minted, used once,
    and discarded.
    """
    url = f"{_base_url()}{path}"
    token = create_access_token(user_id)
    body = {"backend_token": token, **payload}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise CoachError(
            "The study assistant did not answer. Please try again in a minute."
        ) from e
    if response.status_code >= 500:
        raise CoachError(
            "The study assistant hit a snag on that one. Please try again."
        )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = ""
        raise CoachError(str(detail) or "The study assistant refused that request.")
    try:
        return response.json()
    except Exception as e:
        raise CoachError("The study assistant gave back gibberish.") from e


def analyze_material(user_id: int, document_text: str, filename: str) -> dict:
    """Free preview: topics, objectives, study time. Returns the raw dict."""
    data = _post(
        user_id,
        "/coach/analyze-material",
        {"document_text": document_text, "filename": filename},
        ANALYZE_TIMEOUT_SECONDS,
    )
    if not isinstance(data, dict) or not data.get("topics"):
        raise CoachError("The analysis came back empty. Please try again.")
    return data


def generate_schedule(user_id: int, document_text: str, topics: list[str],
                      objectives: list[str], days: int,
                      difficulty: str) -> dict:
    """Paid artifact: the full day-by-day plan. Returns the raw dict."""
    data = _post(
        user_id,
        "/coach/generate-schedule",
        {
            "document_text": document_text,
            "topics": topics,
            "objectives": objectives,
            "days": days,
            "difficulty": difficulty,
        },
        SCHEDULE_TIMEOUT_SECONDS,
    )
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("days"), list)
        or not data["days"]
    ):
        raise CoachError(
            "The schedule came back empty. Your payment stands — "
            "open the schedule again to retry."
        )
    return data
