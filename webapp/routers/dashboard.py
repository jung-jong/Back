import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import Numeric, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.models import (
    AIIntervention,
    ChatMessage,
    ChatSession,
    CourseKeywordStat,
    CourseMessage,
    Enrollment,
    NotificationRead,
    Quest,
    QuizAttempt,
    StudentQuest,
    User,
    WeakConcept,
)
from src.models.enums import EnrollmentStatus, SenderType, StudentQuestStatus, UserRole
from webapp.routers.courses import get_course_for_user

router = APIRouter(prefix="/courses/{course_id}", tags=["analytics"])


class AiConfigRequest(BaseModel):
    guidePrompt: str | None = ""


class QuizSubmitRequest(BaseModel):
    messageId: str
    selected: int
    isCorrect: bool


async def scalar_int(session: AsyncSession, statement) -> int:
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


async def get_active_student_enrollment(
    session: AsyncSession,
    course_id: int,
    current_user: User,
) -> Enrollment:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(status_code=403, detail="학생만 접근할 수 있습니다.")
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


def notification_key(notification_id: str) -> str:
    return notification_id.strip()[:64]


async def mark_notification_read(
    session: AsyncSession,
    user_id: int,
    course_id: int,
    key: str,
) -> None:
    await session.execute(
        text(
            "INSERT IGNORE INTO notification_reads "
            "(user_id, course_id, notification_key) "
            "VALUES (:user_id, :course_id, :notification_key)",
        ),
        {"user_id": user_id, "course_id": course_id, "notification_key": key},
    )


@router.get("/analytics")
async def get_course_analytics(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    student_count = await scalar_int(
        session,
        select(func.count(Enrollment.enrollment_id)).where(Enrollment.course_id == course_id),
    )
    weekly_question_count = await scalar_int(
        session,
        select(func.count(ChatMessage.chat_message_id))
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .join(Enrollment, ChatSession.enrollment_id == Enrollment.enrollment_id)
        .where(Enrollment.course_id == course_id, ChatMessage.sender_type == SenderType.STUDENT),
    )
    assigned_count = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id))
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(Enrollment.course_id == course_id),
    )
    completed_count = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id))
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(Enrollment.course_id == course_id, StudentQuest.status == StudentQuestStatus.GRADED),
    )
    avg_result = await session.execute(
        select(
            func.avg(
                cast(StudentQuest.score_earned, Numeric(10, 4))
                / case((StudentQuest.max_score == 0, None), else_=StudentQuest.max_score),
            ),
        )
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Enrollment.course_id == course_id,
            StudentQuest.status == StudentQuestStatus.GRADED,
            StudentQuest.score_earned.is_not(None),
        ),
    )
    rank_result = await session.execute(
        select(Enrollment.current_rank, func.count(Enrollment.enrollment_id))
        .where(Enrollment.course_id == course_id)
        .group_by(Enrollment.current_rank),
    )
    colors = {"A": "#37b1b1", "B": "#7fd9d9", "C": "#b3e5e5"}
    rank_rows = rank_result.all()
    rank_counts = {getattr(rank, "value", rank or "C"): int(count or 0) for rank, count in rank_rows}
    return {
        "studentCount": student_count,
        "weeklyQuestionCount": weekly_question_count,
        "weeklyQuestionDelta": 0,
        "avgEngagementRate": round((completed_count / assigned_count * 100) if assigned_count else 0),
        "avgQuestAnswerRate": round(float(avg_result.scalar_one_or_none() or 0) * 100),
        "gradeBreakdown": [
            {"name": "A 등급", "value": rank_counts.get("A", 0), "color": colors["A"]},
            {"name": "B 등급", "value": rank_counts.get("B", 0), "color": colors["B"]},
            {"name": "C 등급", "value": rank_counts.get("C", 0), "color": colors["C"]},
            *[{
                "name": f"{getattr(rank, 'value', rank or 'C')} 등급",
                "value": int(count or 0),
                "color": colors.get(getattr(rank, "value", str(rank)), "#7fd9d9"),
            }
            for rank, count in []],
        ],
    }


@router.get("/analytics/keywords")
async def get_course_keywords(
    course_id: int,
    week: str | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    query = select(CourseKeywordStat).where(CourseKeywordStat.course_id == course_id)
    if week:
        digits = "".join(ch for ch in week if ch.isdigit())
        if digits:
            query = query.where(CourseKeywordStat.week_number == int(digits))
    result = await session.execute(query.order_by(CourseKeywordStat.mention_count.desc()).limit(20))
    return [{"keyword": stat.keyword, "count": stat.mention_count} for stat in result.scalars().all()]


@router.get("/analytics/students")
async def get_course_students_by_grade(
    course_id: int,
    grade: str | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    query = (
        select(Enrollment, User)
        .join(User, Enrollment.student_id == User.user_id)
        .where(Enrollment.course_id == course_id)
        .order_by(User.name)
    )
    if grade:
        query = query.where(Enrollment.current_rank == grade.upper())
    result = await session.execute(query)
    return [
        {
            "id": str(user.user_id),
            "name": user.name,
            "email": user.email,
            "grade": getattr(enrollment.current_rank, "value", "C"),
            "xp": enrollment.current_xp or 0,
            "lastActiveAt": enrollment.last_active_at.isoformat() if enrollment.last_active_at else None,
        }
        for enrollment, user in result.all()
    ]


@router.get("/ai-proposals")
async def get_ai_proposals(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await get_course_for_user(session, course_id, current_user, instructor_only=True)
    result = await session.execute(
        select(AIIntervention)
        .where(AIIntervention.course_id == course_id)
        .order_by(AIIntervention.created_at.desc())
        .limit(20),
    )
    proposals = []
    for item in result.scalars().all():
        detail = {}
        if item.action_detail:
            try:
                detail = json.loads(item.action_detail)
            except json.JSONDecodeError:
                detail = {}
        content = detail.get("body") or detail.get("description") or item.target_summary or ""
        if isinstance(content, list):
            content = "\n".join(str(line) for line in content)
        proposals.append(
            {
                "id": str(item.ai_intervention_id),
                "title": item.title,
                "targetGroup": item.target_summary or "",
                "evidence": item.target_summary or "",
                "content": str(content),
            },
        )
    return proposals


@router.get("/ai-config")
async def get_ai_config(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    course = await get_course_for_user(session, course_id, current_user, instructor_only=True)
    return {"guidePrompt": course.system_prompt or ""}


@router.put("/ai-config")
async def put_ai_config(
    course_id: int,
    payload: AiConfigRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    course = await get_course_for_user(session, course_id, current_user, instructor_only=True)
    course.system_prompt = payload.guidePrompt or ""
    await session.commit()
    return {"guidePrompt": course.system_prompt}


@router.get("/me/stats")
async def get_my_course_stats(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    question_count = await scalar_int(
        session,
        select(func.count(ChatMessage.chat_message_id))
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .where(ChatSession.enrollment_id == enrollment.enrollment_id, ChatMessage.sender_type == SenderType.STUDENT),
    )
    completed = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id)).where(
            StudentQuest.enrollment_id == enrollment.enrollment_id,
            StudentQuest.status == StudentQuestStatus.GRADED,
        ),
    )
    total = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id)).where(StudentQuest.enrollment_id == enrollment.enrollment_id),
    )
    quest_score_result = await session.execute(
        select(
            func.coalesce(func.sum(StudentQuest.score_earned), 0),
            func.coalesce(func.sum(StudentQuest.max_score), 0),
        ).where(
            StudentQuest.enrollment_id == enrollment.enrollment_id,
            StudentQuest.status == StudentQuestStatus.GRADED,
            StudentQuest.score_earned.is_not(None),
        ),
    )
    quest_score, quest_total = quest_score_result.one()
    quiz_result = await session.execute(
        select(
            func.coalesce(func.sum(case((QuizAttempt.is_correct.is_(True), 1), else_=0)), 0),
            func.count(QuizAttempt.quiz_attempt_id),
        ).where(QuizAttempt.enrollment_id == enrollment.enrollment_id),
    )
    quiz_score, quiz_total = quiz_result.one()
    xp = enrollment.current_xp or 0
    accuracy_total = int(quest_total or 0) + int(quiz_total or 0)
    accuracy_score = int(quest_score or 0) + int(quiz_score or 0)
    return {
        "questionCount": question_count,
        "quizAccuracy": round((accuracy_score / accuracy_total * 100) if accuracy_total else 0),
        "completedQuests": completed,
        "totalQuests": total,
        "grade": getattr(enrollment.current_rank, "value", "C"),
        "xp": xp,
        "xpToNext": max(600 - xp, 0),
    }


@router.get("/me/weak-points")
async def get_my_weak_points(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    result = await session.execute(
        select(WeakConcept)
        .where(WeakConcept.enrollment_id == enrollment.enrollment_id)
        .order_by(WeakConcept.error_count.desc())
        .limit(20),
    )
    return [
        {
            "id": str(item.weak_concept_id),
            "keyword": item.concept_name,
            "wrongCount": item.error_count or 0,
            "lastWrong": getattr(item.recent_source_type, "value", ""),
            "summary": f"{item.concept_name} 개념을 다시 확인해 보세요.",
            "material": "",
        }
        for item in result.scalars().all()
    ]


@router.get("/notifications")
async def get_notifications(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    reads_result = await session.execute(
        select(NotificationRead.notification_key).where(
            NotificationRead.course_id == course_id,
            NotificationRead.user_id == current_user.user_id,
        ),
    )
    read_keys = set(reads_result.scalars().all())
    messages_result = await session.execute(
        select(CourseMessage, User)
        .join(User, CourseMessage.sender_user_id == User.user_id)
        .where(CourseMessage.course_id == course_id)
        .order_by(CourseMessage.created_at.desc())
        .limit(20),
    )
    notifications = [
        {
            "id": f"m-{message.course_message_id}",
            "type": "message",
            "title": message.title,
            "content": message.body,
            "from": f"{sender.name} 교수님",
            "time": message.created_at.date().isoformat() if message.created_at else "",
            "read": f"m-{message.course_message_id}" in read_keys,
        }
        for message, sender in messages_result.all()
    ]
    quest_result = await session.execute(
        select(StudentQuest, Quest)
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .where(StudentQuest.enrollment_id == enrollment.enrollment_id)
        .order_by(StudentQuest.assigned_at.desc())
        .limit(20),
    )
    notifications.extend(
        {
            "id": f"q-{student_quest.student_quest_id}",
            "type": "quest",
            "title": quest.title,
            "content": quest.description or "새 퀘스트가 도착했습니다.",
            "from": "Custom-TA",
            "time": student_quest.assigned_at.date().isoformat() if student_quest.assigned_at else "",
            "read": f"q-{student_quest.student_quest_id}" in read_keys
            or student_quest.status != StudentQuestStatus.ASSIGNED,
        }
        for student_quest, quest in quest_result.all()
    )
    return notifications


@router.patch("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_notification(
    course_id: int,
    notification_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    key = notification_key(notification_id)
    if key.startswith("m-"):
        try:
            message_id = int(key.removeprefix("m-"))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.") from exc
        exists = await scalar_int(
            session,
            select(func.count(CourseMessage.course_message_id)).where(
                CourseMessage.course_message_id == message_id,
                CourseMessage.course_id == course_id,
            ),
        )
    elif key.startswith("q-"):
        try:
            student_quest_id = int(key.removeprefix("q-"))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.") from exc
        exists = await scalar_int(
            session,
            select(func.count(StudentQuest.student_quest_id)).where(
                StudentQuest.student_quest_id == student_quest_id,
                StudentQuest.enrollment_id == enrollment.enrollment_id,
            ),
        )
    else:
        exists = 0
    if not exists:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    await mark_notification_read(session, current_user.user_id, course_id, key)
    await session.commit()
    return Response(status_code=204)


@router.patch("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all_notifications(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    message_ids_result = await session.execute(
        select(CourseMessage.course_message_id).where(CourseMessage.course_id == course_id),
    )
    quest_ids_result = await session.execute(
        select(StudentQuest.student_quest_id).where(StudentQuest.enrollment_id == enrollment.enrollment_id),
    )
    keys = [
        *(f"m-{message_id}" for message_id in message_ids_result.scalars().all()),
        *(f"q-{student_quest_id}" for student_quest_id in quest_ids_result.scalars().all()),
    ]
    for key in keys:
        await mark_notification_read(session, current_user.user_id, course_id, key)
    await session.commit()
    return Response(status_code=204)


@router.post("/quiz/submit")
async def submit_chat_quiz(
    course_id: int,
    payload: QuizSubmitRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    enrollment = await get_active_student_enrollment(session, course_id, current_user)
    message_key = payload.messageId.strip()[:64]
    if not message_key:
        raise HTTPException(status_code=422, detail="messageId가 필요합니다.")
    await session.execute(
        text(
            "INSERT INTO quiz_attempts "
            "(course_id, enrollment_id, message_key, selected_index, is_correct) "
            "VALUES (:course_id, :enrollment_id, :message_key, :selected_index, :is_correct) "
            "ON DUPLICATE KEY UPDATE "
            "selected_index=VALUES(selected_index), "
            "is_correct=VALUES(is_correct), "
            "created_at=CURRENT_TIMESTAMP",
        ),
        {
            "course_id": course_id,
            "enrollment_id": enrollment.enrollment_id,
            "message_key": message_key,
            "selected_index": payload.selected,
            "is_correct": payload.isCorrect,
        },
    )
    await session.commit()
    return {"message": "퀴즈 결과가 저장되었습니다."}
