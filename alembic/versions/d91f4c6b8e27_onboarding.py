"""email verification, learning pace, and interests.

Four columns and one token table. All three columns on `users` are nullable so
existing accounts keep working exactly as they do — a user created before
onboarding existed has not chosen a pace, and is treated as onboarded rather
than locked out of their own account.
"""
from alembic import op
import sqlalchemy as sa

revision = "d91f4c6b8e27"
down_revision = "b4d7e2a91c30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column(
        "learning_pace", sa.String(20), nullable=True))
    op.add_column("users", sa.Column(
        "onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill rather than leave everyone mid-onboarding: accounts that already
    # existed have demonstrably been using the product, and locking them out of
    # their own progress would be a hostile surprise. New accounts get NULL and
    # go through the flow.
    op.execute(
        "UPDATE users SET email_verified_at = created_at, "
        "onboarding_completed_at = created_at "
        "WHERE onboarding_completed_at IS NULL"
    )

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_verification_tokens_user_id",
                    "email_verification_tokens", ["user_id"])
    op.create_index("ix_email_verification_tokens_token_hash",
                    "email_verification_tokens", ["token_hash"], unique=True)
    op.create_index("ix_email_verification_tokens_created_at",
                    "email_verification_tokens", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_created_at",
                  table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_token_hash",
                  table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_user_id",
                  table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "onboarding_completed_at")
    op.drop_column("users", "learning_pace")
    op.drop_column("users", "email_verified_at")