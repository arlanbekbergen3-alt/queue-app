from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ai_jobs',
    sa.Column('teacher_id', sa.String(), nullable=False),
    sa.Column('assignment_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('error_code', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'error')", name='ck_ai_jobs_status'),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ai_jobs_teacher_created', 'ai_jobs', ['teacher_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_ai_jobs_teacher_created', table_name='ai_jobs')
    op.drop_table('ai_jobs')
