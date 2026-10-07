"""stored learning paths: an ordered list of courses per learner.

The coach can generate a path, but nothing kept one: the response evaporated
on return. These tables are the saved counterpart — the learner's own
ordering over published courses, with progress derived from enrollments
rather than stored a second time.
"""
from alembic import op
import sqlalchemy as sa

revision = "b8e05d2f6a41"
down_revision = "f4a92c7e3b18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_paths",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False,
                  server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_learning_paths_user_id", "learning_paths", ["user_id"]
    )
    op.create_table(
        "learning_path_steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "path_id",
            sa.Integer,
            sa.ForeignKey("learning_paths.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # CASCADE: a deleted course drops out of paths that referenced it
        # rather than leaving steps that point at nothing.
        sa.Column(
            "course_id",
            sa.Integer,
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_learning_path_steps_path_id", "learning_path_steps", ["path_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learning_path_steps_path_id", table_name="learning_path_steps"
    )
    op.drop_table("learning_path_steps")
    op.drop_index("ix_learning_paths_user_id", table_name="learning_paths")
    op.drop_table("learning_paths")
