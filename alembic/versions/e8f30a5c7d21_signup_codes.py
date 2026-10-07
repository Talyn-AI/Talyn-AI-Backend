"""codes replace links for signup verification.

Emailed links are gone: signup verification moves to 6-digit codes through
the unified OTP endpoint, mirroring the password-reset codes. token_hash
goes nullable on both token tables (new rows carry only a code_hash), and
the verification table gains the code columns the reset table already has.
Old link rows are already spent or expired; nothing backfills them.

Code lifetime is 15 minutes for both flows, satisfying the at-least-10
requirement with the same window the reset flow already promised.
"""
from alembic import op
import sqlalchemy as sa

revision = "e8f30a5c7d21"
down_revision = "d9f27b41c5e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("password_reset_tokens", "token_hash", nullable=True)
    op.alter_column("email_verification_tokens", "token_hash", nullable=True)
    op.add_column(
        "email_verification_tokens",
        sa.Column("code_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_email_verification_tokens_code_hash",
        "email_verification_tokens",
        ["code_hash"],
    )
    op.add_column(
        "email_verification_tokens",
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("email_verification_tokens", "attempts")
    op.drop_index(
        "ix_email_verification_tokens_code_hash",
        table_name="email_verification_tokens",
    )
    op.drop_column("email_verification_tokens", "code_hash")
    op.alter_column("email_verification_tokens", "token_hash", nullable=False)
    op.alter_column("password_reset_tokens", "token_hash", nullable=False)
