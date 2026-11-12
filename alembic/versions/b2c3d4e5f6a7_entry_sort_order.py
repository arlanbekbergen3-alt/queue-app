
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entries", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.execute("UPDATE entries SET sort_order = ticket")
    op.alter_column("entries", "sort_order", nullable=False)
    op.drop_index("ix_entries_session_status_ticket", table_name="entries")
    op.create_index(
        "ix_entries_session_status_sort",
        "entries",
        ["session_id", "status", "sort_order"],
        unique=False,
    )
    op.create_index(
        "uq_entries_waiting_sort_order",
        "entries",
        ["session_id", "sort_order"],
        unique=True,
        postgresql_where=sa.text("status = 'waiting'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_entries_waiting_sort_order",
        table_name="entries",
        postgresql_where=sa.text("status = 'waiting'"),
    )
    op.drop_index("ix_entries_session_status_sort", table_name="entries")
    op.create_index(
        "ix_entries_session_status_ticket", "entries", ["session_id", "status", "ticket"]
    )
    op.drop_column("entries", "sort_order")
