"""End-to-end RAG flow through the real FastAPI app, once per vector store.

instructor signs up -> creates a course -> uploads two PDFs (indexed in the
background) -> student joins -> asks questions -> instructor deletes a file
-> student asks again.

Runs the same script with VECTOR_STORE=tidb_json and VECTOR_STORE=qdrant and
requires identical API responses and identical retrieved chunks.

What is real: routers, auth, SQLAlchemy models, PDF parsing, chunking,
indexing, retrieval, source labels, deletion. What is replaced: the DB is
SQLite instead of TiDB, files go to a temp folder instead of S3, Qdrant runs
in-process, the two analytics upserts after each answer (weak concepts,
keyword stats) are skipped because they use MySQL-only ON DUPLICATE KEY
UPDATE, and the AI service uses a deterministic bag-of-words embedding and
an answer that just lists its sources, so no API key is needed.

Set E2E_QDRANT_URL=http://localhost:6333 to run the qdrant arm against a
real Qdrant server instead.
"""

import hashlib
import math
import os
import re

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from sqlalchemy import BigInteger, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import create_async_engine

import database.database as database_module
import src.vectorstore as vectorstore_module
from core.config import settings
from database.base import Base
from src.ai.service import AIService
from src.models import DocumentChunk
from src.vectorstore.qdrant_store import QdrantVectorStore

DIM = 256
REAL_GET_VECTOR_STORE = vectorstore_module.get_vector_store


@compiles(BigInteger, "sqlite")
def _bigint_as_sqlite_rowid(type_, compiler, **kw):
    # SQLite only auto-increments an INTEGER PRIMARY KEY.
    return "INTEGER"


def make_pdf(pages: list[str]) -> bytes:
    """A minimal text PDF that pypdf can extract (ASCII, one line per page)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    font_id = 3
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for text in pages:
        content = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>",
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


AGILE_PDF = make_pdf(
    [
        "Product backlog lists every user story for the whole product ordered by priority",
        "Sprint backlog holds the stories and tasks selected for the current sprint",
        "Sprint review demonstrates working features while the retrospective improves teamwork",
    ],
)
QUALITY_PDF = make_pdf(
    [
        "Robustness means the program handles abnormal input without crashing",
        "Reliability means the software runs correctly for a long time without failure",
    ],
)


def bag_of_words(text: str) -> list[float]:
    vector = [0.0] * DIM
    for token in re.findall(r"[a-z]+", text.lower()):
        if len(token) > 2:
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@pytest.fixture
async def app_env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(database_module, "_engine", engine)
    monkeypatch.setattr(database_module, "_sessionmaker", None)

    async def no_runtime_tables():
        return None  # MySQL DDL; create_all above already made every table

    import webapp.main as main_module

    monkeypatch.setattr(main_module, "ensure_runtime_tables", no_runtime_tables)

    import webapp.routers.chat as chat_router

    async def skip_mysql_upsert(*args, **kwargs):
        return None  # ON DUPLICATE KEY UPDATE; unrelated to retrieval

    monkeypatch.setattr(chat_router, "upsert_weak_concepts", skip_mysql_upsert)
    monkeypatch.setattr(chat_router, "upsert_course_keyword_stats", skip_mysql_upsert)
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "local_rag_enabled", True)
    monkeypatch.setattr(settings, "rag_top_k", 2)

    answers = []

    async def embed_texts(self, texts, task_type="SEMANTIC_SIMILARITY"):
        return [bag_of_words(text) for text in texts]

    async def answer_with_contexts(self, question, contexts, system_prompt=None):
        answers.append([context.text for context in contexts])
        return "answer from: " + " | ".join(context.metadata.get("title", "") for context in contexts)

    monkeypatch.setattr(AIService, "embed_texts", embed_texts)
    monkeypatch.setattr(AIService, "answer_with_contexts", answer_with_contexts)
    monkeypatch.setattr(AIService, "is_embedding_provider_configured", property(lambda self: True))
    monkeypatch.setattr(AIService, "embedding_model_name", property(lambda self: "bag-of-words-test"))

    REAL_GET_VECTOR_STORE.cache_clear()
    yield main_module.app, answers, engine
    REAL_GET_VECTOR_STORE.cache_clear()
    await engine.dispose()


async def run_flow(app, answers, engine) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        async def signup(email, role):
            response = await client.post(
                "/auth/signup",
                json={"name": role, "email": email, "password": "password123", "role": role},
            )
            assert response.status_code == 201, response.text
            return {"Authorization": f"Bearer {response.json()['token']}"}

        teacher = await signup("teacher@example.com", "instructor")
        student = await signup("student@example.com", "student")

        course = await client.post("/courses", json={"name": "Software Engineering"}, headers=teacher)
        assert course.status_code == 201, course.text
        course_id, code = course.json()["id"], course.json()["authCode"]

        file_ids = {}
        for name, data in (("agile.pdf", AGILE_PDF), ("quality.pdf", QUALITY_PDF)):
            uploaded = await client.post(
                f"/courses/{course_id}/files",
                files={"file": (name, data, "application/pdf")},
                headers=teacher,
            )
            assert uploaded.status_code == 201, uploaded.text
            file_ids[name] = uploaded.json()["id"]

        files = await client.get(f"/courses/{course_id}/files", headers=teacher)
        rag_status = sorted((item["name"], item["ragStatus"]) for item in files.json())

        joined = await client.post("/courses/join", json={"code": code}, headers=student)
        assert joined.status_code == 200, joined.text

        async def ask(question):
            response = await client.post(f"/courses/{course_id}/chat", json={"content": question}, headers=student)
            assert response.status_code == 200, response.text
            body = response.json()
            return {"content": body["content"], "sources": body["sources"], "contexts": answers[-1]}

        first = await ask("What is the difference between product backlog and sprint backlog?")
        second = await ask("Which quality means handling abnormal input without crashing?")

        deleted = await client.delete(f"/courses/{course_id}/files/{file_ids['quality.pdf']}", headers=teacher)
        assert deleted.status_code == 204, deleted.text
        # Shares words with the remaining agile file so scores differ. With zero
        # overlap every chunk scores 0 and the two stores break the tie differently.
        after_delete = await ask("Which quality handles abnormal input, is it covered in the sprint review?")

        history = await client.get(f"/courses/{course_id}/chat", headers=student)
        assert history.status_code == 200

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(engine) as session:
        chunk_rows = (await session.execute(select(DocumentChunk))).scalars().all()

    return {
        "rag_status": rag_status,
        "first": first,
        "second": second,
        "after_delete": after_delete,
        "history_length": len(history.json()),
        "chunks_in_db": len(chunk_rows),
        "embedding_dims": sorted({row.embedding_dim for row in chunk_rows}),
    }


async def test_flow_with_tidb_json_matches_qdrant(app_env, monkeypatch):
    app, answers, engine = app_env

    monkeypatch.setattr(settings, "vector_store", "tidb_json")
    REAL_GET_VECTOR_STORE.cache_clear()
    before = await run_flow(app, answers, engine)

    # Fresh database for the second arm so ids and history line up.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    qdrant_url = os.getenv("E2E_QDRANT_URL")
    client = AsyncQdrantClient(url=qdrant_url) if qdrant_url else AsyncQdrantClient(location=":memory:")
    collection = "custom_ta_e2e"
    if await client.collection_exists(collection):
        await client.delete_collection(collection)
    store = QdrantVectorStore(client, collection, DIM)
    monkeypatch.setattr(settings, "vector_store", "qdrant")
    monkeypatch.setattr(vectorstore_module, "get_vector_store", lambda: store)
    import src.documents.service as document_service
    import webapp.routers.chat as chat_router
    import webapp.routers.documents as documents_router

    for module in (document_service, chat_router, documents_router):
        monkeypatch.setattr(module, "get_vector_store", lambda: store)

    after = await run_flow(app, answers, engine)
    points_left = await store.count()
    await client.delete_collection(collection)
    await client.close()

    # The feature behaves the same.
    assert before["rag_status"] == after["rag_status"] == [("agile.pdf", "ready"), ("quality.pdf", "ready")]
    for step in ("first", "second", "after_delete"):
        assert before[step] == after[step], step
    assert before["history_length"] == after["history_length"] == 6
    assert before["chunks_in_db"] == after["chunks_in_db"] == 5

    # And it answers from the right material.
    assert "Sprint backlog" in " ".join(after["first"]["contexts"])
    assert any(source.startswith("quality") for source in after["second"]["sources"])
    assert not any(source.startswith("quality") for source in after["after_delete"]["sources"])

    # Qdrant holds only the remaining file's chunks after the delete.
    assert points_left == 3
    assert after["embedding_dims"] == [DIM]


async def test_vector_store_health_reports_qdrant_points(app_env, monkeypatch):
    app, _, _ = app_env
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "health", 3)
    from src.vectorstore.base import VectorRecord

    await store.upsert(None, [VectorRecord(1, 1, 1, [1.0, 0.0, 0.0], "x")])
    import webapp.main as main_module

    monkeypatch.setattr(main_module, "get_vector_store", lambda: store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/health/vector-store")
    assert response.json() == {"status": "ok", "vector_store": "qdrant", "points": 1}

    async def broken():
        raise ConnectionError("refused")

    monkeypatch.setattr(store, "count", broken)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/health/vector-store")
    assert response.status_code == 503
    await client.close()
