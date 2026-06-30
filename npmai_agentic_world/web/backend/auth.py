"""
web/backend/auth.py
───────────────────
JWT-based authentication for NPMAI Agentic World website.

User model stored in Supabase `users` table:
  user_id          UUID (PK)
  username         TEXT UNIQUE NOT NULL
  email            TEXT UNIQUE NOT NULL
  password_hash    TEXT NOT NULL
  registered_at    TIMESTAMPTZ DEFAULT now()
  agent_slots      INT DEFAULT 3
  subscription_tier TEXT DEFAULT 'free'   ('free' | 'researcher' | 'admin')
  is_admin         BOOL DEFAULT false
  login_count      INT DEFAULT 0
  last_login_at    TIMESTAMPTZ

Rate-limiting (in-process, Redis-backed when available):
  - Registration: 3 attempts per IP per hour
  - Login:        10 attempts per IP per 10 minutes
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
  
import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── project imports ───────────────────────────────────────────────────────────
from data.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

# ── JWT configuration ─────────────────────────────────────────────────────────
JWT_SECRET: str = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRY_SECONDS: int = int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))  # 24 h

# ── Bearer scheme ─────────────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer(auto_error=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class User:
    """Lightweight user object returned by token verification."""
    user_id: str
    username: str
    email: str
    registered_at: str
    agent_slots: int
    subscription_tier: str
    is_admin: bool


@dataclass
class _RateBucket:
    """Sliding-window rate-limit bucket for one key."""
    timestamps: list = field(default_factory=list)

    def is_allowed(self, window_seconds: int, max_requests: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= max_requests:
            return False
        self.timestamps.append(now)
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# In-process rate limiter (falls back gracefully; swap for Redis in prod)
# ═══════════════════════════════════════════════════════════════════════════════

class _RateLimiter:
    """Thread-safe (asyncio-compatible) sliding-window rate limiter."""

    def __init__(self) -> None:
        self._buckets: dict[str, _RateBucket] = defaultdict(_RateBucket)
        self._lock = asyncio.Lock()

    async def check(
        self,
        key: str,
        window_seconds: int,
        max_requests: int,
        action: str = "action",
    ) -> None:
        async with self._lock:
            bucket = self._buckets[key]
            if not bucket.is_allowed(window_seconds, max_requests):
                retry_after = window_seconds
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Too many {action} attempts. "
                        f"Retry after {retry_after} seconds."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )


_rate_limiter = _RateLimiter()


# ═══════════════════════════════════════════════════════════════════════════════
# Password helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _hash_password(plain: str) -> str:
    """Return bcrypt hash of plain-text password."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ═══════════════════════════════════════════════════════════════════════════════
# JWT helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _create_jwt(user_id: str, email: str, is_admin: bool) -> str:
    """Mint a signed JWT for the given user."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
        "jti": secrets.token_hex(16),  # unique per token
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_jwt(token: str) -> dict:
    """
    Decode and verify a JWT.
    Raises HTTPException 401 on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Supabase table setup
# ═══════════════════════════════════════════════════════════════════════════════

async def ensure_users_table() -> None:
    """
    Create the `users` table in Supabase if it doesn't exist.
    Called once at application startup.
    """
    client = SupabaseClient.get_instance()
    sql = """
    CREATE TABLE IF NOT EXISTS users (
        user_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        username         TEXT UNIQUE NOT NULL,
        email            TEXT UNIQUE NOT NULL,
        password_hash    TEXT NOT NULL,
        registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        agent_slots      INT NOT NULL DEFAULT 3,
        subscription_tier TEXT NOT NULL DEFAULT 'free',
        is_admin         BOOLEAN NOT NULL DEFAULT false,
        login_count      INT NOT NULL DEFAULT 0,
        last_login_at    TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
    CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
    """
    try:
        await client.execute_sql(sql)
        logger.info("users table ready")
    except Exception as exc:
        logger.warning("Could not ensure users table: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Core auth functions
# ═══════════════════════════════════════════════════════════════════════════════

async def register(
    username: str,
    email: str,
    password: str,
    ip_address: str = "unknown",
) -> str:
    """
    Register a new user.

    Parameters
    ----------
    username    : display name (3–32 chars, alphanumeric + _ -)
    email       : valid email address
    password    : plain-text password (min 8 chars, hashed server-side)
    ip_address  : caller IP for rate limiting

    Returns
    -------
    Signed JWT string.

    Raises
    ------
    HTTPException 400  – validation / duplicate user
    HTTPException 429  – rate limit hit
    HTTPException 500  – database error
    """
    # ── rate limit: 3 registrations per IP per hour ───────────────────────────
    await _rate_limiter.check(
        key=f"register:{ip_address}",
        window_seconds=3600,
        max_requests=3,
        action="registration",
    )

    # ── validate inputs ───────────────────────────────────────────────────────
    username = username.strip()
    email = email.strip().lower()
    password = password.strip()

    if not (3 <= len(username) <= 32):
        raise HTTPException(400, "Username must be 3–32 characters.")
    if not all(c.isalnum() or c in "-_" for c in username):
        raise HTTPException(400, "Username may only contain letters, digits, - and _.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "Invalid email address.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    # ── hash password ─────────────────────────────────────────────────────────
    password_hash = _hash_password(password)
    user_id = str(uuid.uuid4())

    # ── insert into Supabase ──────────────────────────────────────────────────
    client = SupabaseClient.get_instance()
    try:
        await client.insert(
            "users",
            {
                "user_id": user_id,
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "agent_slots": 3,
                "subscription_tier": "free",
                "is_admin": False,
            },
        )
    except Exception as exc:
        err_str = str(exc).lower()
        if "unique" in err_str or "duplicate" in err_str:
            if "email" in err_str:
                raise HTTPException(400, "An account with that email already exists.")
            if "username" in err_str:
                raise HTTPException(400, "That username is already taken.")
        logger.exception("Registration DB error: %s", exc)
        raise HTTPException(500, "Registration failed. Please try again.")

    token = _create_jwt(user_id=user_id, email=email, is_admin=False)
    logger.info("New user registered: %s (%s)", username, user_id)
    return token


async def login(email: str, password: str, ip_address: str = "unknown") -> str:
    """
    Authenticate a user by email + password.

    Parameters
    ----------
    email       : registered email address
    password    : plain-text password
    ip_address  : caller IP for rate limiting

    Returns
    -------
    Signed JWT string.

    Raises
    ------
    HTTPException 401  – bad credentials
    HTTPException 429  – rate limit hit
    HTTPException 500  – database error
    """
    # ── rate limit: 10 login attempts per IP per 10 minutes ──────────────────
    await _rate_limiter.check(
        key=f"login:{ip_address}",
        window_seconds=600,
        max_requests=10,
        action="login",
    )

    email = email.strip().lower()

    client = SupabaseClient.get_instance()
    try:
        rows = await client.select("users", filters={"email": email})
    except Exception as exc:
        logger.exception("Login DB error: %s", exc)
        raise HTTPException(500, "Login failed. Please try again.")

    # Always do a password check to prevent timing attacks
    _DUMMY_HASH = "$2b$12$DummyHashUsedToPreventTimingAttacksOnMissingAccounts00000"
    if not rows:
        bcrypt.checkpw(b"dummy", _DUMMY_HASH.encode())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_row = rows[0]
    if not _verify_password(password, user_row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── update login metadata ─────────────────────────────────────────────────
    try:
        await client.update(
            "users",
            filters={"user_id": user_row["user_id"]},
            data={
                "login_count": user_row.get("login_count", 0) + 1,
                "last_login_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        pass  # non-critical

    token = _create_jwt(
        user_id=user_row["user_id"],
        email=user_row["email"],
        is_admin=user_row.get("is_admin", False),
    )
    logger.info("User logged in: %s", user_row["user_id"])
    return token


async def verify_token(token: str) -> str:
    """
    Verify a JWT and return the user_id (``sub`` claim).

    Parameters
    ----------
    token : raw JWT string (without "Bearer " prefix)

    Returns
    -------
    user_id string.

    Raises
    ------
    HTTPException 401 on any failure.
    """
    payload = _decode_jwt(token)
    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim.",
        )
    return user_id


async def get_user_by_id(user_id: str) -> Optional[User]:
    """
    Fetch full user record from Supabase by user_id.

    Returns None if not found.
    """
    client = SupabaseClient.get_instance()
    try:
        rows = await client.select("users", filters={"user_id": user_id})
    except Exception as exc:
        logger.exception("get_user_by_id error: %s", exc)
        return None

    if not rows:
        return None

    row = rows[0]
    return User(
        user_id=row["user_id"],
        username=row["username"],
        email=row["email"],
        registered_at=row.get("registered_at", ""),
        agent_slots=row.get("agent_slots", 3),
        subscription_tier=row.get("subscription_tier", "free"),
        is_admin=row.get("is_admin", False),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI dependency helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> User:
    """
    FastAPI dependency — extracts & validates Bearer token, returns User.

    Usage
    -----
    @app.get("/api/protected")
    async def protected(user: User = Depends(get_current_user)):
        ...
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = await verify_token(credentials.credentials)
    user = await get_user_by_id(user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account deleted.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[User]:
    """
    FastAPI dependency — returns User if token present & valid, else None.
    Useful for endpoints that behave differently for authenticated users.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """
    FastAPI dependency — requires is_admin == True.

    Usage
    -----
    @app.post("/api/divine/send")
    async def divine_send(admin: User = Depends(require_admin)):
        ...
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return user


def get_client_ip(request: Request) -> str:
    """
    Extract real client IP from request, respecting X-Forwarded-For header
    set by reverse proxies (nginx, Cloudflare, etc.).
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
