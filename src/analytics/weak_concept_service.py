import re

from sqlalchemy import func
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import WeakConcept
from src.models.enums import RecentSourceType
from src.ai.service import RetrievedContext


STOPWORDS = {
    "what",
    "is",
    "are",
    "was",
    "were",
    "which",
    "when",
    "where",
    "why",
    "how",
    "does",
    "did",
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "about",
    "please",
    "explain",
    "summary",
}


def extract_weak_concepts(
    question: str,
    answer: str,
    contexts: list[RetrievedContext],
    limit: int = 5,
) -> list[str]:
    combined = " ".join(
        [
            question,
            answer,
            *[context.text for context in contexts],
        ],
    ).lower()
    terms = re.findall(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]+", combined)
    counts: dict[str, int] = {}
    for term in terms:
        normalized = term.strip()
        if len(normalized) < 2 or normalized in STOPWORDS:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1

    sorted_terms = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return [term for term, _ in sorted_terms[:limit]]


async def upsert_weak_concepts(
    session: AsyncSession,
    enrollment_id: int,
    concepts: list[str],
    recent_source_type: RecentSourceType,
    recent_source_ref_id: int | None,
) -> None:
    for concept in concepts:
        statement = insert(WeakConcept).values(
            enrollment_id=enrollment_id,
            concept_name=concept,
            error_count=1,
            recent_source_type=recent_source_type,
            recent_source_ref_id=recent_source_ref_id,
        )
        statement = statement.on_duplicate_key_update(
            error_count=func.coalesce(WeakConcept.error_count, 0) + 1,
            recent_source_type=recent_source_type,
            recent_source_ref_id=recent_source_ref_id,
            last_seen_at=func.current_timestamp(),
        )
        await session.execute(statement)
