from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class VectorRecord:
    """One chunk to index: its DB id, embedding, and the payload stored with it."""

    document_chunk_id: int
    course_id: int
    course_document_id: int
    embedding: list[float]
    text: str
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    embedding_model: str | None = None


@dataclass(frozen=True)
class VectorHit:
    document_chunk_id: int
    course_id: int
    course_document_id: int
    text: str
    score: float
    title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    extra: dict = field(default_factory=dict)

    def metadata(self) -> dict:
        return {
            "course_id": self.course_id,
            "course_document_id": self.course_document_id,
            "document_chunk_id": self.document_chunk_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "title": self.title,
        }


class VectorStore(Protocol):
    """Storage and similarity search for chunk embeddings.

    Every method takes the request's DB session so an implementation that
    lives inside the relational DB can join the caller's transaction. Stores
    backed by an external service ignore it.
    """

    name: str

    async def upsert(self, session: AsyncSession, records: list[VectorRecord]) -> None: ...

    async def search(
        self,
        session: AsyncSession,
        query_embedding: list[float],
        course_id: int,
        top_k: int,
    ) -> list[VectorHit]: ...

    async def delete_document(self, session: AsyncSession, course_document_id: int) -> None: ...
