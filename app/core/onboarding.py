"""Onboarding vocabulary: learning pace and the interest catalogue.

Both live on the server so the pickers cannot drift from the values the
backend accepts. A frontend that hardcodes its own list will eventually offer
a pace the API rejects, or miss one that exists.

`learning_pace` is stored as a key, not as "30-60 minutes", because three
things need the same answer and deriving them separately is how they end up
disagreeing: the label a learner sees, the range shown once chosen, and the
daily goal that seeds their study plan.
"""
from typing import NamedTuple


class PaceOption(NamedTuple):
    key: str
    label: str
    min_minutes: int
    max_minutes: int | None
    # Seeds StudyPlan.daily_goal_minutes. Roughly the middle of the range, so
    # the number a learner lands on is not the top of it.
    daily_goal_minutes: int
    description: str


LEARNING_PACES: tuple[PaceOption, ...] = (
    PaceOption(
        key="light",
        label="15–30 minutes",
        min_minutes=15,
        max_minutes=30,
        daily_goal_minutes=20,
        description="A short session most days. Good for busy weeks.",
    ),
    PaceOption(
        key="steady",
        label="30–60 minutes",
        min_minutes=30,
        max_minutes=60,
        daily_goal_minutes=45,
        description="A proper sit-down session. The usual choice.",
    ),
    PaceOption(
        key="deep",
        label="1–2 hours",
        min_minutes=60,
        max_minutes=120,
        daily_goal_minutes=90,
        description="Long focused blocks. Expect to finish faster.",
    ),
    PaceOption(
        key="intensive",
        label="2 hours or more",
        min_minutes=120,
        max_minutes=None,
        daily_goal_minutes=150,
        description="Full immersion. Best with a clear deadline.",
    ),
)

PACE_KEYS = tuple(p.key for p in LEARNING_PACES)
PACE_BY_KEY = {p.key: p for p in LEARNING_PACES}
DEFAULT_PACE = "steady"

# Weekly target derived from pace so the study plan arrives coherent rather
# than half-configured. Roughly five sessions a week at the daily goal.
WEEKLY_TARGET_BY_PACE = {
    "light": 3,
    "steady": 5,
    "deep": 6,
    "intensive": 7,
}


# ── Interests ────────────────────────────────────────────────────────────────
#
# A controlled vocabulary rather than free text. Interests drive
# recommendations and the coach's examples, and "web development, web dev,
# webdesign, websites" is four strings that mean one thing. Free text would
# need cleaning later, with no way to tell a typo from a real interest.

INTERESTS: tuple[tuple[str, str], ...] = (
    ("web-development", "Web development"),
    ("mobile-apps", "Mobile apps"),
    ("data-science", "Data science"),
    ("artificial-intelligence", "AI & machine learning"),
    ("design", "Design & UI/UX"),
    ("productivity", "Productivity"),
    ("business", "Business & entrepreneurship"),
    ("marketing", "Marketing"),
    ("finance", "Finance & accounting"),
    ("writing", "Writing & communication"),
    ("languages", "Languages"),
    ("music", "Music & audio"),
    ("photography", "Photography & video"),
    ("health-fitness", "Health & fitness"),
    ("personal-development", "Personal development"),
    ("academics", "Academics & exam prep"),
    ("cybersecurity", "Cybersecurity"),
    ("devops", "DevOps & cloud"),
    ("gaming", "Game development"),
    ("culinary", "Cooking"),
)

INTEREST_KEYS = tuple(k for k, _ in INTERESTS)
INTEREST_LABELS = dict(INTERESTS)


def is_valid_interest(value: str) -> bool:
    return value in INTEREST_KEYS