from collections import Counter
from datetime import datetime
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CourseDocument, CourseKeywordStat

WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]+")
STOP_WORDS = {
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "or",
    "it",
    "be",
    "can",
    "please",
    "explain",
    "summary",
    "what",
    "when",
    "where",
    "why",
    "how",
    "the",
    "and",
    "for",
    "with",
}


def extract_question_keywords(text: str, limit: int = 5) -> list[str]:
    candidates = []
    for token in WORD_PATTERN.findall(text.lower()):
        if len(token) < 2 or token in STOP_WORDS:
            continue
        candidates.append(token[:100])

    return [keyword for keyword, _ in Counter(candidates).most_common(limit)]


async def resolve_course_week_number(session: AsyncSession, course_id: int) -> int:
    result = await session.execute(
        select(func.max(CourseDocument.week_number)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.week_number.is_not(None),
            CourseDocument.deleted_at.is_(None),
        ),
    )
    week_number = result.scalar_one_or_none()
    if week_number:
        return int(week_number)

    return max(1, int(datetime.utcnow().isocalendar().week))


async def upsert_course_keyword_stats(
    session: AsyncSession,
    course_id: int,
    question_text: str,
    week_number: int | None = None,
) -> list[str]:
    keywords = extract_question_keywords(question_text)
    if not keywords:
        return []

    resolved_week_number = week_number or await resolve_course_week_number(session, course_id)
    for keyword in keywords:
        result = await session.execute(
            select(CourseKeywordStat).where(
                CourseKeywordStat.course_id == course_id,
                CourseKeywordStat.week_number == resolved_week_number,
                CourseKeywordStat.keyword == keyword,
            ),
        )
        stat = result.scalar_one_or_none()
        if stat is None:
            session.add(
                CourseKeywordStat(
                    course_id=course_id,
                    week_number=resolved_week_number,
                    keyword=keyword,
                    mention_count=1,
                ),
            )
        else:
            stat.mention_count = (stat.mention_count or 0) + 1

    return keywords
