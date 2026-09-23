"""Authentication: NetID allowlist + shared team password, JWT sessions, device tokens."""

import hmac
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import bcrypt
import jwt
from fastapi import HTTPException, status

from ..config import DEV_TEAM_PASSWORD, get_settings, normalize_netid

logger = logging.getLogger(__name__)

ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
VIEWER_SUBJECT = "viewer"


@lru_cache(maxsize=1)
def _dev_password_hash() -> bytes:
    return bcrypt.hashpw(DEV_TEAM_PASSWORD.encode("utf-8"), bcrypt.gensalt())


def _team_password_hash() -> bytes:
    settings = get_settings()
    if settings.team_password_hash:
        return settings.team_password_hash.encode("utf-8")
    if settings.is_production:
        # validate_for_environment() stops startup before this can happen
        raise RuntimeError("TEAM_PASSWORD_HASH is not configured")
    return _dev_password_hash()


def verify_team_password(plain_password: str) -> bool:
    """Check a password against the team hash (bcrypt only considers the first 72 bytes)."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8")[:72], _team_password_hash())
    except ValueError as e:
        logger.error(f"Team password hash is malformed: {e}")
        return False


def is_allowed_netid(netid: str) -> bool:
    return normalize_netid(netid) in get_settings().get_allowed_netids()


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return the user for a valid NetID + team password, else None."""
    netid = normalize_netid(username)
    # Always run bcrypt so response time does not reveal whether the NetID is allowed
    password_ok = verify_team_password(password)
    if not is_allowed_netid(netid) or not password_ok:
        return None
    return build_user(netid, ROLE_OPERATOR)


def build_user(username: str, role: str) -> dict:
    if role == ROLE_VIEWER:
        return {"username": VIEWER_SUBJECT, "email": None, "full_name": "View-Only User", "role": ROLE_VIEWER}
    return {"username": username, "email": f"{username}@cornell.edu", "full_name": None, "role": role}


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))

    to_encode = data.copy()
    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify JWT access token."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def user_from_token_payload(payload: dict) -> Optional[dict]:
    """Resolve a decoded token to a user, or None if it is no longer valid.

    Operator tokens are re-checked against the allowlist so removing a NetID
    revokes that person's existing sessions immediately.
    """
    username = payload.get("sub")
    role = payload.get("role")
    if not username:
        return None
    if role == ROLE_VIEWER and username == VIEWER_SUBJECT:
        return build_user(VIEWER_SUBJECT, ROLE_VIEWER)
    if role == ROLE_OPERATOR and is_allowed_netid(username):
        return build_user(username, ROLE_OPERATOR)
    return None


def verify_device_token(device_token: str) -> Optional[str]:
    """Verify device token and return hub ID."""
    if not device_token:
        return None
    candidate = device_token.encode("utf-8")
    hub_id = None
    for token, token_hub_id in get_settings().get_valid_device_tokens().items():
        if hmac.compare_digest(candidate, token.encode("utf-8")):
            hub_id = token_hub_id
    return hub_id
