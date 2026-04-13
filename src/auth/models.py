from datetime import datetime

from sqlalchemy import BigInteger, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import UserRole


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    student_no: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    instructed_courses = relationship(
        "Course",
        foreign_keys="Course.instructor_id",
        back_populates="instructor",
    )
    enrollments = relationship(
        "Enrollment",
        foreign_keys="Enrollment.student_id",
        back_populates="student",
    )
    uploaded_documents = relationship(
        "CourseDocument",
        foreign_keys="CourseDocument.uploaded_by",
        back_populates="uploader",
    )
    created_quests = relationship(
        "Quest",
        foreign_keys="Quest.created_by",
        back_populates="creator",
    )
    sent_course_messages = relationship(
        "CourseMessage",
        foreign_keys="CourseMessage.sender_user_id",
        back_populates="sender",
    )
