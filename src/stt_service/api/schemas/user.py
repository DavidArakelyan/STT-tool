"""Pydantic schemas for user management."""

from datetime import datetime
from pydantic import BaseModel, Field


class UserLogin(BaseModel):
    """Login request."""
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class UserCreate(BaseModel):
    """Create user request."""
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)
    display_name: str | None = Field(None, max_length=200)
    role: str = Field("user", pattern="^(admin|user)$")


class UserUpdate(BaseModel):
    """Update user request."""
    username: str | None = Field(None, min_length=1, max_length=100)
    password: str | None = Field(None, min_length=1)
    display_name: str | None = Field(None, max_length=200)
    role: str | None = Field(None, pattern="^(admin|user)$")
    is_active: bool | None = None


class UserResponse(BaseModel):
    """User response (no password)."""
    user_id: str
    username: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    """Login response with token."""
    token: str
    user: UserResponse


class UserListResponse(BaseModel):
    """List of users."""
    users: list[UserResponse]
    total: int
