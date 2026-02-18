"""Authentication routes."""

import base64
import time

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from stt_service.api.schemas.user import LoginResponse, UserLogin, UserResponse
from stt_service.db.repositories.user import UserRepository
from stt_service.db.session import get_db_session

router = APIRouter(prefix="/auth", tags=["Authentication"])


def make_token(user_id: str) -> str:
    """Create a simple base64 token: user_id:timestamp."""
    payload = f"{user_id}:{int(time.time())}"
    return base64.b64encode(payload.encode()).decode()


def decode_token(token: str) -> str | None:
    """Decode token and return user_id, or None if invalid."""
    try:
        payload = base64.b64decode(token.encode()).decode()
        user_id, _ts = payload.split(":", 1)
        return user_id
    except Exception:
        return None


def _user_response(user) -> UserResponse:
    return UserResponse(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: UserLogin,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Authenticate user and return token."""
    repo = UserRepository(session)
    user = await repo.authenticate(body.username, body.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token = make_token(user.id)
    return LoginResponse(
        token=token,
        user=_user_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get current authenticated user from token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )

    token = authorization.removeprefix("Bearer ").strip()
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )

    repo = UserRepository(session)
    try:
        user = await repo.get_by_id(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated.",
        )

    return _user_response(user)
