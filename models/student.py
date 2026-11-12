from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class StudentORM(Base):
    __tablename__ = "students"
    __table_args__ = (
        Index(
            "uq_students_auto_key",
            "teacher_id",
            "group_key",
            "name_key",
            unique=True,
            postgresql_where=text("auto_link"),
        ),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_key: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    name_key: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    auto_link: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    merged_into_id: Mapped[str | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
