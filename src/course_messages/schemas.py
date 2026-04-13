from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import TargetRuleType


class CourseMessageCreate(BaseModel):
    course_id: int
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    target_rule_type: TargetRuleType = TargetRuleType.ALL
    target_rule_value: str | None = Field(default=None, max_length=255)


class CourseMessageResponse(BaseModel):
    course_message_id: int
    course_id: int
    course_name: str | None = None
    sender_user_id: int
    title: str
    body: str
    target_rule_type: TargetRuleType | None
    target_rule_value: str | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
