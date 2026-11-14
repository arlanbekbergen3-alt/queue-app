from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column("fairness_sharing", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "assignments", sa.Column("origin_publication_id", sa.String(length=36), nullable=True)
    )
    op.create_foreign_key(
        "fk_assignments_origin_publication",
        "assignments",
        "space_publications",
        ["origin_publication_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_assignments_origin_publication_id", "assignments", ["origin_publication_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assignments_origin_publication_id", table_name="assignments")
    op.drop_constraint("fk_assignments_origin_publication", "assignments", type_="foreignkey")
    op.drop_column("assignments", "origin_publication_id")
    op.drop_column("assignments", "fairness_sharing")
