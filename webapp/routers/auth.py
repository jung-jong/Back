from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token, hash_password, verify_password
from database.database import get_db_session
from dependencies.dependency import get_current_user
from src.models import User
from src.models.enums import UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    role: str | None = None


class SignupRequest(LoginRequest):
    name: str = Field(min_length=1, max_length=100)


def parse_role(role: str | None) -> UserRole:
    normalized = (role or "").upper()
    if normalized == UserRole.STUDENT.value:
        return UserRole.STUDENT
    if normalized == UserRole.INSTRUCTOR.value:
        return UserRole.INSTRUCTOR
    raise HTTPException(status_code=400, detail="role은 student 또는 instructor여야 합니다.")


def front_role(role: UserRole) -> str:
    return role.value.lower()


def user_response(user: User) -> dict:
    return {
        "id": str(user.user_id),
        "name": user.name,
        "email": user.email,
        "role": front_role(user.role),
    }


def auth_response(user: User) -> dict:
    return {
        "user": user_response(user),
        "token": create_access_token(subject=str(user.user_id)),
    }


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    existing_user = await session.execute(
        select(User).where(User.email == payload.email.lower()),
    )
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="이미 사용 중인 이메일입니다.")

    user = User(
        role=parse_role(payload.role),
        name=payload.name,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="이미 사용 중인 이메일입니다.") from None

    await session.refresh(user)
    return auth_response(user)


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    result = await session.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.role and front_role(user.role) != payload.role.lower():
        raise HTTPException(status_code=403, detail="선택한 역할과 계정 역할이 다릅니다.")
    return auth_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    return None


@router.get("/me")
async def read_me(current_user: User = Depends(get_current_user)) -> dict:
    return user_response(current_user)
