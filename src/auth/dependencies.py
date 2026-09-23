"""Authentication dependencies for FastAPI."""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .auth_service import ROLE_OPERATOR, decode_access_token, user_from_token_payload

# OAuth2PasswordBearer for Swagger UI integration
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Get current authenticated user from JWT token."""
    user = user_from_token_payload(decode_access_token(token))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_operator(current_user: dict = Depends(get_current_user)) -> dict:
    """Allow only logged-in team members (not view-only sessions)."""
    if current_user.get("role") != ROLE_OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-only users cannot send commands",
        )
    return current_user


async def get_current_user_ws(token: str) -> Optional[str]:
    """
    Get current authenticated user from JWT token for WebSocket connections.

    Returns username on success, None on failure.
    """
    try:
        user = user_from_token_payload(decode_access_token(token))
    except HTTPException:
        return None
    return user["username"] if user else None
