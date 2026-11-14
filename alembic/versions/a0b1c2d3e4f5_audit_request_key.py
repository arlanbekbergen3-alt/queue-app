from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("request_key", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_audit_log_submission_request_key", "audit_log", ["submission_id", "request_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_submission_request_key", table_name="audit_log")
    op.drop_column("audit_log", "request_key")
