import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from core.config import settings

_ALGORITHM = "HS256"


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT ───────────────────────────────────────────────────────────────────────

def _make_token(payload: dict, expires_delta: timedelta) -> str:
    data = payload.copy()
    data["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(data, settings.secret_key, algorithm=_ALGORITHM)


def create_access_token(user: User) -> str:
    return _make_token(
        {"sub": str(user.id), "username": user.username, "role": user.role, "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user: User) -> str:
    return _make_token(
        {"sub": str(user.id), "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def verify_token(token: str, token_type: str = "access") -> dict:
    """
    Decode and validate a JWT.
    Raises jose.JWTError on any failure (expired, invalid signature, wrong type).
    """
    payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
    if payload.get("type") != token_type:
        raise JWTError(f"Expected token type '{token_type}', got '{payload.get('type')}'")
    return payload


# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    username: str,
    password: str,
    role: str = "user",
    workspace: str = "",
    department: str = "",
    employee_id: str = "",
) -> User:
    user = User(
        id=uuid.uuid4(),
        username=username,
        password_hash=hash_password(password),
        role=role,
        workspace=workspace,
        department=department,
        employee_id=employee_id,
    )
    db.add(user)
    await db.flush()
    return user


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> Optional[User]:
    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ── Admin user management (Phase C.5) ───────────────────────────────────────


async def list_users(
    db: AsyncSession, page: int = 1, page_size: int = 20,
) -> tuple[list[User], int]:
    """List all users with pagination. Returns (users, total_count)."""
    count_result = await db.execute(select(func.count()).select_from(User))
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    result = await db.execute(
        select(User)
        .order_by(User.created_at)
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    password: Optional[str] = None,
    department: Optional[str] = None,
    employee_id: Optional[str] = None,
) -> Optional[User]:
    """Update user fields. Returns the updated user or None if not found."""
    user = await get_user_by_id(db, user_id)
    if not user:
        return None

    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if password is not None:
        user.password_hash = hash_password(password)
    if department is not None:
        user.department = department
    if employee_id is not None:
        user.employee_id = employee_id

    await db.flush()
    return user
