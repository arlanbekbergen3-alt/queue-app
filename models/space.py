from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SubjectSpaceORM(Base):
    __tablename__ = "subject_spaces"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class SpaceMemberORM(Base):
    __tablename__ = "space_members"
    __table_args__ = (UniqueConstraint("space_id", "user_id", name="uq_space_members"),)

    space_id: Mapped[str] = mapped_column(
        ForeignKey("subject_spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class PublicationORM(Base):
    __tablename__ = "space_publications"

    space_id: Mapped[str] = mapped_column(
        ForeignKey("subject_spaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class PublicationCriterionORM(Base):
    __tablename__ = "space_publication_criteria"

    publication_id: Mapped[str] = mapped_column(
        ForeignKey("space_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    max_points: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PublicationQuestionORM(Base):
    __tablename__ = "space_publication_questions"

    publication_id: Mapped[str] = mapped_column(
        ForeignKey("space_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    topic: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
