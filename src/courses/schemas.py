from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import CourseStatus


class CourseCreate(BaseModel):
    course_name: str = Field(min_length=1, max_length=255)
    course_description: str | None = None
    term: str = Field(min_length=1, max_length=50)
    entry_code: str | None = Field(default=None, min_length=4, max_length=20)
    system_prompt: str | None = None


class CourseResponse(BaseModel):
    course_id: int
    instructor_id: int
    course_name: str
    course_description: str | None
    term: str
    entry_code: str
    system_prompt: str | None
    status: CourseStatus | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
