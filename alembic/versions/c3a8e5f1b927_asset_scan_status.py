"""email log, asset scan status, and upload hardening

Revision ID: c3a8e5f1b927
Revises: 7f2c9a1b4e50
Create Date: 2026-10-01 09:22:41.884015

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a8e5f1b927'
down_revision: Union[str, Sequence[str], None] = '7f2c9a1b4e50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # size_bytes already exists but holds whatever the uploader claimed. It
    # cannot be corrected retroactively, so existing rows keep their value and
    # new writes come from the storage provider's own ContentLength.
    op.add_column(
        'lesson_assets',
        sa.Column('scan_status', sa.String(length=20), server_default='unscanned',
                  nullable=False),
    )
    op.add_column(
        'lesson_assets',
        sa.Column('scan_detail', sa.String(length=255), server_default='',
                  nullable=False),
    )
    # created_at lets the orphan sweep age objects by upload time rather than
    # by whichever timestamp the provider happens to report.
    op.add_column(
        'lesson_assets',
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )
    op.create_index(op.f('ix_lesson_assets_created_at'), 'lesson_assets',
                    ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_lesson_assets_created_at'), table_name='lesson_assets')
    op.drop_column('lesson_assets', 'created_at')
    op.drop_column('lesson_assets', 'scan_detail')
    op.drop_column('lesson_assets', 'scan_status')
