"""JWT authentication helpers for the web dashboard."""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration -- all secrets MUST be provided via environment variables
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("NETAGENT_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError(
        "NETAGENT_SECRET_KEY environment variable is required. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("NETAGENT_TOKEN_EXPIRE_MIN", "60"))

# Admin credentials -- password MUST be set via environment variable
ADMIN_USERNAME = os.environ.get("NETAGENT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("NETAGENT_ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    raise RuntimeError(
        "NETAGENT_ADMIN_PASSWORD environment variable is required. "
        "The application will not start with default credentials."
    )

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return _pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify *plain* against a bcrypt *hashed* value."""
    return _pwd_ctx.verify(plain, hashed)


# Pre-compute admin hash once at import time so we can compare later.
_admin_hash: Optional[str] = os.environ.get("NETAGENT_ADMIN_HASH")
if not _admin_hash:
    _admin_hash = hash_password(ADMIN_PASSWORD)

# ---------------------------------------------------------------------------
# Token creation / verification
# ---------------------------------------------------------------------------


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT containing *data* with an expiry claim."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and verify a JWT.  Returns the payload dict or raises
    an ``HTTPException`` with a 401 status code.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> dict:
    """FastAPI dependency that extracts and validates the JWT from the
    ``Authorization: Bearer <token>`` header.

    Returns the decoded payload dict (contains at least ``sub``).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials)


# ---------------------------------------------------------------------------
# Login helper
# ---------------------------------------------------------------------------


def authenticate_admin(username: str, password: str) -> Optional[str]:
    """Validate admin credentials and return a JWT on success, or ``None``
    if the credentials are invalid.
    """
    if username != ADMIN_USERNAME:
        return None
    if not verify_password(password, _admin_hash):
        return None
    token = create_access_token({"sub": username, "role": "admin"})
    return token
