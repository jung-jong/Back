from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy.ext.asyncio import AsyncSession

from src.vectorstore.base import VectorHit, VectorRecord

UPSERT_BATCH_SIZE = 128


class QdrantVectorStore:
    """Chunk embeddings in a Qdrant collection, searched with its HNSW index.

    One collection holds every course; `course_id` is a payload field with its
    own index so the filter is applied inside the vector search, not after it.
    Point ids are `document_chunks.document_chunk_id`.
    """

    name = "qdrant"

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        vector_size: int,
        hnsw_ef: int | None = None,
        exact: bool = False,
    ) -> None:
        self._client = client
        self._collection = collection
        self._vector_size = vector_size
        # hnsw_ef: how many candidates the index walks per search. Higher finds
        # more of the true nearest chunks and costs latency. exact skips the index.
        self._search_params = (
            qm.SearchParams(hnsw_ef=hnsw_ef, exact=exact) if (hnsw_ef or exact) else None
        )
        self._ready = False

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(
                    size=self._vector_size,
                    distance=qm.Distance.COSINE,
                ),
            )
            for field_name in ("course_id", "course_document_id"):
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field_name,
                    field_schema=qm.PayloadSchemaType.INTEGER,
                )
        self._ready = True

    async def upsert(self, session: AsyncSession | None, records: list[VectorRecord]) -> None:
        if not records:
            return
        await self.ensure_collection()
        for record in records:
            if len(record.embedding) != self._vector_size:
                raise ValueError(
                    f"Embedding has {len(record.embedding)} dimensions but collection "
                    f"'{self._collection}' expects {self._vector_size}",
                )
        points = [
            qm.PointStruct(
                id=record.document_chunk_id,
                vector=record.embedding,
                payload={
                    "course_id": record.course_id,
                    "course_document_id": record.course_document_id,
                    "document_chunk_id": record.document_chunk_id,
                    "page_start": record.page_start,
                    "page_end": record.page_end,
                    "title": record.title,
                    "text": record.text,
                    "embedding_model": record.embedding_model,
                },
            )
            for record in records
        ]
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            await self._client.upsert(
                collection_name=self._collection,
                points=points[start : start + UPSERT_BATCH_SIZE],
                wait=True,
            )

    async def search(
        self,
        session: AsyncSession | None,
        query_embedding: list[float],
        course_id: int,
        top_k: int,
    ) -> list[VectorHit]:
        await self.ensure_collection()
        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_embedding,
            query_filter=qm.Filter(
                must=[qm.FieldCondition(key="course_id", match=qm.MatchValue(value=course_id))],
            ),
            limit=top_k,
            with_payload=True,
            search_params=self._search_params,
        )
        hits = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                VectorHit(
                    document_chunk_id=int(payload.get("document_chunk_id", point.id)),
                    course_id=int(payload.get("course_id", course_id)),
                    course_document_id=int(payload["course_document_id"]),
                    text=payload.get("text") or "",
                    score=float(point.score),
                    title=payload.get("title"),
                    page_start=payload.get("page_start"),
                    page_end=payload.get("page_end"),
                ),
            )
        return hits

    async def delete_document(self, session: AsyncSession | None, course_document_id: int) -> None:
        await self.ensure_collection()
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="course_document_id",
                            match=qm.MatchValue(value=course_document_id),
                        ),
                    ],
                ),
            ),
            wait=True,
        )

    async def count(self, course_id: int | None = None) -> int:
        await self.ensure_collection()
        count_filter = None
        if course_id is not None:
            count_filter = qm.Filter(
                must=[qm.FieldCondition(key="course_id", match=qm.MatchValue(value=course_id))],
            )
        result = await self._client.count(
            collection_name=self._collection,
            count_filter=count_filter,
            exact=True,
        )
        return result.count
