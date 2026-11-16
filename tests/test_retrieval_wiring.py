import json
from types import SimpleNamespace

from qdrant_client import AsyncQdrantClient

from scripts.migrate_embeddings_to_qdrant import to_record
from src.vectorstore.base import VectorRecord
from src.vectorstore.qdrant_store import QdrantVectorStore
from webapp.routers import chat


class FakeAIService:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding
        self.calls = []

    async def embed_texts(self, texts, task_type="SEMANTIC_SIMILARITY"):
        self.calls.append((texts, task_type))
        return [self.embedding for _ in texts]


async def test_chat_retrieval_reads_contexts_from_the_configured_store(monkeypatch):
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "wiring", vector_size=3)
    await store.upsert(
        None,
        [
            VectorRecord(1, 7, 70, [1.0, 0.0, 0.0], "about recursion", "week1.pdf", 3, 3),
            VectorRecord(2, 7, 70, [0.0, 1.0, 0.0], "about sorting", "week1.pdf", 9, 9),
            VectorRecord(3, 8, 80, [1.0, 0.0, 0.0], "other course", "other.pdf", 1, 1),
        ],
    )
    monkeypatch.setattr(chat, "get_vector_store", lambda: store)
    monkeypatch.setattr(chat.settings, "rag_top_k", 1)
    ai_service = FakeAIService([0.9, 0.1, 0.0])

    contexts = await chat.retrieve_vector_contexts(None, ai_service, "what is recursion?", course_id=7)

    assert ai_service.calls == [(["what is recursion?"], "RETRIEVAL_QUERY")]
    assert [context.text for context in contexts] == ["about recursion"]
    assert contexts[0].metadata["course_document_id"] == 70
    assert contexts[0].metadata["page_start"] == 3
    assert contexts[0].metadata["title"] == "week1.pdf"
    await client.close()


def test_migration_builds_a_record_from_a_stored_chunk():
    chunk = SimpleNamespace(
        document_chunk_id=5,
        embedding_json=json.dumps([0.1, 0.2]),
        chunk_text_preview="preview",
        page_start=2,
        page_end=2,
        embedding_model="gemini-embedding-001",
    )
    document = SimpleNamespace(course_id=1, course_document_id=9, title="week2.pdf")

    record = to_record(chunk, document)

    assert record == VectorRecord(5, 1, 9, [0.1, 0.2], "preview", "week2.pdf", 2, 2, "gemini-embedding-001")


def test_migration_skips_unreadable_embeddings():
    document = SimpleNamespace(course_id=1, course_document_id=9, title="t")
    for raw in ("not json", "[]", "{}", None):
        chunk = SimpleNamespace(
            document_chunk_id=5, embedding_json=raw, chunk_text_preview="", page_start=1, page_end=1, embedding_model=None,
        )
        assert to_record(chunk, document) is None
