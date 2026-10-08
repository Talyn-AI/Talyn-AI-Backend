"""on-delete behavior for every user-rooted foreign key.

Deleting a user from outside the API (Supabase dashboard, SQL console) hit
bare foreign keys with no action and died on the first one — study_plans
was just the first table alphabetically in the way, not the only one.
Every user-owned row below now carries the same semantics the
DELETE /v1/users/me endpoint implements by hand:

- private learner/creator data: CASCADE (goes with the account),
- shared or audit data: SET NULL (courses and catalogue templates outlive
  their creator; analytics and mail trails outlive everyone).

Courses and mission templates specifically survive: deleting a creator
detaches their catalogue instead of destroying other learners'
enrollments, progress, and adopted missions. MissionTemplate.creator_user_id
becomes nullable for the same reason Course.creator_user_id already was.

delete_me is untouched: its manual deletes still run first and find the
rows, and the cascades make direct-SQL deletes behave identically.
"""
from alembic import op

revision = "b7d22f9a41e0"
down_revision = "a2b74e91c083"
branch_labels = None
depends_on = None

# (table, column, action). Constraint names are Postgres defaults
# ({table}_{column}_fkey) — verified against a fresh upgrade, which builds
# them the same way.
CASCADES = (
    ("buddy_matches", "user_id"),
    ("buddy_matches", "buddy_user_id"),
    ("conversation_messages", "user_id"),
    ("enrollments", "user_id"),
    ("lesson_progress", "user_id"),
    ("creator_profiles", "user_id"),
    ("quiz_results", "user_id"),
    ("xp_events", "user_id"),
    ("badges", "user_id"),
    ("study_plans", "user_id"),
    ("mission_template_steps", "template_id"),
    ("missions", "user_id"),
    ("mission_steps", "mission_id"),
    ("payments", "user_id"),
    ("community_posts", "author_user_id"),
    ("community_replies", "post_id"),
    ("community_replies", "author_user_id"),
    ("direct_messages", "sender_id"),
    ("direct_messages", "recipient_id"),
)

SET_NULLS = (
    ("analytics_events", "user_id"),
    ("courses", "creator_user_id"),
    ("mission_templates", "creator_user_id"),
)


REFERENT = {
    "template_id": "mission_templates",
    "mission_id": "missions",
    "post_id": "community_posts",
}


def _swap(table: str, column: str, action: str | None) -> None:
    name = f"{table}_{column}_fkey"
    op.drop_constraint(name, table, type_="foreignkey")
    kwargs: dict = {}
    if action is not None:
        kwargs["ondelete"] = action
    op.create_foreign_key(
        name, table, REFERENT.get(column, "users"),
        [column], ["id"], **kwargs,
    )


def upgrade() -> None:
    for table, column in CASCADES:
        _swap(table, column, "CASCADE")
    for table, column in SET_NULLS:
        _swap(table, column, "SET NULL")
    # Templates predate the nullable convention courses already had.
    op.alter_column("mission_templates", "creator_user_id", nullable=True)


def downgrade() -> None:
    op.alter_column("mission_templates", "creator_user_id", nullable=False)
    for table, column in SET_NULLS:
        _swap(table, column, None)
    for table, column in CASCADES:
        _swap(table, column, None)
