from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class UserORM(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
