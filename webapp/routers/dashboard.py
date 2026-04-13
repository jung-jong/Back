from collections import Counter
import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import Numeric, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.analytics.keyword_stat_service import extract_question_keywords
from src.models import (
    AIIntervention,
    ChatMessage,
    ChatMessageSource,
    ChatSession,
    CourseDocument,
    CourseKeywordStat,
    CourseMessage,
    Enrollment,
    NotificationRead,
    Quest,
    QuestQuestion,
    QuestQuestionChoice,
    QuizAttempt,
    StudentQuestAnswer,
    StudentQuest,
    User,
    WeakConcept,
)
from src.models.enums import EnrollmentStatus, Rank, RecentSourceType, SenderType, StudentQuestStatus, UserRole
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


def rank_progress(total_xp: int) -> tuple[str, int, int, int]:
    if total_xp >= 1200:
        return "A", 600, 0, total_xp
    if total_xp >= 600:
        progress = total_xp - 600
        return "B", progress, max(600 - progress, 0), total_xp
    return "C", total_xp, max(600 - total_xp, 0), total_xp


def rank_enum_for_total(total_xp: int) -> Rank:
    if total_xp >= 1200:
        return Rank.A
    if total_xp >= 600:
        return Rank.B
    return Rank.C


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


GENERIC_WEAK_CONCEPTS = {
    "data",
    "file",
    "gov",
    "line",
    "데이터",
    "데이터의",
    "개방",
}


async def build_quest_weak_point_detail(
    session: AsyncSession,
    item: WeakConcept,
) -> dict | None:
    if item.recent_source_type != RecentSourceType.QUEST or not item.recent_source_ref_id:
        return None
    result = await session.execute(
        select(StudentQuestAnswer, QuestQuestion, Quest)
        .join(QuestQuestion, StudentQuestAnswer.quest_question_id == QuestQuestion.quest_question_id)
        .join(Quest, QuestQuestion.quest_id == Quest.quest_id)
        .where(
            StudentQuestAnswer.student_quest_id == item.recent_source_ref_id,
            StudentQuestAnswer.is_correct.is_(False),
        )
        .order_by(QuestQuestion.question_order),
    )
    rows = result.all()
    if not rows:
        return None
    answer, question, quest = next(
        (
            row
            for row in rows
            if item.concept_name.strip()
            and item.concept_name.strip() in row[1].question_text
        ),
        rows[0],
    )
    choices_result = await session.execute(
        select(QuestQuestionChoice)
        .where(QuestQuestionChoice.quest_question_id == question.quest_question_id)
        .order_by(QuestQuestionChoice.choice_order),
    )
    choices = choices_result.scalars().all()
    selected_choice = next(
        (choice for choice in choices if choice.quest_question_choice_id == answer.selected_choice_id),
        None,
    )
    correct_choice = next((choice for choice in choices if choice.is_correct), None)
    selected_text = selected_choice.choice_text if selected_choice else (answer.answer_text or "미응답")
    correct_text = correct_choice.choice_text if correct_choice else (question.correct_answer_text or "")
    summary_parts = [
        f"문항: {question.question_text}",
        f"내 답: {selected_text}",
    ]
    if correct_text:
        summary_parts.append(f"정답: {correct_text}")
    if question.explanation:
        summary_parts.append(f"해설: {question.explanation}")
    return {
        "keyword": question.question_text,
        "summary": "\n".join(summary_parts),
        "material": quest.title,
        "sourceType": "QUEST",
        "sourceId": str(item.recent_source_ref_id),
        "question": question.question_text,
        "selectedAnswer": selected_text,
        "correctAnswer": correct_text,
        "explanation": question.explanation or "",
    }


async def build_chat_weak_point_detail(
    session: AsyncSession,
    item: WeakConcept,
) -> dict | None:
    if item.recent_source_type != RecentSourceType.CHAT or not item.recent_source_ref_id:
        return None
    message = await session.get(ChatMessage, item.recent_source_ref_id)
    if message is None:
        return None
    source_result = await session.execute(
        select(ChatMessageSource, CourseDocument)
        .join(CourseDocument, ChatMessageSource.course_document_id == CourseDocument.course_document_id)
        .where(ChatMessageSource.chat_message_id == item.recent_source_ref_id)
        .limit(1),
    )
    source_row = source_result.first()
    material = ""
    if source_row:
        source, document = source_row
        if source.page_from:
            material = f"{document.title} {source.page_from}p"
        else:
            material = document.title
    return {
        "keyword": item.concept_name,
        "summary": f"{item.concept_name} 개념을 다시 확인해 보세요.",
        "material": material,
        "sourceType": "CHAT",
        "sourceId": str(item.recent_source_ref_id),
        "question": "",
        "selectedAnswer": "",
        "correctAnswer": "",
        "explanation": message.message_text[:500],
    }


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
    stats = result.scalars().all()
    if not stats and week:
        fallback_result = await session.execute(
            select(CourseKeywordStat)
            .where(CourseKeywordStat.course_id == course_id)
            .order_by(CourseKeywordStat.mention_count.desc())
            .limit(20),
        )
        stats = fallback_result.scalars().all()
    if stats:
        return [{"keyword": stat.keyword, "count": stat.mention_count} for stat in stats]

    recent_questions_result = await session.execute(
        select(ChatMessage.message_text)
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .join(Enrollment, ChatSession.enrollment_id == Enrollment.enrollment_id)
        .where(Enrollment.course_id == course_id, ChatMessage.sender_type == SenderType.STUDENT)
        .order_by(ChatMessage.created_at.desc())
        .limit(200),
    )
    counter: Counter[str] = Counter()
    for question_text in recent_questions_result.scalars().all():
        counter.update(extract_question_keywords(question_text, limit=5))
    return [{"keyword": keyword, "count": count} for keyword, count in counter.most_common(20)]


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
    total_xp = enrollment.current_xp or 0
    calculated_rank = rank_enum_for_total(total_xp)
    if enrollment.current_rank != calculated_rank:
        enrollment.current_rank = calculated_rank
        await session.flush()
        await session.commit()
    grade, xp, xp_to_next, total_xp = rank_progress(total_xp)
    accuracy_total = int(quest_total or 0) + int(quiz_total or 0)
    accuracy_score = int(quest_score or 0) + int(quiz_score or 0)
    return {
        "questionCount": question_count,
        "quizAccuracy": round((accuracy_score / accuracy_total * 100) if accuracy_total else 0),
        "completedQuests": completed,
        "totalQuests": total,
        "grade": grade,
        "xp": xp,
        "xpToNext": xp_to_next,
        "totalXp": total_xp,
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
        .order_by(
            case((WeakConcept.recent_source_type == RecentSourceType.QUEST, 0), else_=1),
            WeakConcept.error_count.desc(),
        )
        .limit(20),
    )
    weak_points = []
    for item in result.scalars().all():
        if (
            item.recent_source_type == RecentSourceType.CHAT
            and item.concept_name.strip().lower() in GENERIC_WEAK_CONCEPTS
        ):
            continue
        detail = await build_quest_weak_point_detail(session, item)
        if detail is None:
            detail = await build_chat_weak_point_detail(session, item)
        if detail is None:
            detail = {
                "keyword": item.concept_name,
                "summary": f"{item.concept_name} 개념을 다시 확인해 보세요.",
                "material": "",
                "sourceType": getattr(item.recent_source_type, "value", ""),
                "sourceId": str(item.recent_source_ref_id or ""),
                "question": "",
                "selectedAnswer": "",
                "correctAnswer": "",
                "explanation": "",
            }
        weak_points.append(
            {
                "id": str(item.weak_concept_id),
                "keyword": detail["keyword"],
                "wrongCount": item.error_count or 0,
                "lastWrong": getattr(item.recent_source_type, "value", ""),
                "summary": detail["summary"],
                "material": detail["material"],
                "sourceType": detail["sourceType"],
                "sourceId": detail["sourceId"],
                "question": detail["question"],
                "selectedAnswer": detail["selectedAnswer"],
                "correctAnswer": detail["correctAnswer"],
                "explanation": detail["explanation"],
            },
        )
    return weak_points


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
