from typing import cast

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import MfaMethod, SecurityPolicy, User, UserRole
from app.security.access import Principal
from app.security.tokens import TokenManager


async def admin_mfa_required(session: AsyncSession, settings: Settings) -> bool:
    policy = await session.get(SecurityPolicy, "default")
    return (
        settings.environment == "production"
        or settings.admin_mfa_required
        or bool(policy and policy.admin_mfa_required)
    )


async def require_step_up(
    *,
    request: Request,
    session: AsyncSession,
    principal: Principal,
    action: str,
    step_up_token: str | None,
) -> None:
    """Require an action-bound MFA proof for policy-protected high-risk work."""
    settings = cast(Settings, request.app.state.settings)
    method_count = await session.scalar(
        select(func.count())
        .select_from(MfaMethod)
        .where(MfaMethod.user_id == principal.user_id, MfaMethod.disabled_at.is_(None))
    )
    if principal.role in {UserRole.SUPER_ADMIN, UserRole.OPERATOR}:
        if not await admin_mfa_required(session, settings):
            return
    elif not method_count:
        return
    if not method_count:
        raise AppError(
            status_code=403,
            code="MFA_ENROLLMENT_REQUIRED",
            message="MFA enrollment is required before this administrator action.",
            details={"action": action},
        )
    if not step_up_token:
        raise AppError(
            status_code=403,
            code="STEP_UP_REQUIRED",
            message="Recent MFA verification is required for this action.",
            details={"action": action},
        )
    user = await session.get(User, principal.user_id)
    if user is None:
        raise AppError(401, "AUTHENTICATION_REQUIRED", "A valid access token is required.")
    cast(TokenManager, request.app.state.token_manager).verify_step_up_token(
        step_up_token,
        user=user,
        action=action,
    )
