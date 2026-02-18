"""User management routes (admin only)."""

from fastapi import APIRouter, HTTPException, status

from stt_service.api.dependencies import AdminUser, UserRepo
from stt_service.api.schemas.user import (
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from stt_service.db.models import UserRole

router = APIRouter(prefix="/users", tags=["Users"])


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


@router.get("", response_model=UserListResponse)
async def list_users(
    user_repo: UserRepo,
    _admin: AdminUser,
) -> UserListResponse:
    """List all users (admin only)."""
    users = await user_repo.list_users()
    total = await user_repo.count_users()
    return UserListResponse(
        users=[_user_response(u) for u in users],
        total=total,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    user_repo: UserRepo,
    _admin: AdminUser,
) -> UserResponse:
    """Create a new user (admin only)."""
    existing = await user_repo.get_by_username(body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' already exists.",
        )

    role = UserRole(body.role) if body.role else UserRole.USER
    user = await user_repo.create(
        username=body.username,
        password=body.password,
        role=role,
        display_name=body.display_name,
    )
    return _user_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    user_repo: UserRepo,
    _admin: AdminUser,
) -> UserResponse:
    """Update a user (admin only)."""
    # Check username uniqueness if changing
    if body.username:
        existing = await user_repo.get_by_username(body.username)
        if existing and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{body.username}' already exists.",
            )

    role = UserRole(body.role) if body.role else None
    user = await user_repo.update(
        user_id,
        username=body.username,
        password=body.password,
        display_name=body.display_name,
        role=role,
        is_active=body.is_active,
    )
    return _user_response(user)


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    user_repo: UserRepo,
    admin: AdminUser,
) -> dict:
    """Delete a user (admin only). Cannot delete yourself."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account.",
        )

    await user_repo.delete(user_id)
    return {"message": "User deleted."}
