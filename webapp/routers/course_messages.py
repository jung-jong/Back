from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.dependency import get_current_user
from database.database import get_db_session
from src.models import Course, CourseMessage, Enrollment, User
from src.models.enums import EnrollmentStatus, TargetRuleType, UserRole
from src.course_messages.schemas import CourseMessageCreate, CourseMessageResponse

router = APIRouter(prefix="/course-messages", tags=["course-messages"])


async def get_owned_course(
    session: AsyncSession,
    course_id: int,
    current_user: User,
) -> Course:
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can manage course messages",
        )

    result = await session.execute(
        select(Course).where(
            Course.course_id == course_id,
            Course.instructor_id == current_user.user_id,
        ),
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found",
        )
    return course


def to_message_response(message: CourseMessage, course_name: str | None = None) -> CourseMessageResponse:
    return CourseMessageResponse(
        course_message_id=message.course_message_id,
        course_id=message.course_id,
        course_name=course_name,
        sender_user_id=message.sender_user_id,
        title=message.title,
        body=message.body,
        target_rule_type=message.target_rule_type,
        target_rule_value=message.target_rule_value,
        created_at=message.created_at,
    )


def is_message_targeted_to_enrollment(
    message: CourseMessage,
    enrollment: Enrollment,
) -> bool:
    target_rule_type = message.target_rule_type or TargetRuleType.ALL
    if target_rule_type == TargetRuleType.ALL:
        return True

    values = {
        item.strip().upper()
        for item in (message.target_rule_value or "").replace("·", ",").replace(".", ",").split(",")
        if item.strip()
    }
    if target_rule_type == TargetRuleType.RANK:
        rank = getattr(enrollment.current_rank, "value", enrollment.current_rank)
        return str(rank or "").upper() in values
    if target_rule_type == TargetRuleType.SELECTED:
        return str(enrollment.enrollment_id) in values
    return False


@router.post("", response_model=CourseMessageResponse, status_code=status.HTTP_201_CREATED)
async def create_course_message(
    payload: CourseMessageCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CourseMessageResponse:
    course = await get_owned_course(session, payload.course_id, current_user)
    message = CourseMessage(
        course_id=payload.course_id,
        sender_user_id=current_user.user_id,
        title=payload.title,
        body=payload.body,
        target_rule_type=payload.target_rule_type,
        target_rule_value=payload.target_rule_value,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return to_message_response(message, course.course_name)


@router.get("/course/{course_id}", response_model=list[CourseMessageResponse])
async def list_course_messages(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[CourseMessageResponse]:
    course = await get_owned_course(session, course_id, current_user)
    result = await session.execute(
        select(CourseMessage)
        .where(CourseMessage.course_id == course_id)
        .order_by(CourseMessage.created_at.desc()),
    )
    return [
        to_message_response(message, course.course_name)
        for message in result.scalars().all()
    ]


@router.get("/me", response_model=list[CourseMessageResponse])
async def list_my_course_messages(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[CourseMessageResponse]:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can list received course messages",
        )

    result = await session.execute(
        select(CourseMessage, Course, Enrollment)
        .join(Course, CourseMessage.course_id == Course.course_id)
        .join(Enrollment, Enrollment.course_id == Course.course_id)
        .where(
            Enrollment.student_id == current_user.user_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .order_by(CourseMessage.created_at.desc()),
    )
    responses: list[CourseMessageResponse] = []
    for message, course, enrollment in result.all():
        if is_message_targeted_to_enrollment(message, enrollment):
            responses.append(to_message_response(message, course.course_name))
    return responses
