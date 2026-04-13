from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.ai.service import AIService
from src.models import (
    Course,
    CourseDocument,
    DocumentChunk,
    Enrollment,
    Quest,
    QuestQuestion,
    QuestQuestionChoice,
    StudentQuest,
    User,
)
from src.models.enums import (
    Difficulty,
    QuestionType,
    QuestCreatorType,
    QuestStatus,
    StudentQuestStatus,
    TargetRuleType,
    UserRole,
)
from src.quests.schemas import QuestCreate
from src.quests.service import assign_quest_to_active_enrollments, grade_student_quest
from webapp.routers.courses import get_course_for_user

router = APIRouter(prefix="/courses/{course_id}/quests", tags=["quests"])


class QuestQuestionRequest(BaseModel):
    type: str = "multiple"
    question: str = ""
    options: list[str] = Field(default_factory=list, max_length=10)
    answer: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_multiple_choice(self) -> "QuestQuestionRequest":
        if self.type != "multiple":
            raise ValueError("현재 퀘스트 편집기는 객관식 문항만 지원합니다.")

        selected_original_index = self.answer
        normalized_options = []
        normalized_answer = 0
        selected_option_kept = False
        for original_index, option in enumerate(self.options):
            option_text = str(option).strip()
            if not option_text:
                continue
            if original_index == selected_original_index:
                normalized_answer = len(normalized_options)
                selected_option_kept = True
            normalized_options.append(option_text)

        self.options = normalized_options
        if not normalized_options:
            self.answer = 0
        elif selected_option_kept:
            self.answer = normalized_answer
        else:
            self.answer = min(selected_original_index, len(normalized_options) - 1)
        return self


class QuestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    scope: str | None = None
    week: str | None = None
    difficulty: str | None = "보통"
    questionCount: int = Field(default=1, ge=1, le=20)
    targetGroup: str | None = None
    deadline: str | None = None
    xp: int = Field(default=0, ge=0)
    description: str | None = None
    questions: list[QuestQuestionRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_question_count(self) -> "QuestRequest":
        if self.questions:
            self.questionCount = len(self.questions)
        return self


class QuestSubmitRequest(BaseModel):
    answers: dict[str, int | bool | str]


class QuestAIDraftRequest(BaseModel):
    title: str | None = None
    scope: str | None = None
    week: str | None = None
    difficulty: str | None = "보통"
    questionCount: int = Field(default=5, ge=1, le=20)
    targetGroup: str | None = "전체 수강생"
    deadline: str | None = None
    xp: int = Field(default=100, ge=0)
    description: str | None = None
    optionCount: int = Field(default=4, ge=2, le=6)


async def get_instructor_course(session: AsyncSession, course_id: int, current_user: User) -> Course:
    return await get_course_for_user(session, course_id, current_user, instructor_only=True)


def difficulty_to_db(value: str | Difficulty | None) -> Difficulty:
    if isinstance(value, Difficulty):
        return value
    normalized = (value or "").strip().lower()
    if normalized in {"easy", "쉬움", "쉬운", "하"}:
        return Difficulty.EASY
    if normalized in {"hard", "어려움", "어려운", "상"}:
        return Difficulty.HARD
    return Difficulty.NORMAL


def difficulty_to_front(value: Difficulty | None) -> str:
    return {
        Difficulty.EASY: "쉬움",
        Difficulty.NORMAL: "보통",
        Difficulty.HARD: "어려움",
    }.get(value, "보통")


def scope_text(quest: Quest) -> str:
    if quest.scope_week_start and quest.scope_week_end:
        if quest.scope_week_start == quest.scope_week_end:
            return f"{quest.scope_week_start}주차 강의 전체"
        return f"{quest.scope_week_start}-{quest.scope_week_end}주차 강의 전체"
    return quest.description or "강의 전체"


def parse_scope_weeks(scope: str | None) -> tuple[int | None, int | None]:
    if not scope:
        return None, None
    numbers = [int(item) for item in re.findall(r"\d+", scope)]
    if not numbers:
        return None, None
    start = numbers[0]
    end = numbers[1] if len(numbers) > 1 else start
    return start, end


def parse_target_group(value: str | None) -> tuple[TargetRuleType, str | None]:
    text_value = (value or "").strip().upper()
    if not text_value or "전체" in text_value or "ALL" in text_value:
        return TargetRuleType.ALL, None

    ranks = []
    for rank in ("A", "B", "C"):
        if re.search(rf"(?<![A-Z0-9]){rank}(?![A-Z0-9])", text_value):
            ranks.append(rank)
    if ranks:
        return TargetRuleType.RANK, ",".join(ranks)

    return TargetRuleType.ALL, None


def target_group_text(quest: Quest) -> str:
    if quest.target_rule_type == TargetRuleType.RANK and quest.target_rule_value:
        ranks = ",".join(
            rank
            for rank in quest.target_rule_value.replace("·", ",").replace(".", ",").split(",")
            if rank.strip().upper() in {"A", "B", "C"}
        )
        return f"{ranks or quest.target_rule_value} 등급 학생"
    if quest.target_rule_type == TargetRuleType.SELECTED:
        return "선택 학생"
    return "전체 수강생"


async def quest_response(session: AsyncSession, quest: Quest, current_user: User | None = None) -> dict:
    completed = False
    if current_user and current_user.role == UserRole.STUDENT:
        result = await session.execute(
            select(StudentQuest)
            .join(Enrollment, StudentQuest.enrollment_id == Enrollment.enrollment_id)
            .where(
                Enrollment.student_id == current_user.user_id,
                StudentQuest.quest_id == quest.quest_id,
                StudentQuest.status == StudentQuestStatus.GRADED,
            ),
        )
        completed = result.scalar_one_or_none() is not None

    ordered_questions = sorted(quest.questions or [], key=lambda item: item.question_order)
    preview = ordered_questions[0].question_text if ordered_questions else quest.description or ""
    questions = []
    for question in ordered_questions:
        choices = sorted(question.choices or [], key=lambda item: item.choice_order)
        if question.question_type == QuestionType.MULTIPLE_CHOICE:
            answer = next((index for index, choice in enumerate(choices) if choice.is_correct), 0)
            questions.append(
                {
                    "id": f"qq-{question.quest_question_id}",
                    "type": "multiple",
                    "question": question.question_text,
                    "options": [choice.choice_text for choice in choices],
                    "answer": answer,
                    "hint": question.explanation,
                },
            )
    return {
        "id": str(quest.quest_id),
        "title": quest.title,
        "scope": scope_text(quest),
        "difficulty": difficulty_to_front(quest.difficulty),
        "questionCount": len(ordered_questions),
        "targetGroup": target_group_text(quest),
        "status": "sent" if quest.status == QuestStatus.SENT else "pending",
        "previewContent": preview,
        "source": "ai" if quest.creator_type == QuestCreatorType.AI_GENERATED else "manual",
        "type": "ai" if quest.creator_type == QuestCreatorType.AI_GENERATED else "professor",
        "deadline": "",
        "xp": quest.xp_reward or 0,
        "description": quest.description,
        "questions": questions,
        "completed": completed,
    }


async def load_quest(session: AsyncSession, quest_id: int) -> Quest:
    result = await session.execute(
        select(Quest)
        .options(selectinload(Quest.questions).selectinload(QuestQuestion.choices))
        .where(Quest.quest_id == quest_id),
    )
    quest = result.scalar_one_or_none()
    if quest is None:
        raise HTTPException(status_code=404, detail="퀘스트를 찾을 수 없습니다.")
    return quest


def fallback_questions(payload: QuestRequest) -> list[QuestQuestionRequest]:
    fallback_text = payload.description or payload.title
    return [
        QuestQuestionRequest(
            question=fallback_text,
            options=["정답", "오답 1", "오답 2", "오답 3"],
            answer=0,
        )
        for _ in range(payload.questionCount)
    ]


def fallback_ai_questions(payload: QuestAIDraftRequest, course: Course) -> list[dict]:
    option_count = max(2, min(payload.optionCount, 6))
    title_prefix = payload.scope or course.course_name
    return [
        {
            "type": "multiple",
            "question": f"{title_prefix} 핵심 개념 확인 문제 {index}",
            "options": [f"보기 {choice}" for choice in range(1, option_count + 1)],
            "answer": 0,
        }
        for index in range(1, payload.questionCount + 1)
    ]


def normalize_ai_draft_questions(
    raw_questions: list | None,
    payload: QuestAIDraftRequest,
    course: Course,
) -> list[dict]:
    normalized = []
    for item in raw_questions or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        options = [
            str(option).strip()
            for option in item.get("options") or []
            if str(option).strip()
        ][: payload.optionCount]
        try:
            answer = int(item.get("answer", 0))
        except (TypeError, ValueError):
            answer = 0
        if not question or len(options) < 2:
            continue
        normalized.append(
            {
                "type": "multiple",
                "question": question,
                "options": options,
                "answer": min(max(answer, 0), len(options) - 1),
            },
        )
        if len(normalized) >= payload.questionCount:
            break
    if normalized:
        return normalized
    return fallback_ai_questions(payload, course)


def assert_quest_sendable(quest: Quest) -> None:
    questions = sorted(quest.questions or [], key=lambda item: item.question_order)
    if not questions:
        raise HTTPException(status_code=400, detail="발송할 문항이 없습니다.")
    for index, question in enumerate(questions, start=1):
        if not question.question_text.strip():
            raise HTTPException(status_code=400, detail=f"{index}번 문항 내용을 입력해 주세요.")
        if question.question_type != QuestionType.MULTIPLE_CHOICE:
            continue
        choices = [choice for choice in question.choices if choice.choice_text.strip()]
        if len(choices) < 2:
            raise HTTPException(status_code=400, detail=f"{index}번 문항은 보기 2개 이상이 필요합니다.")
        if not any(choice.is_correct for choice in choices):
            raise HTTPException(status_code=400, detail=f"{index}번 문항의 정답을 선택해 주세요.")


async def replace_front_questions(
    session: AsyncSession,
    quest: Quest,
    questions: list[QuestQuestionRequest],
) -> None:
    await session.execute(
        delete(QuestQuestionChoice).where(
            QuestQuestionChoice.quest_question_id.in_(
                select(QuestQuestion.quest_question_id).where(QuestQuestion.quest_id == quest.quest_id),
            ),
        ),
    )
    await session.execute(delete(QuestQuestion).where(QuestQuestion.quest_id == quest.quest_id))

    for question_index, item in enumerate(questions, start=1):
        question = QuestQuestion(
            quest_id=quest.quest_id,
            question_order=question_index,
            question_text=item.question.strip() or f"문항 {question_index}",
            question_type=QuestionType.MULTIPLE_CHOICE,
            points=1,
        )
        session.add(question)
        await session.flush()

        for choice_index, choice_text in enumerate(item.options, start=1):
            session.add(
                QuestQuestionChoice(
                    quest_question_id=question.quest_question_id,
                    choice_order=choice_index,
                    choice_text=choice_text.strip(),
                    is_correct=(choice_index - 1) == item.answer,
                ),
            )


async def create_quest_from_schema(
    payload: QuestCreate,
    current_user: User,
    session: AsyncSession,
) -> Quest:
    await get_instructor_course(session, payload.course_id, current_user)
    quest = Quest(
        course_id=payload.course_id,
        creator_type=QuestCreatorType.MANUAL,
        created_by=current_user.user_id,
        title=payload.title,
        description=payload.description,
        scope_week_start=payload.scope_week_start,
        scope_week_end=payload.scope_week_end,
        difficulty=payload.difficulty,
        xp_reward=payload.xp_reward,
        target_rule_type=payload.target_rule_type,
        target_rule_value=payload.target_rule_value,
    )
    session.add(quest)
    await session.flush()

    for question_payload in payload.questions:
        question = QuestQuestion(
            quest_id=quest.quest_id,
            question_order=question_payload.question_order,
            question_text=question_payload.question_text,
            question_type=question_payload.question_type,
            points=question_payload.points,
            correct_answer_text=question_payload.correct_answer_text,
            explanation=question_payload.explanation,
        )
        session.add(question)
        await session.flush()
        for choice_payload in question_payload.choices:
            session.add(
                QuestQuestionChoice(
                    quest_question_id=question.quest_question_id,
                    choice_order=choice_payload.choice_order,
                    choice_text=choice_payload.choice_text,
                    is_correct=choice_payload.is_correct,
                ),
            )

    await session.commit()
    return await load_quest(session, quest.quest_id)


@router.get("")
async def list_course_quests(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    await get_course_for_user(session, course_id, current_user)
    query = (
        select(Quest)
        .options(selectinload(Quest.questions).selectinload(QuestQuestion.choices))
        .where(Quest.course_id == course_id)
        .order_by(Quest.created_at.desc())
    )
    if current_user.role == UserRole.STUDENT:
        query = query.where(Quest.status == QuestStatus.SENT)

    result = await session.execute(query)
    return [await quest_response(session, quest, current_user) for quest in result.scalars().all()]


@router.post("/ai-draft")
async def create_ai_quest_draft(
    course_id: int,
    payload: QuestAIDraftRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    course = await get_instructor_course(session, course_id, current_user)
    week_start, _ = parse_scope_weeks(payload.week)
    document_query = select(CourseDocument).where(
        CourseDocument.course_id == course_id,
        CourseDocument.deleted_at.is_(None),
    )
    if week_start is not None:
        document_query = document_query.where(CourseDocument.week_number == week_start)
    documents_result = await session.execute(
        document_query.order_by(CourseDocument.uploaded_at.desc())
        .limit(8),
    )
    document_rows = documents_result.scalars().all()
    documents = [
        {
            "title": document.title,
            "week": document.week_number,
            "topic": document.topic,
        }
        for document in document_rows
    ]
    document_ids = [document.course_document_id for document in document_rows]
    chunk_snippets = []
    if document_ids:
        chunks_result = await session.execute(
            select(DocumentChunk, CourseDocument)
            .join(CourseDocument, DocumentChunk.course_document_id == CourseDocument.course_document_id)
            .where(DocumentChunk.course_document_id.in_(document_ids))
            .order_by(DocumentChunk.created_at.desc())
            .limit(10),
        )
        chunk_snippets = [
            {
                "document": document.title,
                "page": chunk.page_start,
                "text": (chunk.chunk_text_preview or "")[:700],
            }
            for chunk, document in chunks_result.all()
            if (chunk.chunk_text_preview or "").strip()
        ]
    system_prompt = (
        "You create editable multiple-choice quiz drafts for instructors. "
        "Return only strict JSON. Do not include markdown. "
        "The JSON shape must be: "
        "{\"title\": string, \"description\": string, \"questions\": ["
        "{\"type\":\"multiple\", \"question\": string, \"options\": string[], \"answer\": number}"
        "]}. "
        "Every question must have exactly the requested number of options, and answer is a 0-based index."
    )
    user_prompt = (
        f"Course name: {course.course_name}\n"
        f"Course description: {course.course_description or ''}\n"
        f"Scope: {payload.scope or '전체 강의'}\n"
        f"Difficulty: {payload.difficulty or '보통'}\n"
        f"Question count: {payload.questionCount}\n"
        f"Option count per question: {payload.optionCount}\n"
        f"Instructor note: {payload.description or ''}\n"
        f"Selected week: {payload.week or 'all weeks'}\n"
        f"Uploaded document summaries: {documents}\n"
        f"Reference snippets: {chunk_snippets}\n"
        "Write the quiz in Korean. Make plausible but clearly distinguishable wrong options."
    )

    try:
        ai_response = await AIService().generate_json_response(system_prompt, user_prompt)
    except Exception:
        ai_response = {}

    questions = normalize_ai_draft_questions(
        ai_response.get("questions") if isinstance(ai_response, dict) else None,
        payload,
        course,
    )
    title = (
        payload.title
        or (ai_response.get("title") if isinstance(ai_response, dict) else None)
        or f"{payload.scope or course.course_name} 개념 점검"
    )
    description = (
        payload.description
        or (ai_response.get("description") if isinstance(ai_response, dict) else None)
        or "강의 핵심 개념을 확인하는 퀘스트입니다."
    )
    return {
        "title": title,
        "scope": payload.scope or "강의 전체",
        "difficulty": difficulty_to_front(difficulty_to_db(payload.difficulty)),
        "questionCount": len(questions),
        "targetGroup": payload.targetGroup or "전체 수강생",
        "deadline": payload.deadline or "",
        "xp": payload.xp,
        "description": description,
        "questions": questions,
    }


@router.get("/{quest_id}/content")
async def get_quest_content(
    course_id: int,
    quest_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_course_for_user(session, course_id, current_user)
    result = await session.execute(
        select(Quest)
        .options(selectinload(Quest.questions).selectinload(QuestQuestion.choices))
        .where(Quest.quest_id == quest_id, Quest.course_id == course_id),
    )
    quest = result.scalar_one_or_none()
    if quest is None:
        raise HTTPException(status_code=404, detail="퀘스트를 찾을 수 없습니다.")
    if current_user.role == UserRole.STUDENT and quest.status != QuestStatus.SENT:
        raise HTTPException(status_code=404, detail="퀘스트를 찾을 수 없습니다.")

    questions = []
    for question in sorted(quest.questions, key=lambda item: item.question_order):
        choices = sorted(question.choices, key=lambda item: item.choice_order)
        if question.question_type == QuestionType.MULTIPLE_CHOICE:
            answer = next((index for index, choice in enumerate(choices) if choice.is_correct), 0)
            questions.append(
                {
                    "id": f"qq-{question.quest_question_id}",
                    "type": "multiple",
                    "question": question.question_text,
                    "options": [choice.choice_text for choice in choices],
                    "answer": answer,
                    "hint": question.explanation,
                },
            )
        else:
            questions.append(
                {
                    "id": f"qq-{question.quest_question_id}",
                    "type": "short",
                    "question": question.question_text,
                    "answer": question.correct_answer_text or "",
                    "hint": question.explanation,
                },
            )

    return {"intro": quest.description or "아래 문제를 풀어보세요.", "questions": questions}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course_quest(
    course_id: int,
    payload: QuestRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_instructor_course(session, course_id, current_user)
    scope_week_start, scope_week_end = parse_scope_weeks(payload.scope)
    target_rule_type, target_rule_value = parse_target_group(payload.targetGroup)
    quest = Quest(
        course_id=course_id,
        creator_type=QuestCreatorType.MANUAL,
        created_by=current_user.user_id,
        title=payload.title,
        description=payload.description,
        scope_week_start=scope_week_start,
        scope_week_end=scope_week_end,
        difficulty=difficulty_to_db(payload.difficulty),
        xp_reward=payload.xp,
        target_rule_type=target_rule_type,
        target_rule_value=target_rule_value,
    )
    session.add(quest)
    await session.flush()

    await replace_front_questions(session, quest, payload.questions or fallback_questions(payload))
    await session.commit()
    return await quest_response(session, await load_quest(session, quest.quest_id), current_user)


@router.put("/{quest_id}")
async def update_course_quest(
    course_id: int,
    quest_id: int,
    payload: QuestRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_instructor_course(session, course_id, current_user)
    quest = await session.get(Quest, quest_id)
    if quest is None or quest.course_id != course_id:
        raise HTTPException(status_code=404, detail="퀘스트를 찾을 수 없습니다.")

    scope_week_start, scope_week_end = parse_scope_weeks(payload.scope)
    quest.title = payload.title
    quest.description = payload.description
    quest.scope_week_start = scope_week_start
    quest.scope_week_end = scope_week_end
    quest.difficulty = difficulty_to_db(payload.difficulty)
    quest.xp_reward = payload.xp
    quest.target_rule_type, quest.target_rule_value = parse_target_group(payload.targetGroup)
    await replace_front_questions(session, quest, payload.questions or fallback_questions(payload))
    await session.flush()

    max_score = len(payload.questions or fallback_questions(payload))
    await session.execute(
        update(StudentQuest)
        .where(
            StudentQuest.quest_id == quest_id,
            StudentQuest.status.in_([StudentQuestStatus.ASSIGNED, StudentQuestStatus.STARTED]),
        )
        .values(max_score=max_score),
    )
    await session.commit()
    return await quest_response(session, await load_quest(session, quest_id), current_user)


@router.post("/{quest_id}/send")
async def send_course_quest(
    course_id: int,
    quest_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    await get_instructor_course(session, course_id, current_user)
    quest = await load_quest(session, quest_id)
    if quest.course_id != course_id:
        raise HTTPException(status_code=404, detail="퀘스트를 찾을 수 없습니다.")
    assert_quest_sendable(quest)

    quest.status = QuestStatus.SENT
    quest.sent_at = datetime.utcnow()
    await assign_quest_to_active_enrollments(session, quest)
    await session.commit()
    return await quest_response(session, await load_quest(session, quest_id), current_user)


@router.delete("/{quest_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course_quest(
    course_id: int,
    quest_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    await get_instructor_course(session, course_id, current_user)
    params = {"quest_id": quest_id, "course_id": course_id}
    await session.execute(
        text(
            "DELETE FROM student_quest_answers "
            "WHERE student_quest_id IN ("
            "SELECT sq.student_quest_id FROM student_quests sq "
            "JOIN quests q ON sq.quest_id = q.quest_id "
            "WHERE q.quest_id=:quest_id AND q.course_id=:course_id"
            ")",
        ),
        params,
    )
    await session.execute(text("DELETE FROM student_quests WHERE quest_id=:quest_id"), params)
    await session.execute(text("UPDATE ai_interventions SET linked_quest_id=NULL WHERE linked_quest_id=:quest_id"), params)
    await session.execute(
        text(
            "DELETE FROM quest_question_choices "
            "WHERE quest_question_id IN ("
            "SELECT quest_question_id FROM quest_questions WHERE quest_id=:quest_id"
            ")",
        ),
        params,
    )
    await session.execute(text("DELETE FROM quest_questions WHERE quest_id=:quest_id"), params)
    await session.execute(text("DELETE FROM quests WHERE quest_id=:quest_id AND course_id=:course_id"), params)
    await session.commit()
    return Response(status_code=204)


@router.post("/{quest_id}/submit")
async def submit_course_quest(
    course_id: int,
    quest_id: int,
    payload: QuestSubmitRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(status_code=403, detail="학생만 제출할 수 있습니다.")

    enrollment_result = await session.execute(
        select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == current_user.user_id),
    )
    enrollment = enrollment_result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="수강 정보를 찾을 수 없습니다.")

    result = await session.execute(
        select(StudentQuest)
        .options(selectinload(StudentQuest.quest).selectinload(Quest.questions).selectinload(QuestQuestion.choices))
        .where(StudentQuest.quest_id == quest_id, StudentQuest.enrollment_id == enrollment.enrollment_id),
    )
    student_quest = result.scalar_one_or_none()
    if student_quest is None:
        raise HTTPException(status_code=404, detail="할당된 퀘스트를 찾을 수 없습니다.")

    if student_quest.status in {StudentQuestStatus.SUBMITTED, StudentQuestStatus.GRADED}:
        raise HTTPException(status_code=409, detail="이미 제출한 퀘스트입니다.")

    submitted = {}
    for key, value in payload.answers.items():
        try:
            question_id = int(str(key).replace("qq-", ""))
        except ValueError:
            continue
        question = next((item for item in student_quest.quest.questions if item.quest_question_id == question_id), None)
        if question is None:
            continue
        if question.question_type == QuestionType.MULTIPLE_CHOICE and isinstance(value, int):
            choices = sorted(question.choices, key=lambda item: item.choice_order)
            if 0 <= value < len(choices):
                submitted[question_id] = {"selected_choice_id": choices[value].quest_question_choice_id}
        else:
            submitted[question_id] = {"answer_text": str(value)}

    await grade_student_quest(session, student_quest, submitted)
    await session.commit()
    return {
        "score": student_quest.score_earned or 0,
        "total": student_quest.max_score,
        "xpEarned": student_quest.xp_awarded or 0,
    }
