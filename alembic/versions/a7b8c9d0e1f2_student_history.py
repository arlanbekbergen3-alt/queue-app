import unicodedata
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _key(value: str | None) -> str:
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    return "".join(folded.split())[:60]


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("teacher_id", sa.String(), nullable=False),
        sa.Column("group_key", sa.String(length=60), nullable=False),
        sa.Column("name_key", sa.String(length=60), nullable=False),
        sa.Column("display_name", sa.String(length=60), nullable=False),
        sa.Column("group_name", sa.String(length=60), nullable=True),
        sa.Column("auto_link", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("merged_into_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["merged_into_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_students_teacher_id", "students", ["teacher_id"], unique=False)
    op.create_index(
        "uq_students_auto_key",
        "students",
        ["teacher_id", "group_key", "name_key"],
        unique=True,
        postgresql_where=sa.text("auto_link"),
    )
    op.add_column("entries", sa.Column("student_id", sa.String(), nullable=True))
    op.add_column("entries", sa.Column("device_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_entries_student_id", "entries", ["student_id"], unique=False)
    op.create_foreign_key(
        "fk_entries_student_id", "entries", "students", ["student_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "submissions",
        sa.Column("attempt_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "submissions",
        sa.Column(
            "selection_usage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    _backfill(op.get_bind())


def _backfill(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT e.id, e.name, s.teacher_id, s.group_name FROM entries e"
            " JOIN queue_sessions s ON s.id = e.session_id ORDER BY e.joined_at, e.id"
        )
    ).all()
    students: dict[tuple[str, str, str], str] = {}
    for entry_id, name, teacher_id, group in rows:
        key = (teacher_id, _key(group), _key(name))
        student_id = students.get(key)
        if student_id is None:
            student_id = str(uuid.uuid4())
            students[key] = student_id
            bind.execute(
                sa.text(
                    "INSERT INTO students (id, teacher_id, group_key, name_key, display_name,"
                    " group_name, auto_link, created_at) VALUES (:id, :teacher, :gk, :nk, :name,"
                    " :group, true, now())"
                ),
                {
                    "id": student_id,
                    "teacher": teacher_id,
                    "gk": key[1],
                    "nk": key[2],
                    "name": name,
                    "group": group,
                },
            )
        bind.execute(
            sa.text("UPDATE entries SET student_id = :sid WHERE id = :eid"),
            {"sid": student_id, "eid": entry_id},
        )
    subs = bind.execute(
        sa.text(
            "SELECT sub.id, e.student_id, sub.assignment_id FROM submissions sub"
            " JOIN entries e ON e.id = sub.entry_id WHERE sub.status <> 'cancelled'"
            " ORDER BY sub.started_at, sub.created_at, sub.id"
        )
    ).all()
    counters: dict[tuple[str, str], int] = {}
    for submission_id, student_id, assignment_id in subs:
        pair = (student_id, assignment_id)
        counters[pair] = counters.get(pair, 0) + 1
        bind.execute(
            sa.text("UPDATE submissions SET attempt_number = :n WHERE id = :id"),
            {"n": counters[pair], "id": submission_id},
        )


def downgrade() -> None:
    op.drop_column("submissions", "selection_usage")
    op.drop_column("submissions", "attempt_number")
    op.drop_constraint("fk_entries_student_id", "entries", type_="foreignkey")
    op.drop_index("ix_entries_student_id", table_name="entries")
    op.drop_column("entries", "device_hash")
    op.drop_column("entries", "student_id")
    op.drop_index("uq_students_auto_key", table_name="students")
    op.drop_index("ix_students_teacher_id", table_name="students")
    op.drop_table("students")
