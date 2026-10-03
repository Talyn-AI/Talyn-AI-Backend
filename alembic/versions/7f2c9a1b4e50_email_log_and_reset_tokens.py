"""email log and single-use password reset tokens

Revision ID: 7f2c9a1b4e50
Revises: da5066edf74f
Create Date: 2026-09-30 21:14:07.331902

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f2c9a1b4e50'
down_revision: Union[str, Sequence[str], None] = 'da5066edf74f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        # sha256 of the raw token: a database dump must not hand over
        # working reset links.
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        # NULL = still redeemable. Set on first use.
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        # Cascade rather than SET NULL: a deleted account must leave no live
        # reset token behind.
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_password_reset_tokens_expires_at'),
                    'password_reset_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_password_reset_tokens_token_hash'),
                    'password_reset_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_password_reset_tokens_user_id'),
                    'password_reset_tokens', ['user_id'], unique=False)

    op.create_table(
        'email_log',
        sa.Column('id', sa.Integer(), nullable=False),
        # SET NULL: the record of what was sent outlives the account, the
        # same as the admin audit log.
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('to_email', sa.String(length=255), nullable=False),
        sa.Column('template', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_log_created_at'), 'email_log',
                    ['created_at'], unique=False)
    op.create_index(op.f('ix_email_log_user_id'), 'email_log', ['user_id'],
                    unique=False)
    op.create_index(op.f('ix_email_log_to_email'), 'email_log', ['to_email'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_log_to_email'), table_name='email_log')
    op.drop_index(op.f('ix_email_log_user_id'), table_name='email_log')
    op.drop_index(op.f('ix_email_log_created_at'), table_name='email_log')
    op.drop_table('email_log')
    op.drop_index(op.f('ix_password_reset_tokens_user_id'),
                  table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_token_hash'),
                  table_name='password_reset_tokens')
    op.drop_index(op.f('ix_password_reset_tokens_expires_at'),
                  table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
