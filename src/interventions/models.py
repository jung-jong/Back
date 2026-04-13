from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import InterventionStatus, InterventionType


class AIIntervention(Base):
    __tablename__ = "ai_interventions"

    ai_intervention_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    week_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    intervention_type: Mapped[InterventionType] = mapped_column(
        SAEnum(InterventionType),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_summary: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[str | None] = mapped_column(Text)
    action_detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[InterventionStatus | None] = mapped_column(
        SAEnum(InterventionStatus),
        server_default=text("'PENDING'"),
    )
    linked_quest_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("quests.quest_id"),
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    actioned_at: Mapped[datetime | None] = mapped_column(DateTime)

    course = relationship("Course", back_populates="interventions")
    linked_quest = relationship("Quest", back_populates="interventions")
