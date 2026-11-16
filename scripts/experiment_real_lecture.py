"""One before/after experiment on real lecture PDFs uploaded to one course.

    python -m scripts.experiment_real_lecture \
        --pdf-dir "<folder of lecture PDFs>" \
        --qdrant-url http://localhost:6333 \
        --embedder local --out docs/experiments/real_lecture.json

What happens, end to end:
1. Every PDF is read and split with the app's own DocumentProcessingService
   (pypdf per page, RAG_CHUNK_SIZE / RAG_CHUNK_OVERLAP), as if an instructor
   uploaded the files into one course.
2. Every chunk is embedded once.
3. before: rows are written the way the tidb_json store kept them
   (embedding_json text + chunk_text_preview cut to 1000 chars) into a local
   SQLite file, then searched the way it did: read all rows of the course,
   json.loads, pure-Python cosine, sort, top 5.
   after: the same vectors and full chunk text go into Qdrant and are
   searched with QdrantVectorStore.search.
4. A fixed set of student questions is asked against both. For each one we
   record latency, whether both return the same top 5, whether a chunk from
   the lecture that covers the topic is in the top 5, and how many characters
   of context would reach the LLM.

SQLite stands in for TiDB, so the before numbers leave out the network trip
that ships every embedding to the app; the real before path is slower.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pypdf import PdfReader
from qdrant_client import AsyncQdrantClient

from src.documents.service import DocumentProcessingService
from src.vectorstore.base import VectorRecord
from src.vectorstore.qdrant_store import QdrantVectorStore
from src.vectorstore.tidb_json_store import cosine_similarity

COLLECTION = "custom_ta_real_lecture"
COURSE_ID = 1
TOP_K = 5
PREVIEW_CHARS = 1000  # what process_course_document kept in chunk_text_preview

# Student-style questions for the JNU Software Engineering course. `expect`
# lists the file-name prefixes of the lectures that cover the topic, chosen
# from the course notes in the wiki (not from the search results).
QUESTIONS = [
    ("비정상적인 입력이 들어와도 프로그램이 죽지 않는 품질 특성은 뭐야?", ["2-1", "2-2"]),
    ("정확성과 신뢰성의 차이가 뭐야?", ["2-1", "2-2"]),
    ("ISO 25010 품질 특성에는 어떤 것들이 있어?", ["2-1", "2-2"]),
    ("SPICE는 무엇을 평가하는 기준이야?", ["2-1", "2-2"]),
    ("외적 품질과 내적 품질은 어떻게 구분해?", ["2-1", "2-2"]),
    ("폭포수 모델의 단점은 뭐야?", ["3-1"]),
    ("점증적 모델과 프로토타입 모델은 어떻게 달라?", ["3-1"]),
    ("V 모델에서 검증과 확인의 차이를 알려줘", ["3-1"]),
    ("나선형 모델은 어떤 프로젝트에 적합해?", ["3-1"]),
    ("프로덕트 백로그와 스프린트 백로그의 차이는?", ["4-1", "5-1", "5-2"]),
    ("스프린트 리뷰와 회고는 뭐가 달라?", ["4-1", "5-1", "5-2"]),
    ("유저 스토리는 어떻게 작성해?", ["4-1", "5-1", "5-2"]),
    ("인셉션 덱이 뭐야?", ["4-1", "5-1", "5-2"]),
    ("데일리 스크럼에서는 무엇을 이야기해?", ["4-1", "5-1", "5-2"]),
    ("애자일 선언문의 핵심 가치는?", ["4-1", "5-1", "5-2"]),
    ("JIRA에서 에픽과 이슈는 어떻게 관리해?", ["5-2"]),
    ("WBS는 왜 만들어?", ["6-1"]),
    ("CPM에서 임계 경로는 어떻게 구해?", ["6-1"]),
    ("간트 차트는 무엇을 보여줘?", ["6-1"]),
    ("COCOMO로 개발 노력을 추정하는 방법은?", ["6-1"]),
    ("프로젝트 위험 관리는 어떤 절차로 해?", ["6-1"]),
    ("기능점수는 어떻게 계산해?", ["6-1"]),
    ("기능 요구사항과 비기능 요구사항의 차이는?", ["6-2", "7-1"]),
    ("요구사항 도출 기법에는 무엇이 있어?", ["6-2", "7-1"]),
    ("페르소나는 어떻게 만들어?", ["6-2", "7-1"]),
    ("이해관계자 분석은 왜 해야 해?", ["6-2", "7-1"]),
    ("요구공학의 주요 활동 순서를 알려줘", ["6-2", "7-1"]),
    ("유스케이스 다이어그램에서 액터와 시스템 경계는 어떻게 그려?", ["7-1", "3-1"]),
    ("DFD와 액티비티 다이어그램은 어떻게 달라?", ["7-1", "3-1"]),
    ("좋은 요구사항 명세가 갖춰야 할 조건은?", ["6-2", "7-1"]),
    ("소프트웨어 위기란 무엇이야?", ["1-2"]),
    ("소프트웨어공학은 어떻게 정의돼?", ["1-2"]),
]


@dataclass(frozen=True)
class Chunk:
    chunk_id: int
    file_name: str
    page: int
    text: str


def load_chunks(pdf_dir: Path) -> tuple[list[Chunk], dict]:
    splitter = DocumentProcessingService.__new__(DocumentProcessingService)
    chunks: list[Chunk] = []
    files = sorted(pdf_dir.glob("*.pdf"))
    pages_total = 0
    for pdf in files:
        reader = PdfReader(str(pdf))
        pages_total += len(reader.pages)
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"page": number, "text": text})
        for piece in splitter._split_pages(pages):
            chunks.append(Chunk(len(chunks) + 1, pdf.name, piece["page_start"], piece["text"]))
    lengths = [len(chunk.text) for chunk in chunks]
    corpus = {
        "files": len(files),
        "pages": pages_total,
        "chunks": len(chunks),
        "chunk_chars_median": int(statistics.median(lengths)),
        "chunks_longer_than_preview": sum(length > PREVIEW_CHARS for length in lengths),
    }
    return chunks, corpus


class LocalEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding

        self.name = model
        self._model = TextEmbedding(model)

    def embed(self, texts: list[str], is_query: bool) -> np.ndarray:
        vectors = np.array(list(self._model.embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class GeminiEmbedder:
    """Uses the app's AIService, so it needs GEMINI_API_KEY and EMBEDDING_PROVIDER=gemini in .env."""

    def __init__(self) -> None:
        from src.ai.service import AIService

        self._service = AIService()
        if self._service.embedding_provider != "gemini" or not self._service.is_embedding_provider_configured:
            raise SystemExit("Set EMBEDDING_PROVIDER=gemini and GEMINI_API_KEY in .env to use --embedder gemini")
        self.name = self._service.embedding_model_name

    def embed(self, texts: list[str], is_query: bool) -> np.ndarray:
        from concurrent.futures import ThreadPoolExecutor

        task = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
        # Called from inside the experiment's event loop, so run the async call on its own loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            vectors = pool.submit(asyncio.run, self._service.embed_texts(texts, task_type=task)).result()
        return np.array(vectors, dtype=np.float32)


class UpstageEmbedder:
    """Upstage Solar Embedding 2 through its OpenAI-compatible endpoint.

    Reads UPSTAGE_API_KEY from the environment or from this repo's .env.
    Passages and queries use separate models, as Upstage recommends.
    """

    BATCH = 100  # per-request text limit in the Upstage docs

    def __init__(self) -> None:
        import os

        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv(".env")
        api_key = os.getenv("UPSTAGE_API_KEY")
        if not api_key:
            raise SystemExit("Put UPSTAGE_API_KEY=... in custom-ta-back/.env to use --embedder upstage")
        self._client = OpenAI(api_key=api_key, base_url="https://api.upstage.ai/v1")
        self.name = "solar-embedding-2 (passage/query)"

    def embed(self, texts: list[str], is_query: bool) -> np.ndarray:
        model = "solar-embedding-2-query" if is_query else "solar-embedding-2-passage"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH):
            response = self._client.embeddings.create(model=model, input=texts[start : start + self.BATCH])
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
        array = np.array(vectors, dtype=np.float32)
        return array / np.linalg.norm(array, axis=1, keepdims=True)


def cached_embeddings(embedder, texts: list[str], cache_dir: Path, is_query: bool) -> np.ndarray:
    key = hashlib.sha256((embedder.name + str(is_query) + "\x00".join(texts)).encode()).hexdigest()[:16]
    path = cache_dir / f"{key}.npy"
    if path.exists():
        return np.load(path)
    vectors = embedder.embed(texts, is_query)
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(path, vectors)
    return vectors


def before_search(connection: sqlite3.Connection, query: list[float]) -> tuple[list[tuple[int, str]], float]:
    started = time.perf_counter()
    rows = connection.execute(
        "SELECT chunk_id, embedding_json, chunk_text_preview FROM chunks WHERE course_id = ?",
        (COURSE_ID,),
    ).fetchall()
    scored = [(cosine_similarity(query, json.loads(raw)), chunk_id, text) for chunk_id, raw, text in rows]
    scored.sort(reverse=True)
    elapsed = (time.perf_counter() - started) * 1000
    return [(chunk_id, text) for _, chunk_id, text in scored[:TOP_K]], elapsed


async def run(args: argparse.Namespace) -> dict:
    chunks, corpus = load_chunks(args.pdf_dir)
    print(f"{corpus['files']} PDFs, {corpus['pages']} pages -> {corpus['chunks']} chunks", flush=True)

    embedder = {
        "local": lambda: LocalEmbedder(args.model),
        "gemini": GeminiEmbedder,
        "upstage": UpstageEmbedder,
    }[args.embedder]()
    started = time.perf_counter()
    chunk_vectors = cached_embeddings(embedder, [chunk.text for chunk in chunks], args.cache_dir, is_query=False)
    question_vectors = cached_embeddings(embedder, [q for q, _ in QUESTIONS], args.cache_dir, is_query=True)
    print(f"embedded with {embedder.name} ({chunk_vectors.shape[1]} dims) in {time.perf_counter() - started:.1f}s", flush=True)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}

    with tempfile.TemporaryDirectory() as directory:
        connection = sqlite3.connect(Path(directory) / "chunks.sqlite")
        connection.execute(
            "CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY, course_id INTEGER, embedding_json TEXT, chunk_text_preview TEXT)",
        )
        connection.execute("CREATE INDEX idx_course ON chunks (course_id)")
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?)",
            [
                (
                    chunk.chunk_id,
                    COURSE_ID,
                    json.dumps(vector.tolist()),
                    chunk.text[:PREVIEW_CHARS] if args.before_text == "preview" else chunk.text,
                )
                for chunk, vector in zip(chunks, chunk_vectors, strict=True)
            ],
        )
        connection.commit()
        json_bytes = connection.execute("SELECT SUM(LENGTH(embedding_json)) FROM chunks").fetchone()[0]

        client = (
            AsyncQdrantClient(url=args.qdrant_url, timeout=120)
            if args.qdrant_url
            else AsyncQdrantClient(location=":memory:")
        )
        if await client.collection_exists(COLLECTION):
            await client.delete_collection(COLLECTION)
        store = QdrantVectorStore(client, COLLECTION, int(chunk_vectors.shape[1]))
        await store.upsert(
            None,
            [
                VectorRecord(
                    document_chunk_id=chunk.chunk_id,
                    course_id=COURSE_ID,
                    course_document_id=COURSE_ID,
                    embedding=vector.tolist(),
                    text=chunk.text,
                    title=chunk.file_name,
                    page_start=chunk.page,
                    page_end=chunk.page,
                )
                for chunk, vector in zip(chunks, chunk_vectors, strict=True)
            ],
        )
        # Warm up both arms so neither pays first-call costs inside the measurement.
        before_search(connection, question_vectors[0].tolist())
        await store.search(None, question_vectors[0].tolist(), course_id=COURSE_ID, top_k=TOP_K)

        rows = []
        for (question, expect), vector in zip(QUESTIONS, question_vectors, strict=True):
            query = vector.tolist()
            before_ms, after_ms = [], []
            for _ in range(args.repeats):
                before_hits, elapsed = before_search(connection, query)
                before_ms.append(elapsed)
                started = time.perf_counter()
                after_hits = await store.search(None, query, course_id=COURSE_ID, top_k=TOP_K)
                after_ms.append((time.perf_counter() - started) * 1000)
            before_ids = [chunk_id for chunk_id, _ in before_hits]
            after_ids = [hit.document_chunk_id for hit in after_hits]

            def first_relevant(ids: list[int]) -> int | None:
                return next(
                    (
                        rank
                        for rank, chunk_id in enumerate(ids, start=1)
                        if any(by_id[chunk_id].file_name.startswith(prefix) for prefix in expect)
                    ),
                    None,
                )
            rows.append(
                {
                    "question": question,
                    "expect": expect,
                    "before_p50_ms": round(statistics.median(before_ms), 2),
                    "after_p50_ms": round(statistics.median(after_ms), 2),
                    "same_top5": set(before_ids) == set(after_ids),
                    "same_order": before_ids == after_ids,
                    "before_relevant_rank": first_relevant(before_ids),
                    "relevant_rank": first_relevant(after_ids),
                    "top1": f"{by_id[after_ids[0]].file_name[:24]} p.{by_id[after_ids[0]].page}" if after_ids else None,
                    "before_context_chars": sum(len(text) for _, text in before_hits),
                    "after_context_chars": sum(len(hit.text) for hit in after_hits),
                },
            )
        await client.delete_collection(COLLECTION)
        await client.close()
        connection.close()

    def quality(ranks: list[int | None]) -> dict:
        return {
            "hit_at_1": round(sum(rank == 1 for rank in ranks) / len(ranks), 3),
            "hit_at_5": round(sum(rank is not None for rank in ranks) / len(ranks), 3),
            "mrr_at_5": round(sum(1 / rank for rank in ranks if rank) / len(ranks), 3),
        }

    all_before_ms = [row["before_p50_ms"] for row in rows]
    all_after_ms = [row["after_p50_ms"] for row in rows]
    summary = {
        "questions": len(rows),
        "before_p50_ms": round(statistics.median(all_before_ms), 2),
        "after_p50_ms": round(statistics.median(all_after_ms), 2),
        "before_max_ms": round(max(all_before_ms), 2),
        "after_max_ms": round(max(all_after_ms), 2),
        "same_top5_rate": round(sum(row["same_top5"] for row in rows) / len(rows), 3),
        "same_order_rate": round(sum(row["same_order"] for row in rows) / len(rows), 3),
        "before_quality": quality([row["before_relevant_rank"] for row in rows]),
        "after_quality": quality([row["relevant_rank"] for row in rows]),
        "before_context_chars_avg": round(statistics.mean(row["before_context_chars"] for row in rows)),
        "after_context_chars_avg": round(statistics.mean(row["after_context_chars"] for row in rows)),
    }
    summary["speedup"] = round(summary["before_p50_ms"] / summary["after_p50_ms"], 1)
    return {
        "environment": {
            "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "qdrant": args.qdrant_url or "embedded (exact search, no HNSW)",
            "embedder": embedder.name,
            "dims": int(chunk_vectors.shape[1]),
            "repeats_per_question": args.repeats,
        },
        "controlled": {
            "embedding": f"{embedder.name}, computed once and shared by both arms",
            "chunks": "same 483 chunks from the app's splitter",
            "questions": f"same {len(QUESTIONS)} questions, same query vectors",
            "top_k": TOP_K,
            "similarity": "cosine",
            "stored_text": "full chunk text in both" if args.before_text == "full" else "before keeps first 1000 chars",
            "variable": "vector storage and search: embedding_json rows + Python cosine vs Qdrant",
        },
        "corpus": {**corpus, "embedding_json_bytes_per_chunk": round(json_bytes / corpus["chunks"])},
        "summary": summary,
        "questions": rows,
    }


def print_report(report: dict) -> None:
    s, c = report["summary"], report["corpus"]
    print(f"\ncourse: {c['files']} PDFs, {c['pages']} pages, {c['chunks']} chunks ({report['environment']['embedder']})")
    print(f"{'':<4}{'question':<44}{'before':>9}{'after':>8}  same  rank  top1")
    for index, row in enumerate(report["questions"], start=1):
        rank = row["relevant_rank"] or "-"
        print(
            f"{index:<4}{row['question'][:42]:<44}{row['before_p50_ms']:>7.1f}ms{row['after_p50_ms']:>6.2f}ms"
            f"  {'Y' if row['same_top5'] else 'N':^4}  {rank!s:^4}  {row['top1']}",
        )
    print(
        f"\nsearch p50: before {s['before_p50_ms']}ms, after {s['after_p50_ms']}ms (x{s['speedup']})"
        f"\nsame top 5 as before: {s['same_top5_rate']:.0%} (same order: {s['same_order_rate']:.0%})"
        f"\nbefore: right lecture top1 {s['before_quality']['hit_at_1']:.0%}, top5 {s['before_quality']['hit_at_5']:.0%}, MRR@5 {s['before_quality']['mrr_at_5']}"
        f"\nafter:  right lecture top1 {s['after_quality']['hit_at_1']:.0%}, top5 {s['after_quality']['hit_at_5']:.0%}, MRR@5 {s['after_quality']['mrr_at_5']}"
        f"\ncontext sent to LLM per question: before {s['before_context_chars_avg']} chars, after {s['after_context_chars_avg']} chars",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--embedder", choices=["local", "gemini", "upstage"], default="upstage")
    parser.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument(
        "--before-text",
        choices=["full", "preview"],
        default="full",
        help="full: both arms store the same text (only the vector store differs). "
        "preview: before keeps the first 1000 chars, as the original app did.",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("static/vector_store/embedding_cache"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
