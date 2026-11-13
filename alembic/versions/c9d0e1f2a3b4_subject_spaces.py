from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subject_spaces",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("invite_code", sa.String(length=12), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invite_code"),
    )
    op.create_index("ix_subject_spaces_owner_id", "subject_spaces", ["owner_id"], unique=False)
    op.create_table(
        "space_members",
        sa.Column("space_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["subject_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_members"),
    )
    op.create_index("ix_space_members_space_id", "space_members", ["space_id"], unique=False)
    op.create_index("ix_space_members_user_id", "space_members", ["user_id"], unique=False)
    op.create_table(
        "space_publications",
        sa.Column("space_id", sa.String(), nullable=False),
        sa.Column("author_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["subject_spaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_publications_space_id", "space_publications", ["space_id"], unique=False
    )
    op.create_index(
        "ix_space_publications_author_id", "space_publications", ["author_id"], unique=False
    )
    op.create_table(
        "space_publication_criteria",
        sa.Column("publication_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("max_points", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["space_publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_publication_criteria_publication_id",
        "space_publication_criteria",
        ["publication_id"],
        unique=False,
    )
    op.create_table(
        "space_publication_questions",
        sa.Column("publication_id", sa.String(), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["space_publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_publication_questions_publication_id",
        "space_publication_questions",
        ["publication_id"],
        unique=False,
    )
    op.add_column("assignments", sa.Column("origin_author", sa.String(length=50), nullable=True))
    op.add_column("assignments", sa.Column("origin_space", sa.String(length=80), nullable=True))
    op.add_column("assignments", sa.Column("origin_title", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("assignments", "origin_title")
    op.drop_column("assignments", "origin_space")
    op.drop_column("assignments", "origin_author")
    op.drop_table("space_publication_questions")
    op.drop_table("space_publication_criteria")
    op.drop_table("space_publications")
    op.drop_table("space_members")
    op.drop_table("subject_spaces")
