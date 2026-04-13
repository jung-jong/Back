from datetime import datetime
import json
import math
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.ai.service import AIService, RetrievedContext
from src.analytics.keyword_stat_service import upsert_course_keyword_stats
from src.analytics.weak_concept_service import extract_weak_concepts, upsert_weak_concepts
from src.models import ChatMessage, ChatMessageSource, ChatSession, Course, CourseDocument, DocumentChunk, Enrollment, User
from src.models.enums import EnrollmentStatus, MessageType, RecentSourceType, SenderType, UserRole
from webapp.routers.courses import get_course_for_user

router = APIRouter(prefix="/courses/{course_id}/chat", tags=["chat"])


class ChatRequest(BaseModel):
    content: str = Field(min_length=1)


async def get_student_enrollment(session: AsyncSession, course_id: int, current_user: User) -> Enrollment:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(status_code=403, detail="학생만 채팅할 수 있습니다.")
    result = await session.execute(
        select(Enrollment).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == current_user.user_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        ),
    )
    enrollment = result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="수강 정보를 찾을 수 없습니다.")
    return enrollment


async def retrieve_local_contexts(session: AsyncSession, question: str, course_id: int) -> list[RetrievedContext]:
    result = await session.execute(
        select(DocumentChunk, CourseDocument)
        .join(CourseDocument, DocumentChunk.course_document_id == CourseDocument.course_document_id)
        .where(CourseDocument.course_id == course_id, CourseDocument.deleted_at.is_(None))
        .order_by(DocumentChunk.created_at.desc())
        .limit(200),
    )
    question_terms = {
        term
        for term in re.findall(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]+", question.lower())
        if len(term) >= 2
    }
    contexts = []
    for chunk, document in result.all():
        text = chunk.chunk_text_preview or ""
        terms = set(re.findall(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]+", text.lower()))
        contexts.append(
            RetrievedContext(
                text=text,
                metadata={
                    "course_id": course_id,
                    "course_document_id": document.course_document_id,
                    "document_chunk_id": chunk.document_chunk_id,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "title": document.title,
                },
                score=float(len(question_terms & terms)),
            ),
        )
    contexts.sort(key=lambda item: item.score or 0, reverse=True)
    return [context for context in contexts[: settings.rag_top_k] if context.text.strip()]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


async def retrieve_tidb_vector_contexts(
    session: AsyncSession,
    ai_service: AIService,
    question: str,
    course_id: int,
) -> list[RetrievedContext]:
    query_embedding = (
        await ai_service.embed_texts([question], task_type="RETRIEVAL_QUERY")
    )[0]
    result = await session.execute(
        select(DocumentChunk, CourseDocument)
        .join(CourseDocument, DocumentChunk.course_document_id == CourseDocument.course_document_id)
        .where(
            CourseDocument.course_id == course_id,
            CourseDocument.deleted_at.is_(None),
            DocumentChunk.embedding_json.is_not(None),
        ),
    )
    contexts = []
    for chunk, document in result.all():
        try:
            chunk_embedding = json.loads(chunk.embedding_json or "[]")
        except json.JSONDecodeError:
            continue
        score = cosine_similarity(query_embedding, chunk_embedding)
        contexts.append(
            RetrievedContext(
                text=chunk.chunk_text_preview or "",
                metadata={
                    "course_id": course_id,
                    "course_document_id": document.course_document_id,
                    "document_chunk_id": chunk.document_chunk_id,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "title": document.title,
                },
                score=score,
            ),
        )
    contexts.sort(key=lambda item: item.score or 0, reverse=True)
    return [context for context in contexts[: settings.rag_top_k] if context.text.strip()]


async def source_labels(session: AsyncSession, chat_message_id: int) -> list[str]:
    result = await session.execute(
        select(ChatMessageSource, CourseDocument)
        .join(CourseDocument, ChatMessageSource.course_document_id == CourseDocument.course_document_id)
        .where(ChatMessageSource.chat_message_id == chat_message_id),
    )
    labels = []
    for source, document in result.all():
        if source.page_from and source.page_to and source.page_from != source.page_to:
            page = f" {source.page_from}-{source.page_to}p"
        elif source.page_from:
            page = f" {source.page_from}p"
        else:
            page = ""
        labels.append(f"{document.title}{page}")
    return labels


def message_response(message: ChatMessage, sources: list[str] | None = None) -> dict:
    role = "user" if message.sender_type == SenderType.STUDENT else "ai"
    return {
        "id": str(message.chat_message_id),
        "role": role,
        "content": message.message_text,
        "sources": sources if role == "ai" else None,
        "quiz": None,
    }


@router.get("")
async def list_chat_history(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    enrollment = await get_student_enrollment(session, course_id, current_user)
    result = await session.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .where(ChatSession.enrollment_id == enrollment.enrollment_id)
        .order_by(ChatMessage.created_at),
    )
    messages = []
    for message in result.scalars().all():
        sources = await source_labels(session, message.chat_message_id) if message.sender_type == SenderType.AI else None
        messages.append(message_response(message, sources))
    return messages


@router.post("")
async def send_chat_message(
    course_id: int,
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    enrollment = await get_student_enrollment(session, course_id, current_user)
    course = await get_course_for_user(session, course_id, current_user)
    chat_session = ChatSession(enrollment_id=enrollment.enrollment_id, title=payload.content[:255])
    session.add(chat_session)
    await session.flush()
    session.add(
        ChatMessage(
            chat_session_id=chat_session.chat_session_id,
            sender_type=SenderType.STUDENT,
            message_text=payload.content,
            message_type=MessageType.QUESTION,
        ),
    )
    await session.flush()
    ai_service = AIService()
    try:
        if settings.local_rag_enabled and ai_service.is_embedding_provider_configured:
            contexts = await retrieve_tidb_vector_contexts(session, ai_service, payload.content, course_id)
            if not contexts:
                contexts = await retrieve_local_contexts(session, payload.content, course_id)
        else:
            contexts = await retrieve_local_contexts(session, payload.content, course_id)
        await session.commit()
        answer = await ai_service.answer_with_contexts(payload.content, contexts, course.system_prompt)
    except (RuntimeError, httpx.HTTPError) as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    ai_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        sender_type=SenderType.AI,
        message_text=answer,
        message_type=MessageType.ANSWER,
    )
    session.add(ai_message)
    await session.flush()
    seen_documents = set()
    for context in contexts:
        document_id = context.metadata.get("course_document_id")
        if not document_id or int(document_id) in seen_documents:
            continue
        seen_documents.add(int(document_id))
        session.add(
            ChatMessageSource(
                chat_message_id=ai_message.chat_message_id,
                course_document_id=int(document_id),
                page_from=context.metadata.get("page_start"),
                page_to=context.metadata.get("page_end"),
                source_label=context.metadata.get("title"),
            ),
        )
    await upsert_weak_concepts(
        session,
        enrollment.enrollment_id,
        extract_weak_concepts(payload.content, answer, contexts),
        RecentSourceType.CHAT,
        ai_message.chat_message_id,
    )
    await upsert_course_keyword_stats(session, course_id, payload.content)
    enrollment.last_active_at = datetime.utcnow()
    await session.commit()
    return message_response(ai_message, await source_labels(session, ai_message.chat_message_id))


@router.post("/stream")
async def stream_chat_message(
    course_id: int,
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    message = await send_chat_message(course_id, payload, current_user, session)

    async def iterator():
        for chunk in re.findall(r".{1,80}", message["content"], flags=re.S):
            yield chunk

    return StreamingResponse(iterator(), media_type="text/plain")
