from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import MessageType, SenderType


class ChatAskRequest(BaseModel):
    enrollment_id: int
    question: str = Field(min_length=1)
    chat_session_id: int | None = None


class ChatSourceResponse(BaseModel):
    chat_message_source_id: int
    chat_message_id: int
    course_document_id: int
    page_from: int | None
    page_to: int | None
    source_label: str | None

    model_config = ConfigDict(from_attributes=True)


class ChatMessageResponse(BaseModel):
    chat_message_id: int
    chat_session_id: int
    sender_type: SenderType
    message_text: str
    message_type: MessageType | None
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ChatAskResponse(BaseModel):
    chat_session_id: int
    student_message: ChatMessageResponse
    ai_message: ChatMessageResponse
    sources: list[ChatSourceResponse]
