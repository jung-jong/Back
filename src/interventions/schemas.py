from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import InterventionStatus, InterventionType, TargetRuleType
from src.quests.schemas import QuestCreate, QuestResponse


class InterventionGenerateRequest(BaseModel):
    course_id: int
    week_start_date: date
    week_end_date: date


class AIInterventionResponse(BaseModel):
    ai_intervention_id: int
    course_id: int
    week_start_date: date
    week_end_date: date
    intervention_type: InterventionType
    title: str
    target_summary: str | None
    evidence: str | None
    action_detail: str | None
    status: InterventionStatus | None
    linked_quest_id: int | None
    created_at: datetime | None
    actioned_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class InterventionMessageAction(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    target_rule_type: TargetRuleType = TargetRuleType.ALL
    target_rule_value: str | None = Field(default=None, max_length=255)


class InterventionQuestAction(BaseModel):
    quest: QuestCreate


class InterventionActionResponse(BaseModel):
    intervention: AIInterventionResponse
    linked_quest: QuestResponse | None = None
    course_message_id: int | None = None
