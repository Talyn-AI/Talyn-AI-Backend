"""row level security on every application table.

Supabase exposes PostgREST alongside the database, and any holder of the
project's anon key could read every table through it: without RLS, all of
them are unrestricted. This migration locks that door.

Deliberately policy-less. With RLS enabled and no policies, every
non-bypassing role (anon, authenticated, any future least-privilege role)
is denied everything through PostgREST and direct connections alike. The
application itself is unaffected: it connects as the table owner, and
owners bypass RLS. That asymmetry is the entire design — the app keeps full
access, the public API surface gets none.

What this migration does NOT do, on purpose:

- No GRANTs, no policies. A policy is an access rule, and there is no
  client that should have any. The day a direct-Supabase client exists,
  that day gets policies — not before.
- No FORCE ROW LEVEL SECURITY. FORCE subjects even owners to the
  policies, and with no policies that means the application itself would
  be locked out of its own tables on the next request. A total outage
  wearing a security label.

If you add a table, add it to TABLES below. tests/test_rls.py fails
otherwise — that is the mechanism that keeps this list complete.
"""
from alembic import op

revision = "d4c9a1e27f50"
down_revision = "b8e05d2f6a41"
branch_labels = None
depends_on = None

# Every table in app.models metadata at the time of writing. Compared
# against Base.metadata.tables by tests/test_rls.py, so drift fails loudly.
TABLES = (
    "admin_audit_log",
    "analytics_events",
    "badges",
    "buddy_matches",
    "community_posts",
    "community_replies",
    "conversation_messages",
    "course_modules",
    "courses",
    "creator_profiles",
    "direct_messages",
    "email_log",
    "email_verification_tokens",
    "enrollments",
    "learner_materials",
    "learning_path_steps",
    "learning_paths",
    "lesson_assets",
    "lesson_progress",
    "lessons",
    "live_sessions",
    "mission_steps",
    "mission_template_steps",
    "mission_templates",
    "missions",
    "password_reset_tokens",
    "payments",
    "quiz_results",
    "study_plans",
    "users",
    "waitlist_signups",
    "xp_events",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
