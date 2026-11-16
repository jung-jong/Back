import json
import math

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CourseDocument, DocumentChunk
from src.vectorstore.base import VectorHit, VectorRecord


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class TiDBJsonVectorStore:
    """Original approach: embeddings as JSON text in `document_chunks`, scored in Python.

    Exact search over every chunk of the course. Kept as a zero-dependency
    fallback and as the baseline for `scripts/benchmark_vector_search.py`.
    """

    name = "tidb_json"

    async def upsert(self, session: AsyncSession, records: list[VectorRecord]) -> None:
        for record in records:
            await session.execute(
                update(DocumentChunk)
                .where(DocumentChunk.document_chunk_id == record.document_chunk_id)
                .values(
                    embedding_json=json.dumps(record.embedding),
                    embedding_model=record.embedding_model,
                    embedding_dim=len(record.embedding),
                ),
            )

    async def search(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        course_id: int,
        top_k: int,
    ) -> list[VectorHit]:
        result = await session.execute(
            select(DocumentChunk, CourseDocument)
            .join(
                CourseDocument,
                DocumentChunk.course_document_id == CourseDocument.course_document_id,
            )
            .where(
                CourseDocument.course_id == course_id,
                CourseDocument.deleted_at.is_(None),
                DocumentChunk.embedding_json.is_not(None),
            ),
        )
        hits = []
        for chunk, document in result.all():
            try:
                chunk_embedding = json.loads(chunk.embedding_json or "[]")
            except json.JSONDecodeError:
                continue
            hits.append(
                VectorHit(
                    document_chunk_id=chunk.document_chunk_id,
                    course_id=course_id,
                    course_document_id=document.course_document_id,
                    text=chunk.chunk_text_preview or "",
                    score=cosine_similarity(query_embedding, chunk_embedding),
                    title=document.title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                ),
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    async def delete_document(self, session: AsyncSession, course_document_id: int) -> None:
        # Rows are filtered out by `course_documents.deleted_at`; nothing else to clean up.
        return None
