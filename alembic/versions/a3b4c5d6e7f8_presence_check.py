from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queue_sessions",
        sa.Column("presence_check", sa.String(16), server_default=sa.text("'off'"), nullable=False),
    )
    op.add_column(
        "queue_sessions",
        sa.Column("presence_interval", sa.Integer(), server_default=sa.text("30"), nullable=False),
    )
    op.create_check_constraint(
        "ck_queue_sessions_presence", "queue_sessions", "presence_check IN ('off', 'rotating_code')"
    )
    op.create_check_constraint(
        "ck_queue_sessions_presence_interval",
        "queue_sessions",
        "presence_interval BETWEEN 30 AND 60",
    )


def downgrade() -> None:
    op.drop_constraint("ck_queue_sessions_presence_interval", "queue_sessions", type_="check")
    op.drop_constraint("ck_queue_sessions_presence", "queue_sessions", type_="check")
    op.drop_column("queue_sessions", "presence_interval")
    op.drop_column("queue_sessions", "presence_check")
