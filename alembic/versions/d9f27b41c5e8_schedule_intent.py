"""learner intent on analyses and schedules.

The loop grew two steps between analysis and purchase: the learner names a
purpose ("exam", "interview", ...) and picks a timeline in days. Both shape
the generated schedule, so they are stored where generation reads them —
the analysis row — and echoed onto the schedule for context.

timeline_days is nullable rather than defaulted: NULL means "not chosen",
which the purchase gate can tell apart from "chose 14". A default of 14
would make skipping the step invisible.
"""
from alembic import op
import sqlalchemy as sa

revision = "d9f27b41c5e8"
down_revision = "c7e15a93d204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_analyses",
        sa.Column("purpose", sa.String(100), nullable=False,
                  server_default=""),
    )
    op.add_column(
        "material_analyses",
        sa.Column("timeline_days", sa.Integer, nullable=True),
    )
    op.add_column(
        "study_schedules",
        sa.Column("purpose", sa.String(100), nullable=False,
                  server_default=""),
    )


def downgrade() -> None:
    op.drop_column("study_schedules", "purpose")
    op.drop_column("material_analyses", "timeline_days")
    op.drop_column("material_analyses", "purpose")
