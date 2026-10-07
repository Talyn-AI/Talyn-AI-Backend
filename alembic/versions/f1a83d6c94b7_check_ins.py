"""daily check-ins."""
from alembic import op
import sqlalchemy as sa

revision = "f1a83d6c94b7"
down_revision = "e8f30a5c7d21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "check_ins",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_date", sa.Date, nullable=False),
        sa.Column("mood", sa.String(30), nullable=True),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "check_date",
                            name="uq_check_ins_user_day"),
    )
    op.create_index("ix_check_ins_user_id", "check_ins", ["user_id"])
    op.create_index("ix_check_ins_check_date", "check_ins", ["check_date"])


def downgrade() -> None:
    op.drop_index("ix_check_ins_check_date", table_name="check_ins")
    op.drop_index("ix_check_ins_user_id", table_name="check_ins")
    op.drop_table("check_ins")
