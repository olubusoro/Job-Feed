"""
Authentication helpers: password hashing, session cookie management.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

# bcrypt password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# In-memory session store {token: (user_id, expires_at)}
# In production with multiple workers, swap for Redis or DB-backed sessions.
_sessions: dict[str, tuple[int, datetime]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Return bcrypt hash of *password*."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return pwd_context.verify(plain, hashed)


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────


def create_session(user_id: int) -> str:
    """Create a new session token for *user_id* and store it."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(seconds=settings.SESSION_MAX_AGE)
    _sessions[token] = (user_id, expires_at)
    return token


def delete_session(token: str) -> None:
    """Invalidate a session token."""
    _sessions.pop(token, None)


def get_user_id_from_session(token: str) -> Optional[int]:
    """Return user_id for a valid, non-expired session token, or None."""
    entry = _sessions.get(token)
    if entry is None:
        return None
    user_id, expires_at = entry
    if datetime.utcnow() > expires_at:
        _sessions.pop(token, None)
        return None
    return user_id


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI dependencies
# ─────────────────────────────────────────────────────────────────────────────


def get_current_user(
    request: Request,
    session_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    FastAPI dependency that returns the logged-in User or None.
    Use *require_user* when the endpoint must be authenticated.
    """
    if not session_token:
        return None
    user_id = get_user_id_from_session(session_token)
    if user_id is None:
        return None
    return db.get(User, user_id)


def require_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Raise 401 if not logged in."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def require_admin(
    current_user: User = Depends(require_user),
) -> User:
    """Raise 403 if not an admin."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return current_user
