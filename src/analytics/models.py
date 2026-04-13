from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base


class CourseKeywordStat(Base):
    __tablename__ = "course_keyword_stats"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "week_number",
            "keyword",
            name="unique_course_week_keyword",
        ),
    )

    course_keyword_stat_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    mention_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    calculated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )

    course = relationship("Course", back_populates="keyword_stats")
