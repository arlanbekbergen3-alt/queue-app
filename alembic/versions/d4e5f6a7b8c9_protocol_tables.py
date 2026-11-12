from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUDIT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql
"""
AUDIT_TRIGGER_SQL = """
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
"""


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('submission_id', sa.String(length=36), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('field', sa.String(length=80), nullable=False),
    sa.Column('old_value', sa.String(length=1000), nullable=True),
    sa.Column('new_value', sa.String(length=1000), nullable=True),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint("action <> 'correct' OR length(trim(reason)) > 0", name='ck_audit_log_reason'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_log_submission_seq', 'audit_log', ['submission_id', 'seq'], unique=False)
    op.create_table('assignments',
    sa.Column('teacher_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('questions_per_submission', sa.Integer(), nullable=False),
    sa.Column('practice_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint('questions_per_submission BETWEEN 1 AND 10', name='ck_assignments_questions_count'),
    sa.ForeignKeyConstraint(['teacher_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('teacher_id', 'title', name='uq_assignments_teacher_title')
    )
    op.create_index(op.f('ix_assignments_teacher_id'), 'assignments', ['teacher_id'], unique=False)
    op.create_table('assignment_criteria',
    sa.Column('assignment_id', sa.String(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('max_points', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint('max_points BETWEEN 1 AND 100', name='ck_criteria_max_points'),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('assignment_id', 'name', name='uq_criteria_assignment_name')
    )
    op.create_index(op.f('ix_assignment_criteria_assignment_id'), 'assignment_criteria', ['assignment_id'], unique=False)
    op.create_table('assignment_questions',
    sa.Column('assignment_id', sa.String(), nullable=False),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('difficulty', sa.Integer(), nullable=False),
    sa.Column('topic', sa.String(length=60), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint('difficulty BETWEEN 1 AND 3', name='ck_questions_difficulty'),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('assignment_id', 'text', name='uq_questions_assignment_text')
    )
    op.create_index(op.f('ix_assignment_questions_assignment_id'), 'assignment_questions', ['assignment_id'], unique=False)
    op.create_table('submissions',
    sa.Column('entry_id', sa.String(), nullable=False),
    sa.Column('session_id', sa.String(), nullable=False),
    sa.Column('assignment_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('total_points', sa.Integer(), nullable=True),
    sa.Column('max_points', sa.Integer(), nullable=True),
    sa.Column('comment', sa.String(length=1000), nullable=False),
    sa.Column('selection_seed', sa.String(length=32), nullable=False),
    sa.Column('pool_question_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint("status IN ('in_progress', 'completed', 'cancelled')", name='ck_submissions_status'),
    sa.ForeignKeyConstraint(['assignment_id'], ['assignments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entry_id'], ['entries.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['queue_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entry_id', name='uq_submissions_entry')
    )
    op.create_index('ix_submissions_assignment_status', 'submissions', ['assignment_id', 'status'], unique=False)
    op.create_index('ix_submissions_session_status', 'submissions', ['session_id', 'status'], unique=False)
    op.create_table('submission_questions',
    sa.Column('submission_id', sa.String(), nullable=False),
    sa.Column('question_id', sa.String(), nullable=True),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('difficulty', sa.Integer(), nullable=False),
    sa.Column('topic', sa.String(length=60), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['assignment_questions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('submission_id', 'position', name='uq_submission_questions_position')
    )
    op.create_index('uq_submission_questions_question', 'submission_questions', ['submission_id', 'question_id'], unique=True, postgresql_where=sa.text('question_id IS NOT NULL'))
    op.create_table('submission_scores',
    sa.Column('submission_id', sa.String(), nullable=False),
    sa.Column('criterion_id', sa.String(), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.Column('comment', sa.String(length=300), nullable=False),
    sa.Column('id', sa.String(), nullable=False),
    sa.CheckConstraint('points >= 0', name='ck_scores_points'),
    sa.ForeignKeyConstraint(['criterion_id'], ['assignment_criteria.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('submission_id', 'criterion_id', name='uq_scores_submission_criterion')
    )
    op.add_column('queue_sessions', sa.Column('assignment_id', sa.String(), nullable=True))
    op.create_index(op.f('ix_queue_sessions_assignment_id'), 'queue_sessions', ['assignment_id'], unique=False)
    op.create_foreign_key('queue_sessions_assignment_id_fkey', 'queue_sessions', 'assignments', ['assignment_id'], ['id'], ondelete='SET NULL')
    op.execute(AUDIT_FUNCTION_SQL)
    op.execute(AUDIT_TRIGGER_SQL)


def downgrade() -> None:
    op.drop_constraint('queue_sessions_assignment_id_fkey', 'queue_sessions', type_='foreignkey')
    op.drop_index(op.f('ix_queue_sessions_assignment_id'), table_name='queue_sessions')
    op.drop_column('queue_sessions', 'assignment_id')
    op.drop_table('submission_scores')
    op.drop_index('uq_submission_questions_question', table_name='submission_questions', postgresql_where=sa.text('question_id IS NOT NULL'))
    op.drop_table('submission_questions')
    op.drop_index('ix_submissions_session_status', table_name='submissions')
    op.drop_index('ix_submissions_assignment_status', table_name='submissions')
    op.drop_table('submissions')
    op.drop_index(op.f('ix_assignment_questions_assignment_id'), table_name='assignment_questions')
    op.drop_table('assignment_questions')
    op.drop_index(op.f('ix_assignment_criteria_assignment_id'), table_name='assignment_criteria')
    op.drop_table('assignment_criteria')
    op.drop_index(op.f('ix_assignments_teacher_id'), table_name='assignments')
    op.drop_table('assignments')
    op.drop_index('ix_audit_log_submission_seq', table_name='audit_log')
    op.drop_table('audit_log')
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
