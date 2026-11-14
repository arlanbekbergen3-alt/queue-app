from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roster_entries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("teacher_id", sa.String(), nullable=False),
        sa.Column("group_key", sa.String(length=60), nullable=False),
        sa.Column("group_name", sa.String(length=60), nullable=False),
        sa.Column("name_key", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("teacher_id", "group_key", "name_key", name="uq_roster_group_name"),
    )
    op.create_index("ix_roster_teacher_group", "roster_entries", ["teacher_id", "group_key"])
    op.add_column(
        "queue_sessions",
        sa.Column("roster_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("queue_sessions", "roster_only")
    op.drop_index("ix_roster_teacher_group", table_name="roster_entries")
    op.drop_table("roster_entries")
