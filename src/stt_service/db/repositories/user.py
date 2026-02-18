"""User repository for database operations."""

from typing import Any

import bcrypt
from sqlalchemy import func, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from stt_service.db.models import User, UserRole
from stt_service.utils.exceptions import STTServiceError


class UserNotFoundError(STTServiceError):
    """Raised when a user is not found."""
    pass


class UserRepository:
    """Repository for User database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

    async def create(
        self,
        username: str,
        password: str,
        role: UserRole = UserRole.USER,
        display_name: str | None = None,
    ) -> User:
        """Create a new user."""
        user = User(
            username=username,
            password_hash=self.hash_password(password),
            display_name=display_name or username,
            role=role,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: str) -> User:
        """Get user by ID."""
        query = select(User).where(User.id == user_id)
        result = await self.session.execute(query)
        user = result.scalar_one_or_none()
        if not user:
            raise UserNotFoundError(f"User not found: {user_id}")
        return user

    async def get_by_username(self, username: str) -> User | None:
        """Get user by username (returns None if not found)."""
        query = select(User).where(User.username == username)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def authenticate(self, username: str, password: str) -> User | None:
        """Authenticate user by username and password."""
        user = await self.get_by_username(username)
        if not user or not user.is_active:
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        return user

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[User]:
        """List all users."""
        query = (
            select(User)
            .order_by(User.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_users(self) -> int:
        """Count total users."""
        query = select(func.count(User.id))
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def update(
        self,
        user_id: str,
        username: str | None = None,
        password: str | None = None,
        display_name: str | None = None,
        role: UserRole | None = None,
        is_active: bool | None = None,
    ) -> User:
        """Update user fields."""
        updates: dict[str, Any] = {}
        if username is not None:
            updates["username"] = username
        if password is not None:
            updates["password_hash"] = self.hash_password(password)
        if display_name is not None:
            updates["display_name"] = display_name
        if role is not None:
            updates["role"] = role
        if is_active is not None:
            updates["is_active"] = is_active

        if updates:
            stmt = update(User).where(User.id == user_id).values(**updates)
            await self.session.execute(stmt)
            await self.session.flush()

        return await self.get_by_id(user_id)

    async def delete(self, user_id: str) -> None:
        """Delete a user."""
        user = await self.get_by_id(user_id)
        await self.session.delete(user)
        await self.session.flush()
