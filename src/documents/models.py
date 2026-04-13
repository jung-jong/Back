from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from database.base import Base
from src.models.enums import DocumentCategory, EmbeddingStatus


class CourseDocument(Base):
    __tablename__ = "course_documents"

    course_document_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("courses.course_id"),
        nullable=False,
    )
    week_number: Mapped[int | None] = mapped_column(Integer)
    document_category: Mapped[DocumentCategory | None] = mapped_column(
        SAEnum(DocumentCategory),
        server_default=text("'LECTURE'"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255))
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )
    original_file_name: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str | None] = mapped_column(String(20))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str | None] = mapped_column(String(500))
    embedding_status: Mapped[EmbeddingStatus | None] = mapped_column(
        SAEnum(EmbeddingStatus),
        server_default=text("'PENDING'"),
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id"),
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    course = relationship("Course", back_populates="documents")
    uploader = relationship(
        "User",
        foreign_keys=[uploaded_by],
        back_populates="uploaded_documents",
    )
    chunks = relationship("DocumentChunk", back_populates="course_document")
    message_sources = relationship("ChatMessageSource", back_populates="course_document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    document_chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    course_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("course_documents.course_document_id"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    vector_id: Mapped[str | None] = mapped_column(String(255))
    chunk_text_preview: Mapped[str | None] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    course_document = relationship("CourseDocument", back_populates="chunks")
