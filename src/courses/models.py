from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import CourseStatus


class Course(Base):
    __tablename__ = "courses"

    course_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instructor_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    course_name: Mapped[str] = mapped_column(String(255), nullable=False)
    course_description: Mapped[str | None] = mapped_column(Text)
    term: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CourseStatus | None] = mapped_column(
        SAEnum(CourseStatus),
        server_default=text("'ACTIVE'"),
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    instructor = relationship(
        "User",
        foreign_keys=[instructor_id],
        back_populates="instructed_courses",
    )
    enrollments = relationship("Enrollment", back_populates="course")
    documents = relationship("CourseDocument", back_populates="course")
    quests = relationship("Quest", back_populates="course")
    keyword_stats = relationship("CourseKeywordStat", back_populates="course")
    interventions = relationship("AIIntervention", back_populates="course")
    messages = relationship("CourseMessage", back_populates="course")
