from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import EnrollmentStatus, Rank, RecentSourceType


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "course_id", name="unique_student_course"),
    )

    enrollment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    status: Mapped[EnrollmentStatus | None] = mapped_column(
        SAEnum(EnrollmentStatus),
        server_default=text("'ACTIVE'"),
    )
    current_rank: Mapped[Rank | None] = mapped_column(
        SAEnum(Rank),
        server_default=text("'C'"),
    )
    current_xp: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    joined_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime)

    student = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="enrollments",
    )
    course = relationship("Course", back_populates="enrollments")
    chat_sessions = relationship("ChatSession", back_populates="enrollment")
    student_quests = relationship("StudentQuest", back_populates="enrollment")
    weak_concepts = relationship("WeakConcept", back_populates="enrollment")


class WeakConcept(Base):
    __tablename__ = "weak_concepts"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "concept_name", name="unique_enrollment_concept"),
    )

    weak_concept_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("enrollments.enrollment_id"),
        nullable=False,
    )
    concept_name: Mapped[str] = mapped_column(String(255), nullable=False)
    error_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
    recent_source_type: Mapped[RecentSourceType] = mapped_column(
        SAEnum(RecentSourceType),
        nullable=False,
    )
    recent_source_ref_id: Mapped[int | None] = mapped_column(BigInteger)

    enrollment = relationship("Enrollment", back_populates="weak_concepts")
