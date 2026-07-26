from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.dependencies import PrincipalDependency, get_db_session
from app.models.auth import SecurityPolicy, UserRole
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginEventListResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MfaChallengeVerifyRequest,
    MfaCodeRequest,
    MfaEnrollmentCompleteResponse,
    MfaMethodListResponse,
    MfaPolicyResponse,
    MfaPolicyUpdateRequest,
    RecoveryCodesResponse,
    RefreshRequest,
    SessionListResponse,
    StepUpStartRequest,
    StepUpStartResponse,
    StepUpTokenResponse,
    StepUpVerifyRequest,
    TokenResponse,
    TotpEnrollmentStartResponse,
    TotpEnrollmentVerifyRequest,
    UserResponse,
    WebAuthnFinishRequest,
    WebAuthnStartRequest,
    WebAuthnStartResponse,
)
from app.security.access import require_service_role
from app.security.mfa import MfaSecretCipher
from app.security.passwords import PasswordManager
from app.security.step_up import admin_mfa_required, require_step_up
from app.security.tokens import TokenManager
from app.services.accounts import AccountService
from app.services.audit import add_audit_event
from app.services.auth import AuthService
from app.services.mfa import MfaService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _auth_service(request: Request, session: AsyncSession) -> AuthService:
    return AuthService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        passwords=cast(PasswordManager, request.app.state.password_manager),
        tokens=cast(TokenManager, request.app.state.token_manager),
    )


def _source(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDependency,
    user_agent: Annotated[str | None, Header()] = None,
) -> LoginResponse:
    return await _auth_service(request, session).login(
        payload,
        source=_source(request),
        user_agent=user_agent,
        request_id=request.state.request_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDependency,
) -> TokenResponse:
    return await _auth_service(request, session).refresh(
        payload.refresh_token.get_secret_value(),
        request_id=request.state.request_id,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    request: Request,
    session: SessionDependency,
) -> Response:
    await _auth_service(request, session).logout(
        payload.refresh_token.get_secret_value(),
        request_id=request.state.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _account_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> AccountService:
    return AccountService(
        session=session,
        principal=principal,
        passwords=cast(PasswordManager, request.app.state.password_manager),
        request_id=request.state.request_id,
    )


@router.get("/me", response_model=UserResponse)
async def me(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> UserResponse:
    return await _account_service(request, session, principal).me()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _account_service(request, session, principal).change_password(payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _mfa_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency | None = None,
) -> MfaService:
    return MfaService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(MfaSecretCipher, request.app.state.mfa_cipher),
        tokens=cast(TokenManager, request.app.state.token_manager),
        request_id=request.state.request_id,
        principal=principal,
    )


@router.post("/mfa/challenges/verify", response_model=TokenResponse)
async def verify_login_mfa(
    payload: MfaChallengeVerifyRequest,
    request: Request,
    session: SessionDependency,
) -> TokenResponse:
    return await _mfa_service(request, session).verify_login(
        challenge_id=payload.challenge_id,
        method_type=payload.method_type,
        code=payload.code,
        credential=payload.credential,
        auth=_auth_service(request, session),
    )


@router.get("/mfa/challenges/{challenge_id}/webauthn-options")
async def login_webauthn_options(
    challenge_id: UUID,
    request: Request,
    session: SessionDependency,
) -> dict[str, object]:
    return await _mfa_service(request, session).webauthn_authentication_options(
        challenge_id,
        purpose="LOGIN",
    )


@router.get("/mfa/methods", response_model=MfaMethodListResponse)
async def list_mfa_methods(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MfaMethodListResponse:
    items, remaining = await _mfa_service(request, session, principal).methods()
    return MfaMethodListResponse(
        items=items,
        recovery_codes_remaining=remaining,
        policy_required=await admin_mfa_required(
            session, cast(Settings, request.app.state.settings)
        )
        and principal.role in {UserRole.SUPER_ADMIN, UserRole.OPERATOR},
    )


@router.post("/mfa/totp/start", response_model=TotpEnrollmentStartResponse)
async def start_totp_enrollment(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> TotpEnrollmentStartResponse:
    return await _mfa_service(request, session, principal).start_totp()


@router.post("/mfa/totp/verify", response_model=MfaEnrollmentCompleteResponse)
async def verify_totp_enrollment(
    payload: TotpEnrollmentVerifyRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MfaEnrollmentCompleteResponse:
    method, codes = await _mfa_service(request, session, principal).verify_totp_enrollment(
        payload.method_id,
        payload.code,
    )
    return MfaEnrollmentCompleteResponse(method=method, recovery_codes=codes)


@router.post("/mfa/webauthn/start", response_model=WebAuthnStartResponse)
async def start_webauthn_enrollment(
    payload: WebAuthnStartRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> WebAuthnStartResponse:
    return await _mfa_service(request, session, principal).start_webauthn(name=payload.name)


@router.post("/mfa/webauthn/finish", response_model=MfaEnrollmentCompleteResponse)
async def finish_webauthn_enrollment(
    payload: WebAuthnFinishRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MfaEnrollmentCompleteResponse:
    method, codes = await _mfa_service(request, session, principal).finish_webauthn(
        payload.challenge_id,
        payload.credential,
    )
    return MfaEnrollmentCompleteResponse(method=method, recovery_codes=codes)


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    payload: MfaCodeRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> RecoveryCodesResponse:
    codes = await _mfa_service(request, session, principal).regenerate_recovery_codes(payload.code)
    return RecoveryCodesResponse(codes=codes)


@router.post("/mfa/methods/{method_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa_method(
    method_id: UUID,
    payload: MfaCodeRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _mfa_service(request, session, principal).disable_method(method_id, payload.code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/step-up/start", response_model=StepUpStartResponse)
async def start_step_up(
    payload: StepUpStartRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> StepUpStartResponse:
    challenge = await _mfa_service(request, session, principal).create_step_up(payload.action)
    methods, _remaining = await _mfa_service(request, session, principal).methods()
    return StepUpStartResponse(
        challenge_id=challenge.id,
        expires_in=cast(Settings, request.app.state.settings).mfa_challenge_ttl_seconds,
        methods=sorted({item.type for item in methods} | {"RECOVERY"}),
    )


@router.post("/step-up/verify", response_model=StepUpTokenResponse)
async def verify_step_up(
    payload: StepUpVerifyRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> StepUpTokenResponse:
    token, expires = await _mfa_service(request, session, principal).verify_step_up(
        challenge_id=payload.challenge_id,
        action=payload.action,
        method_type=payload.method_type,
        code=payload.code,
        credential=payload.credential,
    )
    return StepUpTokenResponse(step_up_token=token, expires_in=expires)


@router.get("/step-up/{challenge_id}/webauthn-options")
async def step_up_webauthn_options(
    challenge_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> dict[str, object]:
    return await _mfa_service(request, session, principal).webauthn_authentication_options(
        challenge_id,
        user_id=principal.user_id,
        purpose_prefix="STEP_UP:",
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SessionListResponse:
    return SessionListResponse(items=await _mfa_service(request, session, principal).sessions())


@router.delete("/sessions/others", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_other_sessions(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _mfa_service(request, session, principal).revoke_other_sessions()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sessions/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    family_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _mfa_service(request, session, principal).revoke_session(family_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/login-events", response_model=LoginEventListResponse)
async def login_events(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> LoginEventListResponse:
    return LoginEventListResponse(
        items=await _mfa_service(request, session, principal).login_events()
    )


@router.get("/mfa/policy", response_model=MfaPolicyResponse)
async def get_mfa_policy(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MfaPolicyResponse:
    require_service_role(principal, UserRole.SUPER_ADMIN)
    return MfaPolicyResponse(
        admin_required=await admin_mfa_required(session, cast(Settings, request.app.state.settings))
    )


@router.put("/mfa/policy", response_model=MfaPolicyResponse)
async def update_mfa_policy(
    payload: MfaPolicyUpdateRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> MfaPolicyResponse:
    require_service_role(principal, UserRole.SUPER_ADMIN)
    settings = cast(Settings, request.app.state.settings)
    if settings.environment == "production" and not payload.admin_required:
        raise AppError(
            409,
            "ADMIN_MFA_REQUIRED_IN_PRODUCTION",
            "Administrator MFA cannot be disabled in production.",
        )
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="MFA_POLICY_WRITE",
        step_up_token=x_step_up_token,
    )
    policy = await session.get(SecurityPolicy, "default", with_for_update=True)
    before = bool(policy and policy.admin_mfa_required)
    if policy is None:
        policy = SecurityPolicy(key="default")
        session.add(policy)
    policy.admin_mfa_required = payload.admin_required
    policy.updated_by_id = principal.user_id
    add_audit_event(
        session,
        action="MFA_POLICY_UPDATED",
        outcome="SUCCEEDED",
        actor_user_id=principal.user_id,
        actor_role=principal.role,
        request_id=request.state.request_id,
        before={"admin_required": before},
        after={"admin_required": payload.admin_required},
    )
    await session.commit()
    return MfaPolicyResponse(admin_required=payload.admin_required)
