"""a short numeric code alongside the reset link.

The deployed frontend collects a typed-in code rather than a clicked link, so
each reset row now carries both credentials: the long link token as before,
plus a 6-digit code. Both redeem the same row, so using either spends both —
there is never a live link left behind after a code is used, or vice versa.

code_hash is nullable because rows written before this migration have no code;
attempts counts failed code guesses and burns the row at the cap, which is
the only thing standing between a 6-digit space and an online brute force
(the rate limiter slows it down but does not stop it).
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b21f4d9c03"
down_revision = "c3d82b5f1a47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "password_reset_tokens",
        sa.Column("code_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_password_reset_tokens_code_hash",
        "password_reset_tokens",
        ["code_hash"],
    )
    op.add_column(
        "password_reset_tokens",
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("password_reset_tokens", "attempts")
    op.drop_index(
        "ix_password_reset_tokens_code_hash",
        table_name="password_reset_tokens",
    )
    op.drop_column("password_reset_tokens", "code_hash")
