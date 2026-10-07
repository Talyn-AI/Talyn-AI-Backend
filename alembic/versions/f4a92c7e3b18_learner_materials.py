"""learner library: private study materials.

Learners can upload their own documents — readings, decks, notes — into a
library only they (and admins, through the file URL endpoint) can see. This
table is the claim registry for the "material" upload purpose: presign mints
the key, and claiming it here is what stops the orphan sweep from deleting
the object 24 hours later.
"""
from alembic import op
import sqlalchemy as sa

revision = "f4a92c7e3b18"
down_revision = "e7b21f4d9c03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_materials",
        sa.Column("id", sa.Integer, primary_key=True),
        # CASCADE: these are private files. A deleted account must not leave
        # ownerless documents behind, and ownerless rows would be
        # admin-readable at best — gone means gone.
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False, server_default=""),
        # Keys embed a uuid, so collisions across learners are not a thing;
        # unique anyway, because a key claimed twice is a bug, not sharing.
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("content_type", sa.String(120), nullable=False, server_default=""),
        # The provider's number, read at claim time — never the client's.
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "scan_status", sa.String(20), nullable=False, server_default="unscanned"
        ),
        sa.Column(
            "scan_detail", sa.String(255), nullable=False, server_default=""
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_learner_materials_user_id", "learner_materials", ["user_id"]
    )
    op.create_index(
        "ix_learner_materials_created_at", "learner_materials", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learner_materials_created_at", table_name="learner_materials"
    )
    op.drop_index("ix_learner_materials_user_id", table_name="learner_materials")
    op.drop_table("learner_materials")
