"""Copy embeddings from document_chunks.embedding_json into Qdrant.

Run once when switching VECTOR_STORE from tidb_json to qdrant:

    python -m scripts.migrate_embeddings_to_qdrant --dry-run
    python -m scripts.migrate_embeddings_to_qdrant

Safe to re-run: points are keyed by document_chunk_id, so a second run
overwrites instead of duplicating. embedding_json is left in place so
VECTOR_STORE=tidb_json keeps working as a rollback.
"""

import argparse
import asyncio
import json

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select

from core.config import settings
from database.database import get_sessionmaker
from src.models import CourseDocument, DocumentChunk
from src.vectorstore.base import VectorRecord
from src.vectorstore.qdrant_store import QdrantVectorStore


def to_record(chunk: DocumentChunk, document: CourseDocument) -> VectorRecord | None:
    try:
        embedding = json.loads(chunk.embedding_json or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(embedding, list) or not embedding:
        return None
    return VectorRecord(
        document_chunk_id=chunk.document_chunk_id,
        course_id=document.course_id,
        course_document_id=document.course_document_id,
        embedding=embedding,
        # Only the first 1000 characters were kept in the DB for old chunks.
        text=chunk.chunk_text_preview or "",
        title=document.title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        embedding_model=chunk.embedding_model,
    )


async def migrate(batch_size: int, dry_run: bool) -> None:
    if settings.qdrant_url:
        client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    else:
        client = AsyncQdrantClient(path=str(settings.qdrant_local_path))
    store = QdrantVectorStore(client, settings.qdrant_collection, settings.embedding_dimension)

    migrated = skipped = wrong_dimension = 0
    last_id = 0
    async with get_sessionmaker()() as session:
        while True:
            result = await session.execute(
                select(DocumentChunk, CourseDocument)
                .join(
                    CourseDocument,
                    DocumentChunk.course_document_id == CourseDocument.course_document_id,
                )
                .where(
                    CourseDocument.deleted_at.is_(None),
                    DocumentChunk.embedding_json.is_not(None),
                    DocumentChunk.document_chunk_id > last_id,
                )
                .order_by(DocumentChunk.document_chunk_id)
                .limit(batch_size),
            )
            rows = result.all()
            if not rows:
                break
            last_id = rows[-1][0].document_chunk_id

            records = []
            for chunk, document in rows:
                record = to_record(chunk, document)
                if record is None:
                    skipped += 1
                elif len(record.embedding) != settings.embedding_dimension:
                    wrong_dimension += 1
                else:
                    records.append(record)
            if not dry_run:
                await store.upsert(session, records)
            migrated += len(records)
            print(f"  up to chunk {last_id}: {migrated} ready")

    verb = "would migrate" if dry_run else "migrated"
    print(f"{verb} {migrated} chunks into '{settings.qdrant_collection}'")
    print(f"skipped {skipped} with unreadable embedding_json")
    print(
        f"skipped {wrong_dimension} whose dimension is not {settings.embedding_dimension} "
        "(re-upload those documents to re-embed them)",
    )
    if not dry_run:
        print(f"collection now holds {await store.count()} points")
    await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true", help="read and validate, write nothing")
    args = parser.parse_args()
    asyncio.run(migrate(args.batch_size, args.dry_run))


if __name__ == "__main__":
    main()
