from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql
"""
TRIGGER_SQL = """
CREATE TRIGGER session_events_no_change BEFORE UPDATE OR DELETE ON session_events
FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
"""


def upgrade() -> None:
    op.add_column(
        "queue_sessions",
        sa.Column("order_mode", sa.String(16), server_default=sa.text("'join'"), nullable=False),
    )
    op.add_column(
        "queue_sessions",
        sa.Column("ready_priority", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "queue_sessions",
        sa.Column("ready_bonus_points", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "queue_sessions",
        sa.Column(
            "ready_skip_questions", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "queue_sessions", sa.Column("lottery_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_queue_sessions_order_mode", "queue_sessions", "order_mode IN ('join', 'lottery')"
    )
    op.create_check_constraint(
        "ck_queue_sessions_ready_bonus", "queue_sessions", "ready_bonus_points BETWEEN 0 AND 5"
    )
    op.create_check_constraint(
        "ck_queue_sessions_ready_skip", "queue_sessions", "ready_skip_questions BETWEEN 0 AND 1"
    )
    op.add_column(
        "entries",
        sa.Column("ready_now", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "submissions",
        sa.Column("bonus_points", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "session_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_events_session_seq", "session_events", ["session_id", "seq"], unique=False
    )
    op.execute(FUNCTION_SQL)
    op.execute(TRIGGER_SQL)


def downgrade() -> None:
    op.drop_table("session_events")
    op.drop_column("submissions", "bonus_points")
    op.drop_column("entries", "ready_now")
    op.drop_constraint("ck_queue_sessions_ready_skip", "queue_sessions", type_="check")
    op.drop_constraint("ck_queue_sessions_ready_bonus", "queue_sessions", type_="check")
    op.drop_constraint("ck_queue_sessions_order_mode", "queue_sessions", type_="check")
    op.drop_column("queue_sessions", "lottery_at")
    op.drop_column("queue_sessions", "ready_skip_questions")
    op.drop_column("queue_sessions", "ready_bonus_points")
    op.drop_column("queue_sessions", "ready_priority")
    op.drop_column("queue_sessions", "order_mode")
