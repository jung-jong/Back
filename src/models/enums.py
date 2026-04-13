from enum import Enum


class UserRole(str, Enum):
    STUDENT = "STUDENT"
    INSTRUCTOR = "INSTRUCTOR"


class CourseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class EnrollmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DROPPED = "DROPPED"
    COMPLETED = "COMPLETED"


class Rank(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class DocumentCategory(str, Enum):
    LECTURE = "LECTURE"
    SUPPLEMENT = "SUPPLEMENT"
    REFERENCE = "REFERENCE"


class EmbeddingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SenderType(str, Enum):
    STUDENT = "STUDENT"
    AI = "AI"
    SYSTEM = "SYSTEM"


class MessageType(str, Enum):
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    NOTICE = "NOTICE"


class QuestCreatorType(str, Enum):
    AI_GENERATED = "AI_GENERATED"
    MANUAL = "MANUAL"


class Difficulty(str, Enum):
    EASY = "EASY"
    NORMAL = "NORMAL"
    HARD = "HARD"


class QuestStatus(str, Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    CLOSED = "CLOSED"


class TargetRuleType(str, Enum):
    ALL = "ALL"
    RANK = "RANK"
    SELECTED = "SELECTED"


class QuestionType(str, Enum):
    SHORT_ANSWER = "SHORT_ANSWER"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"


class StudentQuestStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    STARTED = "STARTED"
    SUBMITTED = "SUBMITTED"
    GRADED = "GRADED"
    EXPIRED = "EXPIRED"


class RecentSourceType(str, Enum):
    CHAT = "CHAT"
    QUEST = "QUEST"


class InterventionType(str, Enum):
    SEND_QUEST = "SEND_QUEST"
    SEND_MESSAGE = "SEND_MESSAGE"
    UPLOAD_MATERIAL = "UPLOAD_MATERIAL"


class InterventionStatus(str, Enum):
    PENDING = "PENDING"
    ACTIONED = "ACTIONED"
    DISMISSED = "DISMISSED"
