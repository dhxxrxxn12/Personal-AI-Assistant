"""Bearer-token authentication for Cloud API and Mac Agent routes."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from cloud.settings import settings


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer":
        return token.strip()
    # Useful for non-browser agents and backwards-compatible API clients.
    return request.headers.get("x-api-key", "").strip()


def role_for_token(token: str) -> str | None:
    if not settings.auth_required:
        return "development"
    if settings.admin_token and secrets.compare_digest(token, settings.admin_token):
        return "admin"
    if settings.agent_token and secrets.compare_digest(token, settings.agent_token):
        return "agent"
    return None


def _require(request: Request, role: str) -> str:
    actual = role_for_token(_bearer_token(request))
    shared_token = bool(settings.admin_token and settings.admin_token == settings.agent_token)
    if actual == "development" or actual == role or (role == "agent" and actual == "admin" and shared_token):
        if role == "agent" and settings.auth_required and not request.headers.get("x-jarvis-device-id", "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent device identity header is required",
            )
        return actual
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin(request: Request) -> str:
    return _require(request, "admin")


async def require_agent(request: Request) -> str:
    return _require(request, "agent")


def validate_login_token(token: str) -> str | None:
    return role_for_token(str(token or "").strip())
