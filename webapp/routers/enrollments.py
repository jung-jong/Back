from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dependencies.dependency import get_current_user
from database.database import get_db_session
from src.models import Course, Enrollment, User, WeakConcept
from src.models.enums import CourseStatus, EnrollmentStatus, UserRole
from src.enrollments.schemas import (
    EnrollmentJoinRequest,
    EnrollmentResponse,
    WeakConceptResponse,
)

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


@router.post("/join", response_model=EnrollmentResponse, status_code=status.HTTP_201_CREATED)
async def join_course(
    payload: EnrollmentJoinRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Enrollment:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can join courses",
        )

    result = await session.execute(
        select(Course).where(
            Course.entry_code == payload.entry_code.upper(),
            Course.status == CourseStatus.ACTIVE,
        ),
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active course not found for entry code",
        )

    existing_result = await session.execute(
        select(Enrollment)
        .options(selectinload(Enrollment.course))
        .where(
            Enrollment.student_id == current_user.user_id,
            Enrollment.course_id == course.course_id,
        ),
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if existing.status == EnrollmentStatus.DROPPED:
            existing.status = EnrollmentStatus.ACTIVE
            await session.commit()
            await session.refresh(existing)
        return existing

    enrollment = Enrollment(student_id=current_user.user_id, course_id=course.course_id)
    session.add(enrollment)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already enrolled in this course",
        ) from None

    result = await session.execute(
        select(Enrollment)
        .options(selectinload(Enrollment.course))
        .where(Enrollment.enrollment_id == enrollment.enrollment_id),
    )
    return result.scalar_one()


@router.get("/me", response_model=list[EnrollmentResponse])
async def list_my_enrollments(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[Enrollment]:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can list enrollments",
        )

    result = await session.execute(
        select(Enrollment)
        .options(selectinload(Enrollment.course))
        .where(Enrollment.student_id == current_user.user_id)
        .order_by(Enrollment.joined_at.desc()),
    )
    return list(result.scalars().all())


@router.get("/{enrollment_id}/weak-concepts", response_model=list[WeakConceptResponse])
async def list_my_weak_concepts(
    enrollment_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[WeakConcept]:
    enrollment_result = await session.execute(
        select(Enrollment.enrollment_id).where(
            Enrollment.enrollment_id == enrollment_id,
            Enrollment.student_id == current_user.user_id,
        ),
    )
    if enrollment_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment not found",
        )

    result = await session.execute(
        select(WeakConcept)
        .where(WeakConcept.enrollment_id == enrollment_id)
        .order_by(WeakConcept.error_count.desc(), WeakConcept.last_seen_at.desc()),
    )
    return list(result.scalars().all())
