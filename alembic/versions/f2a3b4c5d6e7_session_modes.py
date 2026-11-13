from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queue_sessions",
        sa.Column(
            "session_mode", sa.String(12), server_default=sa.text("'protocol'"), nullable=False
        ),
    )
    op.add_column(
        "queue_sessions",
        sa.Column("quick_max_points", sa.Integer(), server_default=sa.text("10"), nullable=False),
    )
    op.create_check_constraint(
        "ck_queue_sessions_session_mode",
        "queue_sessions",
        "session_mode IN ('queue_only', 'quick', 'protocol')",
    )
    op.create_check_constraint(
        "ck_queue_sessions_quick_max", "queue_sessions", "quick_max_points BETWEEN 1 AND 100"
    )
    op.add_column(
        "assignments",
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("assignments", "is_system")
    op.drop_constraint("ck_queue_sessions_quick_max", "queue_sessions", type_="check")
    op.drop_constraint("ck_queue_sessions_session_mode", "queue_sessions", type_="check")
    op.drop_column("queue_sessions", "quick_max_points")
    op.drop_column("queue_sessions", "session_mode")
