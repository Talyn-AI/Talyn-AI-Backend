"""payments for generated study schedules.

Course purchases keep working exactly as before; this revision only widens
the table so one row can also record a schedule unlock: course_id becomes
nullable and material_id appears, with a CHECK that exactly one is set. A
row for both or for neither is a bookkeeping bug, and the database should
refuse it rather than let settlement code guess.
"""
from alembic import op
import sqlalchemy as sa

revision = "a9f3c21d4e07"
down_revision = "d4c9a1e27f50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("payments", "course_id", nullable=True)
    op.add_column("payments", sa.Column("material_id", sa.Integer, nullable=True))
    op.create_index("ix_payments_material_id", "payments", ["material_id"])
    op.create_check_constraint(
        "ck_payments_single_item",
        "payments",
        "(course_id IS NOT NULL)::int + (material_id IS NOT NULL)::int = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payments_single_item", "payments", type_="check")
    op.drop_index("ix_payments_material_id", table_name="payments")
    op.drop_column("payments", "material_id")
    # Existing rows all carry a course_id (material payments cannot predate
    # this revision), so re-adding NOT NULL is safe.
    op.alter_column("payments", "course_id", nullable=False)
