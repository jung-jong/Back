import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.dependency import get_current_user
from webapp.routers.quests import create_quest_from_schema, get_instructor_course, load_quest
from database.database import get_db_session
from src.models import AIIntervention, CourseMessage, User
from src.models.enums import (
    InterventionStatus,
    InterventionType,
    TargetRuleType,
)
from src.interventions.schemas import (
    AIInterventionResponse,
    InterventionActionResponse,
    InterventionGenerateRequest,
    InterventionMessageAction,
    InterventionQuestAction,
)
from src.quests.schemas import QuestCreate
from src.interventions.service import generate_weekly_interventions

router = APIRouter(prefix="/interventions", tags=["interventions"])


@router.post("/generate", response_model=list[AIInterventionResponse])
async def generate_intervention(
    payload: InterventionGenerateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[AIIntervention]:
    course = await get_instructor_course(session, payload.course_id, current_user)
    return await generate_weekly_interventions(
        session=session,
        course=course,
        week_start_date=payload.week_start_date,
        week_end_date=payload.week_end_date,
    )


@router.get("/course/{course_id}", response_model=list[AIInterventionResponse])
async def list_course_interventions(
    course_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[AIIntervention]:
    await get_instructor_course(session, course_id, current_user)
    result = await session.execute(
        select(AIIntervention)
        .where(AIIntervention.course_id == course_id)
        .order_by(AIIntervention.created_at.desc()),
    )
    return list(result.scalars().all())


@router.post("/{ai_intervention_id}/dismiss", response_model=AIInterventionResponse)
async def dismiss_intervention(
    ai_intervention_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AIIntervention:
    intervention = await get_instructor_intervention(
        session,
        ai_intervention_id,
        current_user,
    )
    intervention.status = InterventionStatus.DISMISSED
    await session.commit()
    await session.refresh(intervention)
    return intervention


@router.post("/{ai_intervention_id}/action-message", response_model=InterventionActionResponse)
async def action_message_intervention(
    ai_intervention_id: int,
    payload: InterventionMessageAction | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> InterventionActionResponse:
    intervention = await get_instructor_intervention(
        session,
        ai_intervention_id,
        current_user,
    )
    detail = parse_action_detail(intervention)
    target_rule_type = parse_target_rule_type(detail.get("target_rule_type"))
    message_payload = payload or InterventionMessageAction(
        title=detail.get("title") or intervention.title,
        body=(
            detail.get("body")
            or detail.get("message")
            or intervention.target_summary
            or intervention.title
        ),
        target_rule_type=target_rule_type,
        target_rule_value=detail.get("target_rule_value"),
    )
    course_message = CourseMessage(
        course_id=intervention.course_id,
        sender_user_id=current_user.user_id,
        title=message_payload.title,
        body=message_payload.body,
        target_rule_type=message_payload.target_rule_type,
        target_rule_value=message_payload.target_rule_value,
    )
    session.add(course_message)
    intervention.status = InterventionStatus.ACTIONED
    intervention.actioned_at = datetime.utcnow()
    await session.commit()
    await session.refresh(intervention)
    await session.refresh(course_message)
    return InterventionActionResponse(
        intervention=intervention,
        course_message_id=course_message.course_message_id,
    )


@router.post("/{ai_intervention_id}/action-quest", response_model=InterventionActionResponse)
async def action_quest_intervention(
    ai_intervention_id: int,
    payload: InterventionQuestAction | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> InterventionActionResponse:
    intervention = await get_instructor_intervention(
        session,
        ai_intervention_id,
        current_user,
    )
    quest_payload = payload.quest if payload is not None else build_quest_payload(intervention)
    if quest_payload.course_id != intervention.course_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quest course_id must match intervention course_id",
        )

    quest = await create_quest_from_schema(
        payload=quest_payload,
        current_user=current_user,
        session=session,
    )
    intervention.status = InterventionStatus.ACTIONED
    intervention.actioned_at = datetime.utcnow()
    intervention.linked_quest_id = quest.quest_id
    await session.commit()
    await session.refresh(intervention)
    linked_quest = await load_quest(session, quest.quest_id)
    return InterventionActionResponse(
        intervention=intervention,
        linked_quest=linked_quest,
    )


@router.post("/{ai_intervention_id}/action-material", response_model=InterventionActionResponse)
async def action_material_intervention(
    ai_intervention_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> InterventionActionResponse:
    intervention = await get_instructor_intervention(
        session,
        ai_intervention_id,
        current_user,
    )
    if intervention.intervention_type != InterventionType.UPLOAD_MATERIAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only UPLOAD_MATERIAL interventions can be actioned as material",
        )

    intervention.status = InterventionStatus.ACTIONED
    intervention.actioned_at = datetime.utcnow()
    await session.commit()
    await session.refresh(intervention)
    return InterventionActionResponse(intervention=intervention)


async def get_instructor_intervention(
    session: AsyncSession,
    ai_intervention_id: int,
    current_user: User,
) -> AIIntervention:
    result = await session.execute(
        select(AIIntervention).where(
            AIIntervention.ai_intervention_id == ai_intervention_id,
        ),
    )
    intervention = result.scalar_one_or_none()
    if intervention is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Intervention not found",
        )
    await get_instructor_course(session, intervention.course_id, current_user)
    return intervention


def parse_action_detail(intervention: AIIntervention) -> dict:
    if not intervention.action_detail:
        return {}
    try:
        detail = json.loads(intervention.action_detail)
    except json.JSONDecodeError:
        return {}
    return detail if isinstance(detail, dict) else {}


def parse_target_rule_type(value: object) -> TargetRuleType:
    if isinstance(value, TargetRuleType):
        return value
    try:
        return TargetRuleType(str(value or TargetRuleType.ALL.value).upper())
    except ValueError:
        return TargetRuleType.ALL


def build_quest_payload(intervention: AIIntervention) -> QuestCreate:
    detail = parse_action_detail(intervention)
    questions = []
    for index, question in enumerate(detail.get("questions", []), start=1):
        choices = []
        for choice_index, choice in enumerate(question.get("choices", []), start=1):
            choices.append(
                {
                    "choice_order": choice.get("choice_order", choice_index),
                    "choice_text": choice.get("choice_text", f"Choice {choice_index}"),
                    "is_correct": bool(choice.get("is_correct", False)),
                },
            )
        if not choices:
            choices = [
                {"choice_order": 1, "choice_text": "Correct", "is_correct": True},
                {"choice_order": 2, "choice_text": "Incorrect", "is_correct": False},
            ]
        questions.append(
            {
                "question_order": question.get("question_order", index),
                "question_text": question.get("question_text", f"Question {index}"),
                "question_type": "MULTIPLE_CHOICE",
                "points": question.get("points", 1),
                "explanation": question.get("explanation"),
                "choices": choices,
            },
        )

    if not questions:
        questions = [
            {
                "question_order": 1,
                "question_text": "What is the key concept from this week's review?",
                "question_type": "MULTIPLE_CHOICE",
                "points": 1,
                "choices": [
                    {"choice_order": 1, "choice_text": "The main weak concept", "is_correct": True},
                    {"choice_order": 2, "choice_text": "An unrelated topic", "is_correct": False},
                ],
            },
        ]

    return QuestCreate.model_validate(
        {
            "course_id": intervention.course_id,
            "title": detail.get("title") or intervention.title,
            "description": detail.get("description") or intervention.target_summary,
            "difficulty": detail.get("difficulty", "NORMAL"),
            "xp_reward": detail.get("xp_reward", 50),
            "target_rule_type": parse_target_rule_type(detail.get("target_rule_type")).value,
            "target_rule_value": detail.get("target_rule_value"),
            "questions": questions,
        },
    )
