import math
import random

import pytest
from qdrant_client import AsyncQdrantClient

from src.vectorstore.base import VectorRecord
from src.vectorstore.qdrant_store import QdrantVectorStore
from src.vectorstore.tidb_json_store import cosine_similarity

DIM = 16


def unit_vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    values = [rng.gauss(0, 1) for _ in range(DIM)]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def record(chunk_id: int, course_id: int, document_id: int) -> VectorRecord:
    return VectorRecord(
        document_chunk_id=chunk_id,
        course_id=course_id,
        course_document_id=document_id,
        embedding=unit_vector(chunk_id),
        text=f"chunk {chunk_id}",
        title=f"doc {document_id}",
        page_start=chunk_id,
        page_end=chunk_id,
        embedding_model="test-model",
    )


@pytest.fixture
async def store():
    client = AsyncQdrantClient(location=":memory:")
    yield QdrantVectorStore(client=client, collection="test_chunks", vector_size=DIM)
    await client.close()


async def test_search_returns_same_ranking_as_exact_cosine(store):
    records = [record(chunk_id, course_id=1, document_id=10) for chunk_id in range(1, 41)]
    await store.upsert(None, records)
    query = unit_vector(999)

    hits = await store.search(None, query, course_id=1, top_k=5)

    expected = sorted(
        records,
        key=lambda item: cosine_similarity(query, item.embedding),
        reverse=True,
    )[:5]
    assert [hit.document_chunk_id for hit in hits] == [item.document_chunk_id for item in expected]
    assert hits[0].score == pytest.approx(cosine_similarity(query, expected[0].embedding), abs=1e-5)


async def test_search_only_returns_chunks_of_the_requested_course(store):
    await store.upsert(
        None,
        [record(1, course_id=1, document_id=10), record(2, course_id=2, document_id=20)],
    )

    hits = await store.search(None, unit_vector(2), course_id=1, top_k=5)

    assert [hit.document_chunk_id for hit in hits] == [1]
    assert hits[0].metadata() == {
        "course_id": 1,
        "course_document_id": 10,
        "document_chunk_id": 1,
        "page_start": 1,
        "page_end": 1,
        "title": "doc 10",
    }
    assert hits[0].text == "chunk 1"


async def test_delete_document_removes_only_that_documents_vectors(store):
    await store.upsert(
        None,
        [
            record(1, course_id=1, document_id=10),
            record(2, course_id=1, document_id=10),
            record(3, course_id=1, document_id=11),
        ],
    )

    await store.delete_document(None, 10)

    hits = await store.search(None, unit_vector(1), course_id=1, top_k=5)
    assert [hit.document_chunk_id for hit in hits] == [3]
    assert await store.count() == 1


async def test_upsert_replaces_existing_chunk(store):
    await store.upsert(None, [record(1, course_id=1, document_id=10)])
    await store.upsert(None, [record(1, course_id=1, document_id=10)])

    assert await store.count(course_id=1) == 1


async def test_upsert_rejects_wrong_dimension(store):
    bad = VectorRecord(
        document_chunk_id=1,
        course_id=1,
        course_document_id=10,
        embedding=[0.1, 0.2],
        text="x",
    )
    with pytest.raises(ValueError, match="dimensions"):
        await store.upsert(None, [bad])


async def test_search_on_empty_collection_returns_nothing(store):
    assert await store.search(None, unit_vector(1), course_id=1, top_k=5) == []
