from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.enums import (
    Difficulty,
    QuestionType,
    QuestCreatorType,
    QuestStatus,
    StudentQuestStatus,
    TargetRuleType,
)


class QuestChoiceCreate(BaseModel):
    choice_order: int = Field(ge=1)
    choice_text: str = Field(min_length=1, max_length=500)
    is_correct: bool = False


class QuestQuestionCreate(BaseModel):
    question_order: int = Field(ge=1)
    question_text: str = Field(min_length=1)
    question_type: QuestionType
    points: int = Field(default=1, ge=1)
    correct_answer_text: str | None = None
    explanation: str | None = None
    choices: list[QuestChoiceCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_question(self) -> "QuestQuestionCreate":
        if self.question_type == QuestionType.MULTIPLE_CHOICE and not self.choices:
            raise ValueError("MULTIPLE_CHOICE questions require choices")
        return self


class QuestCreate(BaseModel):
    course_id: int
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scope_week_start: int | None = None
    scope_week_end: int | None = None
    difficulty: Difficulty = Difficulty.NORMAL
    xp_reward: int = Field(default=0, ge=0)
    target_rule_type: TargetRuleType = TargetRuleType.ALL
    target_rule_value: str | None = Field(default=None, max_length=255)
    questions: list[QuestQuestionCreate] = Field(min_length=1)


class QuestChoiceResponse(BaseModel):
    quest_question_choice_id: int
    quest_question_id: int
    choice_order: int
    choice_text: str
    is_correct: bool

    model_config = ConfigDict(from_attributes=True)


class QuestQuestionResponse(BaseModel):
    quest_question_id: int
    quest_id: int
    question_order: int
    question_text: str
    question_type: QuestionType
    points: int | None
    correct_answer_text: str | None
    explanation: str | None
    choices: list[QuestChoiceResponse] = []

    model_config = ConfigDict(from_attributes=True)


class QuestResponse(BaseModel):
    quest_id: int
    course_id: int
    creator_type: QuestCreatorType
    created_by: int | None
    title: str
    description: str | None
    scope_week_start: int | None
    scope_week_end: int | None
    difficulty: Difficulty | None
    status: QuestStatus | None
    xp_reward: int | None
    target_rule_type: TargetRuleType | None
    target_rule_value: str | None
    created_at: datetime | None
    sent_at: datetime | None
    questions: list[QuestQuestionResponse] = []

    model_config = ConfigDict(from_attributes=True)


class PublicQuestChoiceResponse(BaseModel):
    quest_question_choice_id: int
    quest_question_id: int
    choice_order: int
    choice_text: str

    model_config = ConfigDict(from_attributes=True)


class PublicQuestQuestionResponse(BaseModel):
    quest_question_id: int
    quest_id: int
    question_order: int
    question_text: str
    question_type: QuestionType
    points: int | None
    choices: list[PublicQuestChoiceResponse] = []

    model_config = ConfigDict(from_attributes=True)


class PublicQuestResponse(BaseModel):
    quest_id: int
    course_id: int
    creator_type: QuestCreatorType
    created_by: int | None
    title: str
    description: str | None
    scope_week_start: int | None
    scope_week_end: int | None
    difficulty: Difficulty | None
    status: QuestStatus | None
    xp_reward: int | None
    target_rule_type: TargetRuleType | None
    target_rule_value: str | None
    created_at: datetime | None
    sent_at: datetime | None
    questions: list[PublicQuestQuestionResponse] = []

    model_config = ConfigDict(from_attributes=True)


class StudentQuestResponse(BaseModel):
    student_quest_id: int
    enrollment_id: int
    quest_id: int
    status: StudentQuestStatus | None
    score_earned: int | None
    max_score: int
    xp_awarded: int | None
    assigned_at: datetime | None
    submitted_at: datetime | None
    graded_at: datetime | None
    quest: PublicQuestResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class StudentAnswerSubmit(BaseModel):
    quest_question_id: int
    selected_choice_id: int | None = None
    answer_text: str | None = None


class StudentQuestSubmitRequest(BaseModel):
    answers: list[StudentAnswerSubmit] = Field(min_length=1)


class StudentQuestAnswerResponse(BaseModel):
    student_quest_answer_id: int
    student_quest_id: int
    quest_question_id: int
    selected_choice_id: int | None
    answer_text: str | None
    is_correct: bool | None
    score_earned: int | None
    answered_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class StudentQuestSubmitResponse(BaseModel):
    student_quest: StudentQuestResponse
    answers: list[StudentQuestAnswerResponse]
