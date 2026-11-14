from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class RosterEntryORM(Base):
    __tablename__ = "roster_entries"
    __table_args__ = (
        UniqueConstraint("teacher_id", "group_key", "name_key", name="uq_roster_group_name"),
        Index("ix_roster_teacher_group", "teacher_id", "group_key"),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    group_key: Mapped[str] = mapped_column(String(60), nullable=False)
    group_name: Mapped[str] = mapped_column(String(60), nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
