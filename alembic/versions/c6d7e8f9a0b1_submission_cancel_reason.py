from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("cancel_reason", sa.String(length=16), nullable=True))



def downgrade() -> None:
    op.drop_column("submissions", "cancel_reason")
