"""Authentication API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Depends, status, Request

from ..models import TokenResponse, UserInfo
from ..auth.auth_service import (
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VIEWER_SUBJECT,
    authenticate_user,
    create_access_token,
)
from ..auth.dependencies import get_current_user
from ..auth.rate_limit import login_rate_limiter
from ..config import get_settings, normalize_netid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _raise_if_limited(ip_key: str, netid_key: str) -> None:
    settings = get_settings()
    window = settings.login_failure_window_seconds
    retry_after = max(
        login_rate_limiter.retry_after(ip_key, settings.login_max_failures_per_ip, window) or 0,
        login_rate_limiter.retry_after(netid_key, settings.login_max_failures_per_netid, window) or 0,
    )
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


@router.post("/login", response_model=TokenResponse)
async def login(request: Request):
    """
    Login with NetID and the team password.

    Accepts either JSON {"username": "..", "password": ".."} or form data (x-www-form-urlencoded).

    Returns JWT access token.
    """
    content_type = request.headers.get("content-type", "")
    try:
        body = await request.json() if content_type.startswith("application/json") else await request.form()
    except ValueError:
        body = None
    if not hasattr(body, "get"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed login request")
    username = body.get("username")
    password = body.get("password")

    if not username or not password or not isinstance(username, str) or not isinstance(password, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="NetID and password are required",
        )

    netid = normalize_netid(username)
    ip_key = f"ip:{_client_ip(request)}"
    netid_key = f"netid:{netid}"
    _raise_if_limited(ip_key, netid_key)

    user = authenticate_user(netid, password)
    if not user:
        login_rate_limiter.record_failure(ip_key)
        login_rate_limiter.record_failure(netid_key)
        logger.warning(f"[LOGIN] Failed login for netid={netid}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect NetID or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    login_rate_limiter.reset(netid_key)
    logger.info(f"[LOGIN] Successful login for netid={netid}")

    access_token = create_access_token(data={"sub": user["username"], "role": ROLE_OPERATOR})
    return TokenResponse(access_token=access_token, token_type="bearer")


@router.post("/login-viewer", response_model=TokenResponse)
async def login_viewer():
    """
    Login as a viewer with read-only access.

    Returns JWT access token with viewer role.
    """
    access_token = create_access_token(data={"sub": VIEWER_SUBJECT, "role": ROLE_VIEWER})
    return TokenResponse(access_token=access_token, token_type="bearer")


@router.get("/me", response_model=UserInfo)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current user information.
    """
    return UserInfo(
        username=current_user["username"],
        email=current_user.get("email"),
        full_name=current_user.get("full_name"),
        role=current_user.get("role"),
    )
