from functools import lru_cache

from core.config import settings
from src.vectorstore.base import VectorHit, VectorRecord, VectorStore

__all__ = ["VectorHit", "VectorRecord", "VectorStore", "get_vector_store"]


@lru_cache
def get_vector_store() -> VectorStore:
    provider = settings.vector_store.lower()
    if provider == "tidb_json":
        from src.vectorstore.tidb_json_store import TiDBJsonVectorStore

        return TiDBJsonVectorStore()
    if provider == "qdrant":
        from qdrant_client import AsyncQdrantClient

        from src.vectorstore.qdrant_store import QdrantVectorStore

        if settings.qdrant_url:
            client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
            )
        else:
            # Embedded mode: no server needed, data on local disk. Exact search
            # only (no HNSW), so use it for development, not for load.
            client = AsyncQdrantClient(path=str(settings.qdrant_local_path))
        return QdrantVectorStore(
            client=client,
            collection=settings.qdrant_collection,
            vector_size=settings.embedding_dimension,
            hnsw_ef=settings.qdrant_hnsw_ef,
        )
    raise RuntimeError(f"Unsupported VECTOR_STORE: {settings.vector_store}")
