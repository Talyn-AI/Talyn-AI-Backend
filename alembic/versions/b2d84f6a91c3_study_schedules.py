"""analysis previews and generated study schedules."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2d84f6a91c3"
down_revision = "a9f3c21d4e07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_analyses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "material_id",
            sa.Integer,
            sa.ForeignKey("learner_materials.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("topics", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("objectives", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("estimated_minutes", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "study_schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "material_id",
            sa.Integer,
            sa.ForeignKey("learner_materials.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_study_schedules_user_id", "study_schedules", ["user_id"]
    )
    op.create_table(
        "schedule_days",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Integer,
            sa.ForeignKey("study_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_number", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("objectives", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("tasks", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_schedule_days_schedule_id", "schedule_days", ["schedule_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_days_schedule_id", table_name="schedule_days"
    )
    op.drop_table("schedule_days")
    op.drop_index("ix_study_schedules_user_id", table_name="study_schedules")
    op.drop_table("study_schedules")
    op.drop_table("material_analyses")
