from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column(
            "require_extreme_reason", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "assignments",
        sa.Column(
            "deviation_warning", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "assignments",
        sa.Column("deviation_sigma", sa.Float(), server_default=sa.text("2.0"), nullable=False),
    )
    op.create_check_constraint(
        "ck_assignments_deviation_sigma", "assignments", "deviation_sigma BETWEEN 0.5 AND 5"
    )


def downgrade() -> None:
    op.drop_constraint("ck_assignments_deviation_sigma", "assignments", type_="check")
    op.drop_column("assignments", "deviation_sigma")
    op.drop_column("assignments", "deviation_warning")
    op.drop_column("assignments", "require_extreme_reason")
