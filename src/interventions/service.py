from datetime import date
from decimal import Decimal
import json
from collections import Counter

from sqlalchemy import Numeric, case, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AIIntervention,
    ChatMessage,
    ChatSession,
    Course,
    CourseDocument,
    CourseKeywordStat,
    Enrollment,
    Quest,
    StudentQuest,
    StudentQuestAnswer,
    WeakConcept,
)
from src.models.enums import (
    EmbeddingStatus,
    EnrollmentStatus,
    InterventionStatus,
    InterventionType,
    MessageType,
    SenderType,
    StudentQuestStatus,
)
from src.ai.service import AIService
from src.analytics.keyword_stat_service import extract_question_keywords

async def generate_weekly_interventions(
    session: AsyncSession,
    course: Course,
    week_start_date: date,
    week_end_date: date,
) -> list[AIIntervention]:
    snapshot = await build_course_week_snapshot(
        session,
        course.course_id,
        week_start_date,
        week_end_date,
    )
    suggestions = await suggest_interventions_with_llm(course, snapshot)
    existing_result = await session.execute(
        select(AIIntervention.intervention_type).where(
            AIIntervention.course_id == course.course_id,
            AIIntervention.week_start_date == week_start_date,
            AIIntervention.week_end_date == week_end_date,
            AIIntervention.status != InterventionStatus.DISMISSED,
        ),
    )
    existing_types = {item[0] for item in existing_result.all()}

    interventions: list[AIIntervention] = []
    seen_types = set(existing_types)
    for suggestion in suggestions:
        intervention_type = suggestion["intervention_type"]
        if intervention_type in seen_types:
            continue
        seen_types.add(intervention_type)
        intervention = AIIntervention(
            course_id=course.course_id,
            week_start_date=week_start_date,
            week_end_date=week_end_date,
            intervention_type=intervention_type,
            title=suggestion["title"][:255],
            target_summary=suggestion.get("target_summary", "")[:255],
            evidence=json.dumps(snapshot, ensure_ascii=False),
            action_detail=json.dumps(suggestion.get("action_detail", {}), ensure_ascii=False),
            status=InterventionStatus.PENDING,
        )
        session.add(intervention)
        interventions.append(intervention)

    if not interventions and not existing_types:
        fallback = normalize_suggestion(build_fallback_suggestion(snapshot), snapshot)
        intervention = AIIntervention(
            course_id=course.course_id,
            week_start_date=week_start_date,
            week_end_date=week_end_date,
            intervention_type=fallback["intervention_type"],
            title=fallback["title"][:255],
            target_summary=fallback.get("target_summary", "")[:255],
            evidence=json.dumps(snapshot, ensure_ascii=False),
            action_detail=json.dumps(fallback.get("action_detail", {}), ensure_ascii=False),
            status=InterventionStatus.PENDING,
        )
        session.add(intervention)
        interventions.append(intervention)

    await session.commit()
    for intervention in interventions:
        await session.refresh(intervention)
    return interventions


async def build_course_week_snapshot(
    session: AsyncSession,
    course_id: int,
    week_start_date: date,
    week_end_date: date,
) -> dict:
    total_students = await scalar_int(
        session,
        select(func.count(Enrollment.enrollment_id)).where(
            Enrollment.course_id == course_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        ),
    )
    question_metrics = await build_question_metrics(
        session,
        course_id,
        total_students,
        week_start_date,
        week_end_date,
    )
    quiz_metrics = await build_quiz_metrics(
        session,
        course_id,
        total_students,
        week_start_date,
        week_end_date,
    )
    weak_concepts = await build_weak_concept_metrics(session, course_id)
    material_metrics = await build_material_metrics(session, course_id)

    category_signals = score_intervention_categories(
        total_students=total_students,
        question_metrics=question_metrics,
        quiz_metrics=quiz_metrics,
        weak_concepts=weak_concepts,
        material_metrics=material_metrics,
    )

    return {
        "course_id": course_id,
        "week_start_date": week_start_date.isoformat(),
        "week_end_date": week_end_date.isoformat(),
        "total_active_students": total_students,
        "question_metrics": question_metrics,
        "quiz_metrics": quiz_metrics,
        "weak_concept_metrics": weak_concepts,
        "material_metrics": material_metrics,
        "category_signals": category_signals,
        "decision_categories": [
            {
                "intervention_type": "SEND_QUEST",
                "meaning": "Create a targeted review quiz when wrong-answer rate or weak concept concentration is high.",
            },
            {
                "intervention_type": "SEND_MESSAGE",
                "meaning": "Send motivation or guidance when question rate or quiz participation is low.",
            },
            {
                "intervention_type": "UPLOAD_MATERIAL",
                "meaning": "Suggest supplemental material when repeated questions indicate missing or unclear lecture material.",
            },
        ],
    }


async def build_question_metrics(
    session: AsyncSession,
    course_id: int,
    total_students: int,
    week_start_date: date,
    week_end_date: date,
) -> dict:
    question_count = await scalar_int(
        session,
        select(func.count(ChatMessage.chat_message_id))
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .join(Enrollment, ChatSession.enrollment_id == Enrollment.enrollment_id)
        .where(
            Enrollment.course_id == course_id,
            ChatMessage.sender_type == SenderType.STUDENT,
            ChatMessage.message_type == MessageType.QUESTION,
            func.date(ChatMessage.created_at) >= week_start_date,
            func.date(ChatMessage.created_at) <= week_end_date,
        ),
    )
    students_who_asked = await scalar_int(
        session,
        select(func.count(distinct(Enrollment.enrollment_id)))
        .join(ChatSession, ChatSession.enrollment_id == Enrollment.enrollment_id)
        .join(ChatMessage, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .where(
            Enrollment.course_id == course_id,
            ChatMessage.sender_type == SenderType.STUDENT,
            ChatMessage.message_type == MessageType.QUESTION,
            func.date(ChatMessage.created_at) >= week_start_date,
            func.date(ChatMessage.created_at) <= week_end_date,
        ),
    )
    recent_question_result = await session.execute(
        select(ChatMessage.message_text)
        .join(ChatSession, ChatMessage.chat_session_id == ChatSession.chat_session_id)
        .join(Enrollment, ChatSession.enrollment_id == Enrollment.enrollment_id)
        .where(
            Enrollment.course_id == course_id,
            ChatMessage.sender_type == SenderType.STUDENT,
            ChatMessage.message_type == MessageType.QUESTION,
            func.date(ChatMessage.created_at) >= week_start_date,
            func.date(ChatMessage.created_at) <= week_end_date,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(30),
    )
    recent_questions = [row[0] for row in recent_question_result.all()]
    keyword_counter: Counter[str] = Counter()
    for question in recent_questions:
        keyword_counter.update(extract_question_keywords(question, limit=8))

    stored_keyword_result = await session.execute(
        select(
            CourseKeywordStat.week_number,
            CourseKeywordStat.keyword,
            CourseKeywordStat.mention_count,
        )
        .where(
            CourseKeywordStat.course_id == course_id,
            func.date(CourseKeywordStat.calculated_at) >= week_start_date,
            func.date(CourseKeywordStat.calculated_at) <= week_end_date,
        )
        .order_by(CourseKeywordStat.mention_count.desc())
        .limit(15),
    )
    stored_keywords = [
        {
            "week_number": int(week_number),
            "keyword": keyword,
            "mention_count": int(mention_count or 0),
        }
        for week_number, keyword, mention_count in stored_keyword_result.all()
    ]

    return {
        "question_count": question_count,
        "students_who_asked": students_who_asked,
        "question_rate": safe_ratio(students_who_asked, total_students),
        "questions_per_active_student": safe_ratio(question_count, total_students),
        "students_without_questions": max(total_students - students_who_asked, 0),
        "top_keywords_from_chat": [
            {"keyword": keyword, "mention_count": count}
            for keyword, count in keyword_counter.most_common(10)
        ],
        "stored_weekly_keyword_stats": stored_keywords,
        "recent_student_questions": recent_questions[:10],
    }


async def build_quiz_metrics(
    session: AsyncSession,
    course_id: int,
    total_students: int,
    week_start_date: date,
    week_end_date: date,
) -> dict:
    assigned_count = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id))
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            func.date(StudentQuest.assigned_at) >= week_start_date,
            func.date(StudentQuest.assigned_at) <= week_end_date,
        ),
    )
    submitted_or_graded_count = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id))
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuest.status.in_(
                [
                    StudentQuestStatus.SUBMITTED,
                    StudentQuestStatus.GRADED,
                ],
            ),
            func.date(StudentQuest.assigned_at) >= week_start_date,
            func.date(StudentQuest.assigned_at) <= week_end_date,
        ),
    )
    graded_count = await scalar_int(
        session,
        select(func.count(StudentQuest.student_quest_id))
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuest.status == StudentQuestStatus.GRADED,
            func.date(StudentQuest.assigned_at) >= week_start_date,
            func.date(StudentQuest.assigned_at) <= week_end_date,
        ),
    )
    participants = await scalar_int(
        session,
        select(func.count(distinct(StudentQuest.enrollment_id)))
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuest.status.in_(
                [
                    StudentQuestStatus.SUBMITTED,
                    StudentQuestStatus.GRADED,
                ],
            ),
            func.date(StudentQuest.assigned_at) >= week_start_date,
            func.date(StudentQuest.assigned_at) <= week_end_date,
        ),
    )
    total_answers = await scalar_int(
        session,
        select(func.count(StudentQuestAnswer.student_quest_answer_id))
        .join(StudentQuest, StudentQuestAnswer.student_quest_id == StudentQuest.student_quest_id)
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            func.date(StudentQuestAnswer.answered_at) >= week_start_date,
            func.date(StudentQuestAnswer.answered_at) <= week_end_date,
        ),
    )
    wrong_answers = await scalar_int(
        session,
        select(func.count(StudentQuestAnswer.student_quest_answer_id))
        .join(StudentQuest, StudentQuestAnswer.student_quest_id == StudentQuest.student_quest_id)
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuestAnswer.is_correct.is_(False),
            func.date(StudentQuestAnswer.answered_at) >= week_start_date,
            func.date(StudentQuestAnswer.answered_at) <= week_end_date,
        ),
    )
    avg_score_result = await session.execute(
        select(
            func.avg(
                cast(StudentQuest.score_earned, Numeric(10, 4))
                / case((StudentQuest.max_score == 0, None), else_=StudentQuest.max_score),
            ),
        )
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuest.status == StudentQuestStatus.GRADED,
            StudentQuest.score_earned.is_not(None),
            func.date(StudentQuest.assigned_at) >= week_start_date,
            func.date(StudentQuest.assigned_at) <= week_end_date,
        ),
    )
    average_score_ratio = decimal_to_float(avg_score_result.scalar_one_or_none())

    wrong_question_result = await session.execute(
        select(
            StudentQuestAnswer.quest_question_id,
            func.count(StudentQuestAnswer.student_quest_answer_id).label("wrong_count"),
        )
        .join(StudentQuest, StudentQuestAnswer.student_quest_id == StudentQuest.student_quest_id)
        .join(Quest, StudentQuest.quest_id == Quest.quest_id)
        .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
        .where(
            Quest.course_id == course_id,
            Enrollment.course_id == course_id,
            StudentQuestAnswer.is_correct.is_(False),
            func.date(StudentQuestAnswer.answered_at) >= week_start_date,
            func.date(StudentQuestAnswer.answered_at) <= week_end_date,
        )
        .group_by(StudentQuestAnswer.quest_question_id)
        .order_by(func.count(StudentQuestAnswer.student_quest_answer_id).desc())
        .limit(5),
    )

    return {
        "assigned_student_quest_count": assigned_count,
        "submitted_or_graded_student_quest_count": submitted_or_graded_count,
        "graded_student_quest_count": graded_count,
        "quiz_participant_count": participants,
        "quiz_participation_rate_by_assignment": safe_ratio(
            submitted_or_graded_count,
            assigned_count,
        ),
        "quiz_participation_rate_by_student": safe_ratio(participants, total_students),
        "non_participant_student_estimate": max(total_students - participants, 0),
        "total_answer_count": total_answers,
        "wrong_answer_count": wrong_answers,
        "wrong_answer_rate": safe_ratio(wrong_answers, total_answers),
        "average_score_ratio": average_score_ratio,
        "top_wrong_question_ids": [
            {"quest_question_id": int(question_id), "wrong_count": int(wrong_count or 0)}
            for question_id, wrong_count in wrong_question_result.all()
        ],
    }


async def build_weak_concept_metrics(
    session: AsyncSession,
    course_id: int,
) -> dict:
    result = await session.execute(
        select(
            WeakConcept.concept_name,
            func.coalesce(func.sum(WeakConcept.error_count), 0),
            func.count(distinct(WeakConcept.enrollment_id)),
        )
        .join(Enrollment, WeakConcept.enrollment_id == Enrollment.enrollment_id)
        .where(Enrollment.course_id == course_id)
        .group_by(WeakConcept.concept_name)
        .order_by(func.coalesce(func.sum(WeakConcept.error_count), 0).desc())
        .limit(10),
    )
    top_weak_concepts = [
        {
            "concept": concept_name,
            "error_count": int(error_count or 0),
            "affected_student_count": int(affected_student_count or 0),
        }
        for concept_name, error_count, affected_student_count in result.all()
    ]
    return {
        "top_weak_concepts": top_weak_concepts,
        "has_concentrated_weakness": bool(
            top_weak_concepts and top_weak_concepts[0]["error_count"] >= 3
        ),
    }


async def build_material_metrics(session: AsyncSession, course_id: int) -> dict:
    total_documents = await scalar_int(
        session,
        select(func.count(CourseDocument.course_document_id)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.deleted_at.is_(None),
        ),
    )
    completed_documents = await scalar_int(
        session,
        select(func.count(CourseDocument.course_document_id)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.deleted_at.is_(None),
            CourseDocument.embedding_status == EmbeddingStatus.COMPLETED,
        ),
    )
    failed_or_pending_documents = await scalar_int(
        session,
        select(func.count(CourseDocument.course_document_id)).where(
            CourseDocument.course_id == course_id,
            CourseDocument.deleted_at.is_(None),
            CourseDocument.embedding_status.in_(
                [
                    EmbeddingStatus.PENDING,
                    EmbeddingStatus.PROCESSING,
                    EmbeddingStatus.FAILED,
                ],
            ),
        ),
    )
    return {
        "total_documents": total_documents,
        "completed_embedding_documents": completed_documents,
        "pending_or_failed_embedding_documents": failed_or_pending_documents,
        "material_ready_rate": safe_ratio(completed_documents, total_documents),
    }


def score_intervention_categories(
    total_students: int,
    question_metrics: dict,
    quiz_metrics: dict,
    weak_concepts: dict,
    material_metrics: dict,
) -> dict:
    question_rate = question_metrics["question_rate"]
    questions_per_student = question_metrics["questions_per_active_student"]
    quiz_participation = quiz_metrics["quiz_participation_rate_by_student"]
    assignment_participation = quiz_metrics["quiz_participation_rate_by_assignment"]
    wrong_answer_rate = quiz_metrics["wrong_answer_rate"]
    material_ready_rate = material_metrics["material_ready_rate"]
    weak_count = len(weak_concepts["top_weak_concepts"])

    send_quest_score = (
        (wrong_answer_rate * 45)
        + (weak_count * 5)
        + ((1 - (quiz_metrics["average_score_ratio"] or 1)) * 25)
    )
    send_message_score = (
        ((1 - question_rate) * 35)
        + ((1 - quiz_participation) * 35)
        + ((1 - assignment_participation) * 20)
    )
    upload_material_score = (
        min(questions_per_student, 3) * 15
        + ((1 - material_ready_rate) * 25)
        + (10 if question_metrics["top_keywords_from_chat"] else 0)
    )

    if total_students == 0:
        send_message_score += 20

    return {
        "SEND_QUEST": round(send_quest_score, 2),
        "SEND_MESSAGE": round(send_message_score, 2),
        "UPLOAD_MATERIAL": round(upload_material_score, 2),
        "largest_signal": max(
            {
                "SEND_QUEST": send_quest_score,
                "SEND_MESSAGE": send_message_score,
                "UPLOAD_MATERIAL": upload_material_score,
            },
            key=lambda key: {
                "SEND_QUEST": send_quest_score,
                "SEND_MESSAGE": send_message_score,
                "UPLOAD_MATERIAL": upload_material_score,
            }[key],
        ),
    }


async def suggest_interventions_with_llm(course: Course, snapshot: dict) -> list[dict]:
    system_prompt = (
        "You are an education analytics LLM for an instructor dashboard. "
        "You are NOT the RAG chatbot and must not answer student questions. "
        "Use only the weekly analytics JSON to judge every action category independently. "
        "Return only valid JSON without markdown. "
        "Allowed intervention_type values are SEND_QUEST, SEND_MESSAGE, UPLOAD_MATERIAL. "
        "Do not choose just one. Return an actions array containing every applicable intervention. "
        "If all three are justified, return all three. If only one is justified, return one. "
        "Include no action only when there is truly no meaningful signal. "
        "SEND_QUEST is applicable when quiz wrong-answer rate, low average score, or weak concepts "
        "indicate targeted practice is needed. SEND_MESSAGE is applicable when question rate or "
        "quiz participation is low and encouragement/guidance is needed. UPLOAD_MATERIAL is applicable "
        "when repeated chat keywords/questions suggest missing or unclear materials. "
        "Each action must include: intervention_type, title, target_summary, data_used, reasoning, action_detail. "
        "For SEND_QUEST, action_detail must include title, description, xp_reward, target_rule_type, "
        "and 2-4 multiple-choice questions. Each question must include question_text, explanation, "
        "and choices with choice_text and is_correct. "
        "For SEND_MESSAGE, action_detail must include title, body, target_rule_type. "
        "For UPLOAD_MATERIAL, action_detail must include title, material_outline, recommended_keywords, target_rule_type."
    )
    user_prompt = (
        f"Course: {course.course_name}\n"
        f"Description: {course.course_description or ''}\n"
        f"Weekly analytics JSON:\n{json.dumps(snapshot, ensure_ascii=False)}"
    )
    try:
        llm_response = await AIService().generate_json_response(system_prompt, user_prompt)
    except Exception:
        llm_response = {"actions": build_fallback_suggestions(snapshot)}
    return normalize_suggestions(llm_response, snapshot)


def normalize_suggestions(llm_response: object, snapshot: dict) -> list[dict]:
    if isinstance(llm_response, list):
        raw_actions = llm_response
    elif isinstance(llm_response, dict):
        raw_actions = llm_response.get("actions") or llm_response.get("interventions")
        if raw_actions is None and llm_response.get("intervention_type"):
            raw_actions = [llm_response]
    else:
        raw_actions = None

    if not isinstance(raw_actions, list) or not raw_actions:
        raw_actions = build_fallback_suggestions(snapshot)

    normalized: list[dict] = []
    seen_types: set[InterventionType] = set()
    for action in raw_actions:
        if not isinstance(action, dict):
            continue
        normalized_action = normalize_suggestion(action, snapshot)
        intervention_type = normalized_action["intervention_type"]
        if intervention_type in seen_types:
            continue
        seen_types.add(intervention_type)
        normalized.append(normalized_action)

    return normalized or [
        normalize_suggestion(action, snapshot)
        for action in build_fallback_suggestions(snapshot)
    ]


def normalize_suggestion(suggestion: dict, snapshot: dict) -> dict:
    intervention_type = str(
        suggestion.get("intervention_type")
        or snapshot.get("category_signals", {}).get("largest_signal")
        or "SEND_MESSAGE",
    ).upper()
    if intervention_type not in {"SEND_QUEST", "SEND_MESSAGE", "UPLOAD_MATERIAL"}:
        intervention_type = "SEND_MESSAGE"

    action_detail = suggestion.get("action_detail")
    if not isinstance(action_detail, dict):
        action_detail = {}
    action_detail.setdefault("analytics_summary", build_compact_summary(snapshot))
    action_detail.setdefault("target_rule_type", "ALL")

    if intervention_type == "SEND_QUEST":
        action_detail = ensure_quest_action_detail(action_detail, snapshot)
    elif intervention_type == "SEND_MESSAGE":
        action_detail = ensure_message_action_detail(action_detail, snapshot)
    else:
        action_detail = ensure_material_action_detail(action_detail, snapshot)

    return {
        "intervention_type": InterventionType(intervention_type),
        "title": str(suggestion.get("title") or default_title(intervention_type, snapshot)),
        "target_summary": str(
            suggestion.get("target_summary")
            or build_target_summary(intervention_type, snapshot),
        ),
        "action_detail": action_detail,
    }


def build_fallback_suggestion(snapshot: dict) -> dict:
    return build_fallback_suggestions(snapshot)[0]


def build_fallback_suggestions(snapshot: dict) -> list[dict]:
    suggestions: list[dict] = []
    if is_quest_applicable(snapshot):
        suggestions.append(
            {
                "intervention_type": "SEND_QUEST",
                "title": "Weak concept review quest",
                "target_summary": build_target_summary("SEND_QUEST", snapshot),
                "action_detail": build_fallback_quest_detail(snapshot),
            },
        )
    if is_message_applicable(snapshot):
        suggestions.append(
            {
                "intervention_type": "SEND_MESSAGE",
                "title": "Learning participation message",
                "target_summary": build_target_summary("SEND_MESSAGE", snapshot),
                "action_detail": build_fallback_message_detail(snapshot),
            },
        )
    if is_material_applicable(snapshot):
        suggestions.append(
            {
                "intervention_type": "UPLOAD_MATERIAL",
                "title": "Supplemental material recommendation",
                "target_summary": build_target_summary("UPLOAD_MATERIAL", snapshot),
                "action_detail": build_fallback_material_detail(snapshot),
            },
        )

    if suggestions:
        return suggestions

    largest_signal = snapshot.get("category_signals", {}).get("largest_signal", "SEND_MESSAGE")
    return [
        {
            "intervention_type": largest_signal,
            "title": default_title(largest_signal, snapshot),
            "target_summary": build_target_summary(largest_signal, snapshot),
            "action_detail": {
                "SEND_QUEST": build_fallback_quest_detail,
                "SEND_MESSAGE": build_fallback_message_detail,
                "UPLOAD_MATERIAL": build_fallback_material_detail,
            }[largest_signal](snapshot),
        },
    ]


def is_quest_applicable(snapshot: dict) -> bool:
    quiz_metrics = snapshot["quiz_metrics"]
    weak_metrics = snapshot["weak_concept_metrics"]
    return (
        quiz_metrics["wrong_answer_rate"] >= 0.35
        or (quiz_metrics["average_score_ratio"] is not None and quiz_metrics["average_score_ratio"] <= 0.7)
        or weak_metrics["has_concentrated_weakness"]
        or len(weak_metrics["top_weak_concepts"]) >= 3
    )


def is_message_applicable(snapshot: dict) -> bool:
    question_metrics = snapshot["question_metrics"]
    quiz_metrics = snapshot["quiz_metrics"]
    total_students = snapshot["total_active_students"]
    return (
        total_students == 0
        or question_metrics["question_rate"] <= 0.45
        or quiz_metrics["quiz_participation_rate_by_student"] <= 0.6
        or quiz_metrics["quiz_participation_rate_by_assignment"] <= 0.65
    )


def is_material_applicable(snapshot: dict) -> bool:
    question_metrics = snapshot["question_metrics"]
    material_metrics = snapshot["material_metrics"]
    keywords = extract_keywords_from_snapshot(snapshot)
    return (
        bool(keywords)
        and (
            question_metrics["questions_per_active_student"] >= 0.4
            or material_metrics["material_ready_rate"] < 0.8
            or len(keywords) >= 3
        )
    )


def ensure_quest_action_detail(action_detail: dict, snapshot: dict) -> dict:
    fallback = build_fallback_quest_detail(snapshot)
    action_detail.setdefault("title", fallback["title"])
    action_detail.setdefault("description", fallback["description"])
    action_detail.setdefault("xp_reward", fallback["xp_reward"])
    questions = action_detail.get("questions")
    if not isinstance(questions, list) or not questions:
        action_detail["questions"] = fallback["questions"]
    return action_detail


def ensure_message_action_detail(action_detail: dict, snapshot: dict) -> dict:
    fallback = build_fallback_message_detail(snapshot)
    if not action_detail.get("title"):
        action_detail["title"] = fallback["title"]
    if not action_detail.get("body"):
        action_detail["body"] = fallback["body"]
    return action_detail


def ensure_material_action_detail(action_detail: dict, snapshot: dict) -> dict:
    fallback = build_fallback_material_detail(snapshot)
    action_detail.setdefault("title", fallback["title"])
    action_detail.setdefault("material_outline", fallback["material_outline"])
    action_detail.setdefault("recommended_keywords", fallback["recommended_keywords"])
    return action_detail


def build_fallback_quest_detail(snapshot: dict) -> dict:
    concept = primary_concept(snapshot)
    return {
        "title": f"{concept} focused review",
        "description": "A short review quest generated from this week's questions and quiz mistakes.",
        "xp_reward": 50,
        "target_rule_type": "ALL",
        "questions": [
            {
                "question_text": f"What is the most important point when explaining {concept}?",
                "question_type": "MULTIPLE_CHOICE",
                "points": 1,
                "explanation": "This checks the core definition of a concept that appeared in questions or mistakes.",
                "choices": [
                    {"choice_text": "Connect the definition with when and how it is applied.", "is_correct": True},
                    {"choice_text": "Memorize only the term name.", "is_correct": False},
                    {"choice_text": "Ignore source context and examples.", "is_correct": False},
                ],
            },
            {
                "question_text": f"What should students do when questions about {concept} repeat?",
                "question_type": "MULTIPLE_CHOICE",
                "points": 1,
                "explanation": "Weak concepts improve through targeted review and short checks.",
                "choices": [
                    {"choice_text": "Revisit the related material and solve a quick check question.", "is_correct": True},
                    {"choice_text": "Skip the missed question and move on.", "is_correct": False},
                    {"choice_text": "Do not use question history for review.", "is_correct": False},
                ],
            },
        ],
    }


def build_fallback_message_detail(snapshot: dict) -> dict:
    question_rate = snapshot["question_metrics"]["question_rate"]
    quiz_rate = snapshot["quiz_metrics"]["quiz_participation_rate_by_student"]
    return {
        "title": "Weekly learning check",
        "body": (
            f"This week's question participation is {question_rate:.0%}, "
            f"and quiz participation is {quiz_rate:.0%}. "
            "One short question or one review quiz helps the AI identify weak concepts more accurately."
        ),
        "target_rule_type": "ALL",
    }


def build_fallback_material_detail(snapshot: dict) -> dict:
    keywords = extract_keywords_from_snapshot(snapshot)
    return {
        "title": "Upload supplemental material for repeated question keywords",
        "material_outline": [
            "Core definitions for repeated question keywords",
            "Examples and counterexamples students confused",
            "Short check questions with explanations",
        ],
        "recommended_keywords": keywords,
        "target_rule_type": "ALL",
    }


def build_compact_summary(snapshot: dict) -> dict:
    return {
        "question_rate": snapshot["question_metrics"]["question_rate"],
        "quiz_participation_rate": snapshot["quiz_metrics"]["quiz_participation_rate_by_student"],
        "wrong_answer_rate": snapshot["quiz_metrics"]["wrong_answer_rate"],
        "top_keywords": extract_keywords_from_snapshot(snapshot),
        "top_weak_concepts": snapshot["weak_concept_metrics"]["top_weak_concepts"][:5],
        "category_scores": snapshot["category_signals"],
    }


def build_target_summary(intervention_type: str, snapshot: dict) -> str:
    if intervention_type == "SEND_QUEST":
        return (
            f"Wrong-answer rate {snapshot['quiz_metrics']['wrong_answer_rate']:.0%}, "
            f"primary weak concept: {primary_concept(snapshot)}"
        )
    if intervention_type == "UPLOAD_MATERIAL":
        keywords = ", ".join(extract_keywords_from_snapshot(snapshot)[:3]) or "repeated questions"
        return f"Supplemental material needed for: {keywords}"
    return (
        f"Question participation {snapshot['question_metrics']['question_rate']:.0%}, "
        f"quiz participation {snapshot['quiz_metrics']['quiz_participation_rate_by_student']:.0%}"
    )


def default_title(intervention_type: str, snapshot: dict) -> str:
    if intervention_type == "SEND_QUEST":
        return f"{primary_concept(snapshot)} review quest"
    if intervention_type == "UPLOAD_MATERIAL":
        return "Supplemental material recommendation"
    return "Learning participation message"


def primary_concept(snapshot: dict) -> str:
    weak = snapshot["weak_concept_metrics"]["top_weak_concepts"]
    if weak:
        return str(weak[0]["concept"])
    keywords = extract_keywords_from_snapshot(snapshot)
    if keywords:
        return keywords[0]
    return "core concept"


def extract_keywords_from_snapshot(snapshot: dict) -> list[str]:
    keywords = [
        item["keyword"]
        for item in snapshot["question_metrics"].get("top_keywords_from_chat", [])
        if item.get("keyword")
    ]
    if keywords:
        return keywords[:5]
    return [
        item["keyword"]
        for item in snapshot["question_metrics"].get("stored_weekly_keyword_stats", [])
        if item.get("keyword")
    ][:5]


async def scalar_int(session: AsyncSession, statement) -> int:
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


def safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator or 0) / float(denominator), 4)


def decimal_to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
