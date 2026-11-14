from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assignment_criteria", sa.Column("levels", postgresql.JSONB(), nullable=True))
    op.add_column("space_publication_criteria", sa.Column("levels", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("space_publication_criteria", "levels")
    op.drop_column("assignment_criteria", "levels")
