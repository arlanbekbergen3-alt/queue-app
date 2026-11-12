from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('queue_sessions',
    sa.Column('teacher_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('code', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('next_ticket', sa.Integer(), nullable=False),
    sa.Column('avg_service_seconds', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint("status IN ('open', 'closed')", name='ck_queue_sessions_status'),
    sa.CheckConstraint('avg_service_seconds > 0', name='ck_queue_sessions_avg_positive'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_queue_sessions_teacher_id'), 'queue_sessions', ['teacher_id'], unique=False)
    op.create_table('entries',
    sa.Column('session_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('ticket', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('called_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint("status IN ('waiting', 'called', 'done', 'skipped', 'left')", name='ck_entries_status'),
    sa.ForeignKeyConstraint(['session_id'], ['queue_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'ticket', name='uq_entries_session_ticket'),
    sa.UniqueConstraint('session_id', 'token_hash', name='uq_entries_session_token')
    )
    op.create_index('ix_entries_session_status_ticket', 'entries', ['session_id', 'status', 'ticket'], unique=False)
    op.create_index('uq_entries_one_called_per_session', 'entries', ['session_id'], unique=True, postgresql_where=sa.text("status = 'called'"))


def downgrade() -> None:
    op.drop_index('uq_entries_one_called_per_session', table_name='entries', postgresql_where=sa.text("status = 'called'"))
    op.drop_index('ix_entries_session_status_ticket', table_name='entries')
    op.drop_table('entries')
    op.drop_index(op.f('ix_queue_sessions_teacher_id'), table_name='queue_sessions')
    op.drop_table('queue_sessions')
    op.drop_table('users')
