from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import MessageType, SenderType


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("enrollments.enrollment_id"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    enrollment = relationship("Enrollment", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="chat_session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_sessions.chat_session_id"),
        nullable=False,
    )
    sender_type: Mapped[SenderType] = mapped_column(SAEnum(SenderType), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[MessageType | None] = mapped_column(
        SAEnum(MessageType),
        server_default=text("'QUESTION'"),
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    chat_session = relationship("ChatSession", back_populates="messages")
    sources = relationship("ChatMessageSource", back_populates="chat_message")


class ChatMessageSource(Base):
    __tablename__ = "chat_message_sources"

    chat_message_source_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_messages.chat_message_id"),
        nullable=False,
    )
    course_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("course_documents.course_document_id"),
        nullable=False,
    )
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    source_label: Mapped[str | None] = mapped_column(String(255))

    chat_message = relationship("ChatMessage", back_populates="sources")
    course_document = relationship("CourseDocument", back_populates="message_sources")
