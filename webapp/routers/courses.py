import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.models import Course, Enrollment, User
from src.models.enums import CourseStatus, EnrollmentStatus, Rank, UserRole

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    semester: str | None = None


class CourseJoinRequest(BaseModel):
    code: str = Field(min_length=4, max_length=20)


def generate_entry_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def resolve_entry_code(session: AsyncSession, requested_entry_code: str | None = None) -> str:
    if requested_entry_code is not None:
        return requested_entry_code.upper()
    for _ in range(10):
        entry_code = generate_entry_code()
        result = await session.execute(select(Course.course_id).where(Course.entry_code == entry_code))
        if result.scalar_one_or_none() is None:
            return entry_code
    raise HTTPException(status_code=503, detail="강의 인증 코드를 생성하지 못했습니다.")


def date_text(value) -> str:
    return value.date().isoformat() if value else ""


async def get_student_count(session: AsyncSession, course_id: int) -> int:
    result = await session.execute(
        select(func.count(Enrollment.enrollment_id)).where(Enrollment.course_id == course_id),
    )
    return int(result.scalar_one() or 0)


async def course_response(session: AsyncSession, course: Course) -> dict:
    instructor = await session.get(User, course.instructor_id)
    student_count = await get_student_count(session, course.course_id)
    return {
        "id": str(course.course_id),
        "name": course.course_name,
        "description": course.course_description,
        "studentCount": student_count,
        "authCode": course.entry_code,
        "createdAt": date_text(course.created_at),
        "hasData": student_count > 0,
        "instructorName": instructor.name if instructor else "",
        "semester": course.term,
    }


async def get_course_for_user(
    session: AsyncSession,
    course_id: int,
    current_user: User,
    instructor_only: bool = False,
) -> Course:
    if current_user.role == UserRole.INSTRUCTOR:
        result = await session.execute(
            select(Course).where(Course.course_id == course_id, Course.instructor_id == current_user.user_id),
        )
    elif not instructor_only:
        result = await session.execute(
            select(Course)
            .join(Enrollment, Course.course_id == Enrollment.course_id)
            .where(
                Course.course_id == course_id,
                Enrollment.student_id == current_user.user_id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            ),
        )
    else:
        raise HTTPException(status_code=403, detail="교강사만 사용할 수 있습니다.")
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="강의를 찾을 수 없습니다.")
    return course


@router.get("/me")
async def list_my_courses(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    if current_user.role == UserRole.INSTRUCTOR:
        result = await session.execute(
            select(Course)
            .where(Course.instructor_id == current_user.user_id)
            .order_by(Course.created_at.desc()),
        )
    else:
        result = await session.execute(
            select(Course)
            .join(Enrollment, Course.course_id == Enrollment.course_id)
            .where(Enrollment.student_id == current_user.user_id, Enrollment.status == EnrollmentStatus.ACTIVE)
            .order_by(Enrollment.joined_at.desc()),
        )
    return [await course_response(session, course) for course in result.scalars().all()]


@router.get("/{course_id}")
async def get_course(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    course = await get_course_for_user(session, course_id, current_user)
    return await course_response(session, course)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(status_code=403, detail="교강사만 강의를 개설할 수 있습니다.")
    course = Course(
        instructor_id=current_user.user_id,
        course_name=payload.name,
        course_description=payload.description,
        term=payload.semester or "2026-1학기",
        entry_code=await resolve_entry_code(session),
    )
    session.add(course)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="강의 인증 코드가 중복되었습니다.") from None
    await session.refresh(course)
    return await course_response(session, course)


@router.post("/join")
async def join_course(
    payload: CourseJoinRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(status_code=403, detail="학생만 강의에 입장할 수 있습니다.")
    result = await session.execute(
        select(Course).where(Course.entry_code == payload.code.upper(), Course.status == CourseStatus.ACTIVE),
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="유효하지 않은 인증 코드입니다.")
    existing = await session.execute(
        select(Enrollment).where(Enrollment.student_id == current_user.user_id, Enrollment.course_id == course.course_id),
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="이미 참여 중인 강의입니다.")
    session.add(
        Enrollment(
            student_id=current_user.user_id,
            course_id=course.course_id,
            current_rank=Rank.C,
            current_xp=0,
        ),
    )
    await session.commit()
    return await course_response(session, course)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    params = {"course_id": course_id}
    statements = [
        "DELETE FROM chat_message_sources WHERE chat_message_id IN (SELECT cm.chat_message_id FROM chat_messages cm JOIN chat_sessions cs ON cm.chat_session_id = cs.chat_session_id JOIN enrollments e ON cs.enrollment_id = e.enrollment_id WHERE e.course_id = :course_id)",
        "DELETE FROM chat_messages WHERE chat_session_id IN (SELECT cs.chat_session_id FROM chat_sessions cs JOIN enrollments e ON cs.enrollment_id = e.enrollment_id WHERE e.course_id = :course_id)",
        "DELETE FROM chat_sessions WHERE enrollment_id IN (SELECT enrollment_id FROM enrollments WHERE course_id = :course_id)",
        "DELETE FROM student_quest_answers WHERE student_quest_id IN (SELECT sq.student_quest_id FROM student_quests sq JOIN enrollments e ON sq.enrollment_id = e.enrollment_id WHERE e.course_id = :course_id)",
        "DELETE FROM student_quests WHERE enrollment_id IN (SELECT enrollment_id FROM enrollments WHERE course_id = :course_id)",
        "DELETE FROM ai_interventions WHERE course_id = :course_id",
        "DELETE FROM course_messages WHERE course_id = :course_id",
        "DELETE FROM quest_question_choices WHERE quest_question_id IN (SELECT qq.quest_question_id FROM quest_questions qq JOIN quests q ON qq.quest_id = q.quest_id WHERE q.course_id = :course_id)",
        "DELETE FROM quest_questions WHERE quest_id IN (SELECT quest_id FROM quests WHERE course_id = :course_id)",
        "DELETE FROM quests WHERE course_id = :course_id",
        "DELETE FROM document_chunks WHERE course_document_id IN (SELECT course_document_id FROM course_documents WHERE course_id = :course_id)",
        "DELETE FROM course_documents WHERE course_id = :course_id",
        "DELETE FROM course_keyword_stats WHERE course_id = :course_id",
        "DELETE FROM weak_concepts WHERE enrollment_id IN (SELECT enrollment_id FROM enrollments WHERE course_id = :course_id)",
        "DELETE FROM enrollments WHERE course_id = :course_id",
        "DELETE FROM courses WHERE course_id = :course_id",
    ]
    for statement in statements:
        await session.execute(text(statement), params)
    await session.commit()
    return Response(status_code=204)
