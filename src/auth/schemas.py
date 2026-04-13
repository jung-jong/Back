from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.models.enums import UserRole


class UserCreate(BaseModel):
    role: UserRole
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    student_no: str | None = Field(default=None, max_length=50)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    user_id: int
    role: UserRole
    name: str
    email: EmailStr
    student_no: str | None

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
