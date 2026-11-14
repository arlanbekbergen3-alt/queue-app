from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queue_sessions",
        sa.Column("show_student_names", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("queue_sessions", "show_student_names")
