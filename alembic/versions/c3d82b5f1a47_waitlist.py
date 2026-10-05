"""waitlist signups from the early-access page.

One table. No foreign key to `users`: most of these people do not have an
account yet, which is the entire point of the page, and a nullable link that is
almost always NULL would only invite questions about what it means.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c3d82b5f1a47"
down_revision = "a1f4c7e29b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        # Reuses the same controlled vocabulary as onboarding rather than
        # inventing a second one, so "design" means one thing across the app.
        sa.Column("interests", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column(
            "course",
            sa.String(200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Unique so a repeat submit updates the existing row instead of piling up
    # duplicates from someone clicking twice.
    op.create_index(
        "ix_waitlist_signups_email", "waitlist_signups", ["email"], unique=True
    )
    op.create_index("ix_waitlist_signups_created_at", "waitlist_signups", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_waitlist_signups_created_at", table_name="waitlist_signups")
    op.drop_index("ix_waitlist_signups_email", table_name="waitlist_signups")
    op.drop_table("waitlist_signups")
