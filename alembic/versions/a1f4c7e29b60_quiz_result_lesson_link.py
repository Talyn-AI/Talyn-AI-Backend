"""link a quiz result to the lesson it tested.

Quiz results were only ever course-scoped, so there was no way to tell which
quiz an attempt belonged to — and therefore no way to hold a learner at a quiz
without guessing from the topic string, which is free text.

Nullable on purpose: existing rows predate the link, and a gate that only reads
results carrying a lesson_id simply ignores them rather than treating an
unlinked legacy attempt as a pass.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1f4c7e29b60"
down_revision = "d91f4c6b8e27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quiz_results",
        sa.Column(
            "lesson_id",
            sa.Integer,
            sa.ForeignKey("lessons.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_quiz_results_lesson_id", "quiz_results", ["lesson_id"])


def downgrade() -> None:
    op.drop_index("ix_quiz_results_lesson_id", table_name="quiz_results")
    op.drop_column("quiz_results", "lesson_id")
