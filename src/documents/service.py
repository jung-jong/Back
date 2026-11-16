from io import BytesIO

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy import delete, select

from core.config import settings
from database.database import get_sessionmaker
from src.ai.service import AIService, VectorDocument
from src.documents.storage import read_stored_file_bytes
from src.models import CourseDocument, DocumentChunk
from src.models.enums import EmbeddingStatus
from src.vectorstore import VectorRecord, VectorStore, get_vector_store


class DocumentProcessingService:
    def __init__(
        self,
        ai_service: AIService | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.ai_service = ai_service or AIService()
        self.vector_store = vector_store or get_vector_store()

    async def process_course_document(self, course_document_id: int) -> None:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(CourseDocument).where(
                    CourseDocument.course_document_id == course_document_id,
                ),
            )
            document = result.scalar_one_or_none()
            if document is None:
                return

            document.embedding_status = EmbeddingStatus.PROCESSING
            await session.commit()

            try:
                pages = self._extract_pdf_pages(document.storage_path)
                document.page_count = len(pages)
                await session.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.course_document_id == course_document_id,
                    ),
                )
                # Re-processing replaces the chunks, so drop their old vectors too.
                await self.vector_store.delete_document(session, course_document_id)

                chunks = self._split_pages(pages)
                chunk_models = [
                    DocumentChunk(
                        course_document_id=course_document_id,
                        chunk_index=index,
                        page_start=chunk["page_start"],
                        page_end=chunk["page_end"],
                        vector_id=self._build_vector_id(course_document_id, index),
                        chunk_text_preview=chunk["text"][:1000],
                    )
                    for index, chunk in enumerate(chunks)
                ]
                session.add_all(chunk_models)
                await session.flush()

                vector_documents = [
                    VectorDocument(
                        id=chunk.vector_id,
                        text=chunks[index]["text"],
                        metadata={
                            "course_id": document.course_id,
                            "course_document_id": document.course_document_id,
                            "document_chunk_id": chunk.document_chunk_id,
                            "page_start": chunk.page_start,
                            "page_end": chunk.page_end,
                            "title": document.title,
                            "text": chunks[index]["text"],
                        },
                    )
                    for index, chunk in enumerate(chunk_models)
                    if chunk.vector_id is not None
                ]
                if self.ai_service.is_embedding_provider_configured:
                    embeddings = await self.ai_service.embed_texts(
                        [document.text for document in vector_documents],
                        task_type="RETRIEVAL_DOCUMENT",
                    )
                    records = []
                    for vector_document, embedding in zip(vector_documents, embeddings, strict=True):
                        metadata = vector_document.metadata
                        records.append(
                            VectorRecord(
                                document_chunk_id=metadata["document_chunk_id"],
                                course_id=metadata["course_id"],
                                course_document_id=metadata["course_document_id"],
                                embedding=embedding,
                                text=vector_document.text,
                                title=metadata["title"],
                                page_start=metadata["page_start"],
                                page_end=metadata["page_end"],
                                embedding_model=self.ai_service.embedding_model_name,
                            ),
                        )
                    for chunk, embedding in zip(chunk_models, embeddings, strict=True):
                        chunk.embedding_model = self.ai_service.embedding_model_name
                        chunk.embedding_dim = len(embedding)
                    await session.flush()
                    await self.vector_store.upsert(session, records)

                if not settings.local_rag_enabled:
                    document.embedding_status = EmbeddingStatus.FAILED
                    await session.commit()
                    return
                document.embedding_status = EmbeddingStatus.COMPLETED
                await session.commit()
            except Exception:
                await session.rollback()
                # The chunk rows are rolled back, so their vectors must not outlive them.
                try:
                    await self.vector_store.delete_document(session, course_document_id)
                except Exception:
                    pass
                document.embedding_status = EmbeddingStatus.FAILED
                session.add(document)
                await session.commit()
                raise

    def _extract_pdf_pages(self, storage_path: str | None) -> list[dict]:
        if not storage_path:
            raise ValueError("Document storage_path is empty")

        reader = PdfReader(BytesIO(read_stored_file_bytes(storage_path)))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"page": index, "text": text})
        return pages

    def _split_pages(self, pages: list[dict]) -> list[dict]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        )
        chunks = []
        for page in pages:
            for chunk_text in splitter.split_text(page["text"]):
                if chunk_text.strip():
                    chunks.append(
                        {
                            "text": chunk_text,
                            "page_start": page["page"],
                            "page_end": page["page"],
                        },
                    )
        return chunks

    def _build_vector_id(self, course_document_id: int, chunk_index: int) -> str:
        return f"course_document:{course_document_id}:chunk:{chunk_index}"
