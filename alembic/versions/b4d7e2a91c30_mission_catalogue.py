"""mission catalogue: creator-authored templates learners adopt.

Missions used to be written by the learner who worked through them. Now a
creator publishes a template and a learner adopts it, which copies the content
into a per-learner instance so step progress stays per learner.

`missions.template_id` is nullable and existing rows keep a NULL one: nobody's
in-progress work is rewritten, and their missions simply predate the catalogue.
No title is copied into templates — adoption copies template -> mission, never
the other way, so there is nothing to backfill.
"""
from alembic import op
import sqlalchemy as sa

revision = "b4d7e2a91c30"
down_revision = "c3a8e5f1b927"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mission_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("creator_user_id", sa.Integer, sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("purpose", sa.String(1000), nullable=False, server_default=""),
        sa.Column("reward_xp", sa.Integer, nullable=False, server_default="100"),
        sa.Column("badge", sa.String(120), nullable=True),
        sa.Column("published", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_mission_templates_creator_user_id",
                    "mission_templates", ["creator_user_id"])

    op.create_table(
        "mission_template_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("template_id", sa.Integer,
                  sa.ForeignKey("mission_templates.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("order", sa.Integer, nullable=False),
    )
    op.create_index("ix_mission_template_steps_template_id",
                    "mission_template_steps", ["template_id"])

    # Added to the existing table rather than recreated: the learner's missions
    # and their completed steps are real data. ON DELETE SET NULL, because
    # withdrawing a template must not delete the missions learners adopted from
    # it - they hold their own copy of the content.
    op.add_column("missions", sa.Column(
        "template_id", sa.Integer,
        sa.ForeignKey("mission_templates.id", ondelete="SET NULL"),
        nullable=True))
    op.create_index("ix_missions_template_id", "missions", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_missions_template_id", table_name="missions")
    op.drop_column("missions", "template_id")
    op.drop_index("ix_mission_template_steps_template_id",
                  table_name="mission_template_steps")
    op.drop_table("mission_template_steps")
    op.drop_index("ix_mission_templates_creator_user_id",
                  table_name="mission_templates")
    op.drop_table("mission_templates")