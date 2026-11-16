"""Before/after experiments: brute-force JSON search vs Qdrant.

    python -m scripts.benchmark_vector_search --qdrant-url http://localhost:6333

"before" reproduces the tidb_json store: read every embedding_json row of the
course, json.loads each, pure-Python cosine, sort, take top-k. The rows are
read from a local SQLite file standing in for TiDB, so there is no network
hop and the real system is slower than what is reported.

"after" is QdrantVectorStore.search against the server at --qdrant-url.
Without a URL the client's embedded mode is used, which has no HNSW index;
those numbers only prove the code path runs.

Vectors are random unit vectors, so this measures speed and how closely the
index matches exact search. It says nothing about answer quality.
"""

import argparse
import asyncio
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
from qdrant_client import AsyncQdrantClient

from src.vectorstore.base import VectorRecord
from src.vectorstore.qdrant_store import QdrantVectorStore
from src.vectorstore.tidb_json_store import cosine_similarity

COLLECTION = "custom_ta_benchmark"
DIM = 768
TOP_K = 5


class VectorSource:
    """Synthetic embeddings.

    random: independent Gaussian directions. Every pair of vectors is almost
    equally far apart, which is the hardest case for an approximate index.
    clustered: 100 topics spread through a 32-dimensional subspace, closer to
    how text embeddings of course material bunch up by subject.
    """

    def __init__(self, kind: str, rng: np.random.Generator) -> None:
        self.kind = kind
        self.rng = rng
        if kind == "clustered":
            self.centers = self._normalize(rng.standard_normal((100, DIM), dtype=np.float32))
            self.projection = rng.standard_normal((32, DIM), dtype=np.float32) / np.sqrt(32)

    @staticmethod
    def _normalize(vectors: np.ndarray) -> np.ndarray:
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def sample(self, count: int) -> np.ndarray:
        if self.kind == "random":
            return self._normalize(self.rng.standard_normal((count, DIM), dtype=np.float32))
        topics = self.rng.integers(0, len(self.centers), size=count)
        latent = self.rng.standard_normal((count, 32), dtype=np.float32)
        noise = self.rng.standard_normal((count, DIM), dtype=np.float32) * 0.01
        return self._normalize(self.centers[topics] + 0.6 * (latent @ self.projection) + noise)


def summarize(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
        "runs": len(ordered),
    }


class JsonRows:
    """document_chunks stand-in: one row per chunk, embedding as JSON text."""

    def __init__(self, directory: Path) -> None:
        self.connection = sqlite3.connect(directory / "chunks.sqlite")
        self.connection.execute(
            "CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY, course_id INTEGER, embedding_json TEXT)",
        )
        self.connection.execute("CREATE INDEX idx_course ON chunks (course_id)")
        self.json_bytes = 0
        self.rows = 0

    def insert(self, start_id: int, course_ids: np.ndarray, vectors: np.ndarray) -> None:
        payload = []
        for offset, (course_id, vector) in enumerate(zip(course_ids, vectors, strict=True)):
            raw = json.dumps(vector.tolist())
            self.json_bytes += len(raw)
            payload.append((start_id + offset, int(course_id), raw))
        self.connection.executemany("INSERT INTO chunks VALUES (?, ?, ?)", payload)
        self.connection.commit()
        self.rows += len(payload)

    def search(self, query: list[float], course_id: int) -> tuple[list[int], float, float]:
        """Returns (top chunk ids, fetch ms, parse+score ms)."""
        started = time.perf_counter()
        rows = self.connection.execute(
            "SELECT chunk_id, embedding_json FROM chunks WHERE course_id = ?",
            (course_id,),
        ).fetchall()
        fetched = time.perf_counter()
        scored = [(cosine_similarity(query, json.loads(raw)), chunk_id) for chunk_id, raw in rows]
        scored.sort(reverse=True)
        done = time.perf_counter()
        return (
            [chunk_id for _, chunk_id in scored[:TOP_K]],
            (fetched - started) * 1000,
            (done - fetched) * 1000,
        )

    def close(self) -> None:
        self.connection.close()


async def wait_until_indexed(client: AsyncQdrantClient, is_server: bool) -> dict:
    """Let the optimizer finish so searches hit the HNSW index, not a half-built segment."""
    if not is_server:
        return {"status": "embedded", "indexed_vectors": 0}
    deadline = time.monotonic() + 600
    stable = 0
    info = None
    while time.monotonic() < deadline:
        info = await client.get_collection(COLLECTION)
        if str(info.status).lower().endswith("green"):
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        await asyncio.sleep(1)
    return {
        "status": str(info.status).split(".")[-1].lower(),
        "indexed_vectors": info.indexed_vectors_count or 0,
    }


async def build_dataset(
    client: AsyncQdrantClient,
    is_server: bool,
    directory: Path,
    course_ids: np.ndarray,
    source: VectorSource,
) -> tuple[JsonRows, QdrantVectorStore, np.ndarray, dict]:
    total = len(course_ids)
    vectors = source.sample(total)
    rows = JsonRows(directory)
    if await client.collection_exists(COLLECTION):
        await client.delete_collection(COLLECTION)
    store = QdrantVectorStore(client, COLLECTION, DIM)

    json_seconds = 0.0
    qdrant_seconds = 0.0
    step = 2000
    for start in range(0, total, step):
        block = slice(start, start + step)
        started = time.perf_counter()
        rows.insert(start, course_ids[block], vectors[block])
        json_seconds += time.perf_counter() - started
        records = [
            VectorRecord(
                document_chunk_id=start + offset,
                course_id=int(course_id),
                course_document_id=int(course_id),
                embedding=vector.tolist(),
                text="",
            )
            for offset, (course_id, vector) in enumerate(zip(course_ids[block], vectors[block], strict=True))
        ]
        started = time.perf_counter()
        await store.upsert(None, records)
        qdrant_seconds += time.perf_counter() - started
    index_started = time.perf_counter()
    index_state = await wait_until_indexed(client, is_server)
    build = {
        "json_insert_s": round(json_seconds, 1),
        "qdrant_upsert_s": round(qdrant_seconds, 1),
        "qdrant_index_wait_s": round(time.perf_counter() - index_started, 1),
        "json_bytes_per_chunk": round(rows.json_bytes / total),
        "float32_bytes_per_chunk": DIM * 4,
        **index_state,
    }
    return rows, store, vectors, build


async def measure(
    rows: JsonRows,
    store: QdrantVectorStore,
    vectors: np.ndarray,
    course_ids: np.ndarray,
    course_id: int,
    before_runs: int,
    after_runs: int,
    source: VectorSource,
) -> dict:
    member_ids = np.flatnonzero(course_ids == course_id)
    queries = source.sample(max(before_runs, after_runs))
    exact = member_ids[np.argsort(-(queries @ vectors[member_ids].T), axis=1)[:, :TOP_K]]

    fetch_ms, score_ms, total_ms = [], [], []
    before_matches_exact = True
    for query, expected in zip(queries[:before_runs], exact, strict=False):
        ids, fetch, score = rows.search(query.tolist(), course_id)
        fetch_ms.append(fetch)
        score_ms.append(score)
        total_ms.append(fetch + score)
        before_matches_exact &= set(ids) == set(expected.tolist())

    await store.search(None, queries[0].tolist(), course_id=course_id, top_k=TOP_K)  # warm up
    after_ms = []
    found = 0
    for query, expected in zip(queries[:after_runs], exact, strict=False):
        started = time.perf_counter()
        hits = await store.search(None, query.tolist(), course_id=course_id, top_k=TOP_K)
        after_ms.append((time.perf_counter() - started) * 1000)
        found += len({hit.document_chunk_id for hit in hits} & set(expected.tolist()))

    before = summarize(total_ms)
    after = summarize(after_ms)
    return {
        "chunks_in_course": int(len(member_ids)),
        "before": {
            **before,
            "fetch_p50_ms": round(statistics.median(fetch_ms), 2),
            "score_p50_ms": round(statistics.median(score_ms), 2),
            "matches_exact": bool(before_matches_exact),
        },
        "after": {**after, "recall_at_5": round(found / (after_runs * TOP_K), 4)},
        "speedup_p50": round(before["p50_ms"] / after["p50_ms"], 1),
    }


async def ef_sweep(client, vectors: np.ndarray, source: VectorSource, runs: int = 100) -> list[dict]:
    """Same collection, different search effort: what recall costs in latency."""
    queries = source.sample(runs)
    exact = np.argsort(-(queries @ vectors.T), axis=1)[:, :TOP_K]
    rows = []
    for label, kwargs in (
        ("default", {}),
        ("hnsw_ef=128", {"hnsw_ef": 128}),
        ("hnsw_ef=256", {"hnsw_ef": 256}),
        ("hnsw_ef=512", {"hnsw_ef": 512}),
        ("hnsw_ef=1024", {"hnsw_ef": 1024}),
        ("exact (no index)", {"exact": True}),
    ):
        store = QdrantVectorStore(client, COLLECTION, DIM, **kwargs)
        await store.search(None, queries[0].tolist(), course_id=1, top_k=TOP_K)
        samples, found = [], 0
        for query, expected in zip(queries, exact, strict=True):
            started = time.perf_counter()
            hits = await store.search(None, query.tolist(), course_id=1, top_k=TOP_K)
            samples.append((time.perf_counter() - started) * 1000)
            found += len({hit.document_chunk_id for hit in hits} & set(expected.tolist()))
        rows.append({"setting": label, **summarize(samples), "recall_at_5": round(found / (runs * TOP_K), 4)})
    return rows


async def concurrent_wall_time(rows, store, course_id: int, clients: int, source: VectorSource) -> dict:
    """N students ask at once. The before path is CPU work inside the event loop, as in the app."""
    queries = [query.tolist() for query in source.sample(clients)]

    async def before(query):
        rows.search(query, course_id)

    async def after(query):
        await store.search(None, query, course_id=course_id, top_k=TOP_K)

    results = {}
    for label, call in (("before", before), ("after", after)):
        started = time.perf_counter()
        await asyncio.gather(*(call(query) for query in queries))
        results[f"{label}_wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    results["clients"] = clients
    return results


async def run(args: argparse.Namespace) -> dict:
    is_server = bool(args.qdrant_url)
    source = VectorSource(args.data, np.random.default_rng(7))

    def new_client() -> AsyncQdrantClient:
        if is_server:
            return AsyncQdrantClient(url=args.qdrant_url, api_key=args.qdrant_api_key, timeout=120)
        return AsyncQdrantClient(location=":memory:")

    report = {
        "environment": {
            "machine": f"{platform.system()} {platform.machine()}",
            "python": platform.python_version(),
            "qdrant": args.qdrant_url or "embedded (exact search, no HNSW)",
            "dim": DIM,
            "top_k": TOP_K,
            "data": args.data,
        },
        "single_course": [],
        "many_courses": None,
        "concurrency": None,
        "ef_sweep": None,
    }

    # A. One course holds every chunk: how each path scales with course size.
    for size in args.sizes:
        print(f"[A] single course, {size} chunks", flush=True)
        client = new_client()
        with tempfile.TemporaryDirectory() as directory:
            course_ids = np.ones(size, dtype=np.int64)
            rows, store, vectors, build = await build_dataset(client, is_server, Path(directory), course_ids, source)
            before_runs = 20 if size <= 2000 else (5 if size <= 20000 else 3)
            result = await measure(rows, store, vectors, course_ids, 1, before_runs, 50, source)
            if size == args.concurrency_size:
                report["concurrency"] = {
                    "chunks_in_course": size,
                    **await concurrent_wall_time(rows, store, 1, 8, source),
                }
            if is_server and size == max(args.sizes):
                report["ef_sweep"] = {"chunks": size, "rows": await ef_sweep(client, vectors, source)}
            rows.close()
        report["single_course"].append({"chunks": size, **result, "build": build})
        await client.delete_collection(COLLECTION)
        await client.close()

    # B. Custom-TA's real shape: many courses, each small. Search is always scoped to one.
    total, per_course = args.many_total, args.many_per_course
    print(f"[B] {total} chunks across {total // per_course} courses", flush=True)
    client = new_client()
    with tempfile.TemporaryDirectory() as directory:
        course_ids = np.repeat(np.arange(1, total // per_course + 1), per_course)
        rows, store, vectors, build = await build_dataset(client, is_server, Path(directory), course_ids, source)
        result = await measure(rows, store, vectors, course_ids, int(course_ids[total // 2]), 20, 50, source)
        rows.close()
    report["many_courses"] = {
        "total_chunks": total,
        "courses": total // per_course,
        **result,
        "build": build,
    }
    await client.delete_collection(COLLECTION)
    await client.close()
    return report


def print_report(report: dict) -> None:
    print("\nA. one course, growing")
    print(f"{'chunks':>8} | {'before p50':>10} {'(fetch':>8} {'score)':>8} | {'after p50':>9} {'p95':>7} | {'recall@5':>8} | {'x':>6} | hnsw")
    for row in report["single_course"]:
        before, after = row["before"], row["after"]
        print(
            f"{row['chunks']:>8} | {before['p50_ms']:>8.1f}ms {before['fetch_p50_ms']:>6.1f}ms {before['score_p50_ms']:>6.1f}ms"
            f" | {after['p50_ms']:>7.2f}ms {after['p95_ms']:>5.2f}ms | {after['recall_at_5']:>8.3f} | {row['speedup_p50']:>6.1f}"
            f" | {row['build']['indexed_vectors']}",
        )
    many = report["many_courses"]
    print(f"\nB. {many['total_chunks']} chunks in {many['courses']} courses, search one course of {many['chunks_in_course']}")
    print(
        f"   before p50 {many['before']['p50_ms']}ms | after p50 {many['after']['p50_ms']}ms"
        f" | recall@5 {many['after']['recall_at_5']} | x{many['speedup_p50']}",
    )
    if report["concurrency"]:
        c = report["concurrency"]
        print(f"\nC. {c['clients']} questions at once, course of {c['chunks_in_course']} chunks")
        print(f"   before {c['before_wall_ms']}ms until all answered | after {c['after_wall_ms']}ms")
    if report["ef_sweep"]:
        print(f"\nD. search effort on {report['ef_sweep']['chunks']} chunks")
        for row in report["ef_sweep"]["rows"]:
            print(f"   {row['setting']:<18} p50 {row['p50_ms']:>6.2f}ms  p95 {row['p95_ms']:>6.2f}ms  recall@5 {row['recall_at_5']:.3f}")
    last = report["single_course"][-1]["build"]
    print(f"\nstorage per chunk: JSON text {last['json_bytes_per_chunk']} bytes vs float32 {last['float32_bytes_per_chunk']} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[200, 2000, 20000, 50000])
    parser.add_argument("--concurrency-size", type=int, default=2000)
    parser.add_argument("--many-total", type=int, default=50000)
    parser.add_argument("--many-per-course", type=int, default=250)
    parser.add_argument("--data", choices=["random", "clustered"], default="clustered")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--qdrant-api-key")
    parser.add_argument("--out", type=Path, help="write the full report as JSON")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
