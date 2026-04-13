from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from database.base import Base


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "course_id",
            "notification_key",
            name="unique_notification_read_user_course_key",
        ),
    )

    notification_read_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    notification_key: Mapped[str] = mapped_column(String(64), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id",
            "course_id",
            "message_key",
            name="unique_quiz_attempt_enrollment_course_message",
        ),
    )

    quiz_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("enrollments.enrollment_id"),
        nullable=False,
    )
    message_key: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
