"""row level security for daily check-ins.

Same shape as the earlier RLS revisions: enable, no policies, no force.
Kept as its own revision because deployed migrations are history.
"""
from alembic import op

revision = "a2b74e91c083"
down_revision = "f1a83d6c94b7"
branch_labels = None
depends_on = None

TABLES = (
    "check_ins",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
