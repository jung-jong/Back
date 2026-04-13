from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import (
    Enrollment,
    Quest,
    QuestQuestion,
    QuestQuestionChoice,
    StudentQuest,
    StudentQuestAnswer,
)
from src.models.enums import (
    EnrollmentStatus,
    QuestionType,
    Rank,
    RecentSourceType,
    TargetRuleType,
    StudentQuestStatus,
)
from src.analytics.weak_concept_service import upsert_weak_concepts


async def calculate_max_score(session: AsyncSession, quest_id: int) -> int:
    result = await session.execute(
        select(QuestQuestion).where(QuestQuestion.quest_id == quest_id),
    )
    return sum(question.points or 0 for question in result.scalars().all())


async def assign_quest_to_active_enrollments(
    session: AsyncSession,
    quest: Quest,
) -> list[StudentQuest]:
    max_score = await calculate_max_score(session, quest.quest_id)
    query = select(Enrollment).where(
        Enrollment.course_id == quest.course_id,
        Enrollment.status == EnrollmentStatus.ACTIVE,
    )
    if quest.target_rule_type == TargetRuleType.RANK and quest.target_rule_value:
        query = query.where(Enrollment.current_rank == quest.target_rule_value)
    elif quest.target_rule_type == TargetRuleType.SELECTED and quest.target_rule_value:
        enrollment_ids = parse_selected_enrollment_ids(quest.target_rule_value)
        if not enrollment_ids:
            return []
        query = query.where(Enrollment.enrollment_id.in_(enrollment_ids))

    result = await session.execute(query)
    enrollments = result.scalars().all()

    assigned = []
    for enrollment in enrollments:
        existing = await session.execute(
            select(StudentQuest).where(
                StudentQuest.enrollment_id == enrollment.enrollment_id,
                StudentQuest.quest_id == quest.quest_id,
            ),
        )
        if existing.scalar_one_or_none() is not None:
            continue

        student_quest = StudentQuest(
            enrollment_id=enrollment.enrollment_id,
            quest_id=quest.quest_id,
            max_score=max_score,
        )
        session.add(student_quest)
        assigned.append(student_quest)
    return assigned


async def grade_student_quest(
    session: AsyncSession,
    student_quest: StudentQuest,
    submitted_answers: dict[int, dict],
) -> list[StudentQuestAnswer]:
    quest_result = await session.execute(
        select(Quest)
        .options(
            selectinload(Quest.questions).selectinload(QuestQuestion.choices),
        )
        .where(Quest.quest_id == student_quest.quest_id),
    )
    quest = quest_result.scalar_one()

    await session.execute(
        delete(StudentQuestAnswer).where(
            StudentQuestAnswer.student_quest_id == student_quest.student_quest_id,
        ),
    )

    answers = []
    total_score = 0
    weak_concepts = []
    for question in sorted(quest.questions, key=lambda item: item.question_order):
        submitted = submitted_answers.get(question.quest_question_id, {})
        selected_choice_id = submitted.get("selected_choice_id")
        answer_text = submitted.get("answer_text")
        is_correct = False

        if question.question_type == QuestionType.MULTIPLE_CHOICE:
            correct_choice_ids = {
                choice.quest_question_choice_id
                for choice in question.choices
                if choice.is_correct
            }
            is_correct = selected_choice_id in correct_choice_ids
        elif question.correct_answer_text and answer_text:
            is_correct = normalize_answer(answer_text) == normalize_answer(
                question.correct_answer_text,
            )

        score_earned = question.points or 0 if is_correct else 0
        total_score += score_earned
        if not is_correct:
            weak_concepts.append(question.question_text[:255])

        answer = StudentQuestAnswer(
            student_quest_id=student_quest.student_quest_id,
            quest_question_id=question.quest_question_id,
            selected_choice_id=selected_choice_id,
            answer_text=answer_text,
            is_correct=is_correct,
            score_earned=score_earned,
        )
        session.add(answer)
        answers.append(answer)

    student_quest.score_earned = total_score
    student_quest.status = StudentQuestStatus.GRADED
    student_quest.submitted_at = datetime.utcnow()
    student_quest.graded_at = datetime.utcnow()
    student_quest.xp_awarded = calculate_xp_award(
        score_earned=total_score,
        max_score=student_quest.max_score,
        xp_reward=quest.xp_reward or 0,
    )

    enrollment = await session.get(Enrollment, student_quest.enrollment_id)
    if enrollment is not None:
        enrollment.current_xp = (enrollment.current_xp or 0) + (student_quest.xp_awarded or 0)
        enrollment.current_rank = calculate_rank(enrollment.current_xp or 0)
        enrollment.last_active_at = datetime.utcnow()

    await session.flush()
    await upsert_weak_concepts(
        session=session,
        enrollment_id=student_quest.enrollment_id,
        concepts=weak_concepts,
        recent_source_type=RecentSourceType.QUEST,
        recent_source_ref_id=student_quest.student_quest_id,
    )
    return answers


def normalize_answer(value: str) -> str:
    return " ".join(value.strip().lower().split())


def calculate_xp_award(score_earned: int, max_score: int, xp_reward: int) -> int:
    if max_score <= 0 or xp_reward <= 0:
        return 0
    return int(xp_reward * (score_earned / max_score))


def calculate_rank(current_xp: int) -> Rank:
    if current_xp >= 1200:
        return Rank.A
    if current_xp >= 600:
        return Rank.B
    return Rank.C


def parse_selected_enrollment_ids(value: str) -> list[int]:
    enrollment_ids = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            enrollment_ids.append(int(item))
        except ValueError:
            continue
    return enrollment_ids
