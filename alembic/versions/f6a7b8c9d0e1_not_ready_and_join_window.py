from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entries",
        sa.Column("not_ready", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "entries",
        sa.Column("not_ready_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "queue_sessions",
        sa.Column("join_open", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("queue_sessions", "join_open")
    op.drop_column("entries", "not_ready_count")
    op.drop_column("entries", "not_ready")
