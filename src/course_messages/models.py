from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import TargetRuleType


class CourseMessage(Base):
    __tablename__ = "course_messages"

    course_message_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    target_rule_type: Mapped[TargetRuleType | None] = mapped_column(
        SAEnum(TargetRuleType),
        server_default=text("'ALL'"),
    )
    target_rule_value: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    course = relationship("Course", back_populates="messages")
    sender = relationship(
        "User",
        foreign_keys=[sender_user_id],
        back_populates="sent_course_messages",
    )
