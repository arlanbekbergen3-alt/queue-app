from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queue_sessions", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("queue_sessions", sa.Column("questions_override", sa.Integer(), nullable=True))
    op.add_column("queue_sessions", sa.Column("budget_declined", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_queue_sessions_questions_override",
        "queue_sessions",
        "questions_override IS NULL OR questions_override BETWEEN 1 AND 10",
    )
    op.add_column("submissions", sa.Column("questions_target", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("submissions", "questions_target")
    op.drop_constraint("ck_queue_sessions_questions_override", "queue_sessions", type_="check")
    op.drop_column("queue_sessions", "budget_declined")
    op.drop_column("queue_sessions", "questions_override")
    op.drop_column("queue_sessions", "deadline_at")
