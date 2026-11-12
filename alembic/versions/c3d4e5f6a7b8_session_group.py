
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queue_sessions", sa.Column("group_name", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("queue_sessions", "group_name")
