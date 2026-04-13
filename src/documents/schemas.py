from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.models.enums import DocumentCategory, EmbeddingStatus


class CourseDocumentResponse(BaseModel):
    course_document_id: int
    course_id: int
    week_number: int | None
    document_category: DocumentCategory | None
    title: str
    original_file_name: str | None
    file_type: str | None
    file_size_bytes: int | None
    page_count: int | None
    storage_path: str | None
    embedding_status: EmbeddingStatus | None
    uploaded_by: int | None
    uploaded_at: datetime | None
    deleted_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class DocumentChunkResponse(BaseModel):
    document_chunk_id: int
    course_document_id: int
    chunk_index: int
    page_start: int | None
    page_end: int | None
    vector_id: str | None
    chunk_text_preview: str | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
