"""row level security for the monetization tables.

Same shape as d4c9a1e27f50: enable, no policies, no force. Kept as its own
revision rather than appended to that one because that migration has already
run in production — editing a deployed migration changes history, not the
database.
"""
from alembic import op

revision = "c7e15a93d204"
down_revision = "b2d84f6a91c3"
branch_labels = None
depends_on = None

TABLES = (
    "material_analyses",
    "study_schedules",
    "schedule_days",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
