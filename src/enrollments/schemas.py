from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import EnrollmentStatus, Rank


class EnrollmentJoinRequest(BaseModel):
    entry_code: str = Field(min_length=4, max_length=20)


class EnrollmentCourseSummary(BaseModel):
    course_id: int
    course_name: str
    course_description: str | None
    term: str
    entry_code: str

    model_config = ConfigDict(from_attributes=True)


class EnrollmentResponse(BaseModel):
    enrollment_id: int
    student_id: int
    course_id: int
    status: EnrollmentStatus | None
    current_rank: Rank | None
    current_xp: int | None
    joined_at: datetime | None
    last_active_at: datetime | None
    course: EnrollmentCourseSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class WeakConceptResponse(BaseModel):
    weak_concept_id: int
    enrollment_id: int
    concept_name: str
    error_count: int | None
    last_seen_at: datetime | None
    recent_source_type: str
    recent_source_ref_id: int | None

    model_config = ConfigDict(from_attributes=True)
