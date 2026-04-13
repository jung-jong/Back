from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import (
    Difficulty,
    QuestCreatorType,
    QuestStatus,
    QuestionType,
    StudentQuestStatus,
    TargetRuleType,
)


class Quest(Base):
    __tablename__ = "quests"

    quest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    creator_type: Mapped[QuestCreatorType] = mapped_column(
        SAEnum(QuestCreatorType),
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope_week_start: Mapped[int | None] = mapped_column(Integer)
    scope_week_end: Mapped[int | None] = mapped_column(Integer)
    difficulty: Mapped[Difficulty | None] = mapped_column(
        SAEnum(Difficulty),
        server_default=text("'NORMAL'"),
    )
    status: Mapped[QuestStatus | None] = mapped_column(
        SAEnum(QuestStatus),
        server_default=text("'DRAFT'"),
    )
    xp_reward: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    target_rule_type: Mapped[TargetRuleType | None] = mapped_column(
        SAEnum(TargetRuleType),
        server_default=text("'ALL'"),
    )
    target_rule_value: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    course = relationship("Course", back_populates="quests")
    creator = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="created_quests",
    )
    questions = relationship("QuestQuestion", back_populates="quest")
    student_quests = relationship("StudentQuest", back_populates="quest")
    interventions = relationship("AIIntervention", back_populates="linked_quest")


class QuestQuestion(Base):
    __tablename__ = "quest_questions"

    quest_question_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    quest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quests.quest_id"),
        nullable=False,
    )
    question_order: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[QuestionType] = mapped_column(SAEnum(QuestionType), nullable=False)
    points: Mapped[int | None] = mapped_column(Integer, server_default=text("1"))
    correct_answer_text: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)

    quest = relationship("Quest", back_populates="questions")
    choices = relationship("QuestQuestionChoice", back_populates="quest_question")
    student_answers = relationship("StudentQuestAnswer", back_populates="quest_question")


class QuestQuestionChoice(Base):
    __tablename__ = "quest_question_choices"

    quest_question_choice_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    quest_question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quest_questions.quest_question_id"),
        nullable=False,
    )
    choice_order: Mapped[int] = mapped_column(Integer, nullable=False)
    choice_text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_correct: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )

    quest_question = relationship("QuestQuestion", back_populates="choices")
    student_answers = relationship(
        "StudentQuestAnswer",
        back_populates="selected_choice",
    )


class StudentQuest(Base):
    __tablename__ = "student_quests"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "quest_id", name="unique_enrollment_quest"),
    )

    student_quest_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("enrollments.enrollment_id"),
        nullable=False,
    )
    quest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quests.quest_id"),
        nullable=False,
    )
    status: Mapped[StudentQuestStatus | None] = mapped_column(
        SAEnum(StudentQuestStatus),
        server_default=text("'ASSIGNED'"),
    )
    score_earned: Mapped[int | None] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer, nullable=False)
    xp_awarded: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime)

    enrollment = relationship("Enrollment", back_populates="student_quests")
    quest = relationship("Quest", back_populates="student_quests")
    answers = relationship("StudentQuestAnswer", back_populates="student_quest")


class StudentQuestAnswer(Base):
    __tablename__ = "student_quest_answers"

    student_quest_answer_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    student_quest_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("student_quests.student_quest_id"),
        nullable=False,
    )
    quest_question_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quest_questions.quest_question_id"),
        nullable=False,
    )
    selected_choice_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("quest_question_choices.quest_question_choice_id"),
    )
    answer_text: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    score_earned: Mapped[int | None] = mapped_column(Integer)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    student_quest = relationship("StudentQuest", back_populates="answers")
    quest_question = relationship("QuestQuestion", back_populates="student_answers")
    selected_choice = relationship(
        "QuestQuestionChoice",
        back_populates="student_answers",
    )
