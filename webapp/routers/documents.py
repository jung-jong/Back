import re
from datetime import datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.documents.service import DocumentProcessingService
from src.documents.storage import get_storage_service, remove_stored_file, stored_file_url
from src.models import CourseDocument, User
from src.models.enums import DocumentCategory, EmbeddingStatus, UserRole
from webapp.routers.courses import get_course_for_user

router = APIRouter(prefix="/courses/{course_id}/files", tags=["files"])


class FileMetadataPatch(BaseModel):
    week: str | None = None
    topic: str | None = None


class FilePublishPatch(BaseModel):
    isPublished: bool


def human_size(size: int | None) -> str:
    if not size:
        return "0B"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"


def parse_week(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None


def file_download_url(request: Request, document: CourseDocument) -> str | None:
    return stored_file_url(request, document.storage_path)


def document_topic(document: CourseDocument) -> str:
    return document.topic or ""


def file_response(request: Request, document: CourseDocument) -> dict:
    topic = document_topic(document)
    return {
        "id": str(document.course_document_id),
        "name": document.original_file_name or document.title,
        "size": human_size(document.file_size_bytes),
        "uploadedAt": document.uploaded_at.date().isoformat() if document.uploaded_at else "",
        "week": f"{document.week_number}\uc8fc\ucc28" if document.week_number else "",
        "topic": topic,
        "isPublished": bool(document.is_published),
        "ragStatus": "ready" if document.embedding_status == EmbeddingStatus.COMPLETED else "indexing",
        "url": file_download_url(request, document),
    }


def can_publish(document: CourseDocument) -> bool:
    return bool(document.week_number and document_topic(document).strip())


async def process_document_background(course_document_id: int) -> None:
    try:
        await DocumentProcessingService().process_course_document(course_document_id)
    except Exception:
        return


@router.get("")
async def list_course_files(
    course_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await get_course_for_user(session, course_id, current_user)
    result = await session.execute(
        select(CourseDocument)
        .where(CourseDocument.course_id == course_id, CourseDocument.deleted_at.is_(None))
        .order_by(CourseDocument.uploaded_at.desc()),
    )
    documents = list(result.scalars().all())
    if current_user.role == UserRole.STUDENT:
        documents = [document for document in documents if document.is_published and can_publish(document)]
    return [file_response(request, document) for document in documents]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_course_file(
    course_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")
    stored_file = await get_storage_service().upload_file(file, course_id=course_id)
    document = CourseDocument(
        course_id=course_id,
        document_category=DocumentCategory.LECTURE,
        title=Path(stored_file.original_file_name).stem,
        topic=None,
        is_published=False,
        original_file_name=stored_file.original_file_name,
        file_type=stored_file.file_type,
        file_size_bytes=stored_file.file_size_bytes,
        storage_path=stored_file.storage_path,
        embedding_status=EmbeddingStatus.PENDING,
        uploaded_by=current_user.user_id,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    background_tasks.add_task(process_document_background, document.course_document_id)
    return file_response(request, document)


@router.patch("/{file_id}")
async def update_course_file(
    course_id: int,
    file_id: int,
    payload: FileMetadataPatch,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    document = await session.get(CourseDocument, file_id)
    if document is None or document.course_id != course_id or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    if payload.week is not None:
        document.week_number = parse_week(payload.week)
    if payload.topic is not None:
        document.topic = payload.topic.strip() or None
    if document.is_published and not can_publish(document):
        document.is_published = False
    await session.commit()
    await session.refresh(document)
    return file_response(request, document)


@router.patch("/{file_id}/publish")
async def publish_course_file(
    course_id: int,
    file_id: int,
    payload: FilePublishPatch,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    document = await session.get(CourseDocument, file_id)
    if document is None or document.course_id != course_id or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    if payload.isPublished and not can_publish(document):
        raise HTTPException(status_code=400, detail="주차와 주제를 먼저 설정해야 공개할 수 있습니다.")
    document.is_published = payload.isPublished
    await session.commit()
    await session.refresh(document)
    return file_response(request, document)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course_file(
    course_id: int,
    file_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    document = await session.get(CourseDocument, file_id)
    if document is None or document.course_id != course_id:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    document.deleted_at = datetime.utcnow()
    await session.commit()
    remove_stored_file(document.storage_path)
    return Response(status_code=204)
