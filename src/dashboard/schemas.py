from datetime import datetime

from pydantic import BaseModel

from src.courses.schemas import CourseResponse


class RankDistributionItem(BaseModel):
    rank: str
    count: int


class WeakConceptSummary(BaseModel):
    concept_name: str
    total_error_count: int
    affected_student_count: int


class KeywordStatSummary(BaseModel):
    week_number: int
    keyword: str
    mention_count: int
    calculated_at: datetime | None


class RecentQuestionSummary(BaseModel):
    chat_message_id: int
    enrollment_id: int
    student_name: str | None
    message_text: str
    created_at: datetime | None


class StudentLearningSummary(BaseModel):
    enrollment_id: int
    student_id: int
    student_name: str
    email: str
    student_no: str | None
    current_rank: str | None
    current_xp: int
    last_active_at: datetime | None
    question_count: int
    weak_concept_count: int
    completed_quest_count: int
    average_score_rate: float | None


class CourseDashboardSummary(BaseModel):
    course: CourseResponse
    total_students: int
    active_students: int
    total_questions: int
    total_ai_answers: int
    total_documents: int
    completed_documents: int
    pending_interventions: int
    sent_quests: int
    graded_student_quests: int
    average_score_rate: float | None
    rank_distribution: list[RankDistributionItem]
    top_weak_concepts: list[WeakConceptSummary]
    keyword_stats: list[KeywordStatSummary]
    recent_questions: list[RecentQuestionSummary]
    students: list[StudentLearningSummary]
