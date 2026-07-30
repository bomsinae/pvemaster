import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import (
    AuditLog,
    MfaChallenge,
    MfaMethod,
    MfaMethodType,
    RecoveryCode,
    RefreshToken,
    SecurityPolicy,
    User,
    UserRole,
)
from app.schemas.auth import (
    LoginEventResponse,
    MfaMethodResponse,
    SessionResponse,
    TokenResponse,
    TotpEnrollmentStartResponse,
    WebAuthnStartResponse,
)
from app.security.access import Principal
from app.security.mfa import (
    EncryptedMfaSecret,
    MfaSecretCipher,
    generate_totp_secret,
    hash_recovery_code,
    totp_uri,
    verify_totp,
)
from app.security.tokens import TokenManager
from app.services.audit import add_audit_event
from app.services.auth import AuthService


class MfaService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: MfaSecretCipher,
        tokens: TokenManager,
        request_id: str,
        principal: Principal | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._tokens = tokens
        self._request_id = request_id
        self._principal = principal

    async def methods(self) -> tuple[list[MfaMethodResponse], int]:
        principal = self._require_principal()
        methods = (
            await self._session.scalars(
                select(MfaMethod)
                .where(
                    MfaMethod.user_id == principal.user_id,
                    MfaMethod.disabled_at.is_(None),
                )
                .order_by(MfaMethod.enrolled_at)
            )
        ).all()
        remaining = int(
            (
                await self._session.scalar(
                    select(func.count())
                    .select_from(RecoveryCode)
                    .where(
                        RecoveryCode.user_id == principal.user_id,
                        RecoveryCode.used_at.is_(None),
                    )
                )
            )
            or 0
        )
        return [self._method_response(item) for item in methods], remaining

    async def start_totp(self, *, name: str = "Authenticator app") -> TotpEnrollmentStartResponse:
        principal = self._require_principal()
        now = datetime.now(UTC)
        method = MfaMethod(
            id=uuid4(),
            user_id=principal.user_id,
            type=MfaMethodType.TOTP.value,
            name=name,
            disabled_at=now,
        )
        secret = generate_totp_secret()
        encrypted = self._cipher.encrypt(
            secret,
            user_id=principal.user_id,
            method_id=method.id,
        )
        method.secret_ciphertext = encrypted.ciphertext
        method.secret_nonce = encrypted.nonce
        method.key_version = encrypted.key_version
        self._session.add(method)
        await self._session.commit()
        return TotpEnrollmentStartResponse(
            method_id=method.id,
            secret=secret,
            provisioning_uri=totp_uri(
                secret,
                account=principal.email,
                issuer=self._settings.webauthn_rp_name,
            ),
        )

    async def verify_totp_enrollment(
        self,
        method_id: UUID,
        code: str,
    ) -> tuple[MfaMethodResponse, list[str]]:
        principal = self._require_principal()
        method = await self._session.scalar(
            select(MfaMethod)
            .where(
                MfaMethod.id == method_id,
                MfaMethod.user_id == principal.user_id,
                MfaMethod.type == MfaMethodType.TOTP.value,
                MfaMethod.disabled_at.is_not(None),
            )
            .with_for_update()
        )
        if method is None or not self._verify_totp_method(method, code):
            raise self._invalid_mfa()
        now = datetime.now(UTC)
        method.disabled_at = None
        method.enrolled_at = now
        method.last_used_at = now
        recovery_codes = await self._rotate_recovery_codes(principal.user_id)
        self._audit("MFA_ENROLLED", principal.user_id, {"type": method.type})
        await self._session.commit()
        return self._method_response(method), recovery_codes

    async def start_webauthn(self, *, name: str) -> WebAuthnStartResponse:
        principal = self._require_principal()
        try:
            from webauthn import generate_registration_options, options_to_json
            from webauthn.helpers.structs import (
                AuthenticatorSelectionCriteria,
                ResidentKeyRequirement,
                UserVerificationRequirement,
            )
        except ImportError as exc:
            raise AppError(503, "WEBAUTHN_UNAVAILABLE", "WebAuthn is unavailable.") from exc

        options = generate_registration_options(
            rp_id=self._settings.webauthn_rp_id,
            rp_name=self._settings.webauthn_rp_name,
            user_id=principal.user_id.bytes,
            user_name=principal.email,
            user_display_name=principal.email,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        challenge = MfaChallenge(
            user_id=principal.user_id,
            purpose="WEBAUTHN_ENROLL",
            challenge=options.challenge,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.mfa_challenge_ttl_seconds),
            max_attempts=self._settings.mfa_max_attempts,
            context={"name": name},
        )
        self._session.add(challenge)
        await self._session.commit()
        return WebAuthnStartResponse(
            challenge_id=challenge.id,
            options=json.loads(options_to_json(options)),
        )

    async def finish_webauthn(
        self,
        challenge_id: UUID,
        credential: dict[str, object],
    ) -> tuple[MfaMethodResponse, list[str]]:
        principal = self._require_principal()
        challenge = await self._challenge(
            challenge_id,
            user_id=principal.user_id,
            purpose="WEBAUTHN_ENROLL",
        )
        try:
            from webauthn import verify_registration_response

            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge.challenge or b"",
                expected_rp_id=self._settings.webauthn_rp_id,
                expected_origin=self._settings.webauthn_origin,
                require_user_verification=True,
            )
        except Exception as exc:
            await self._failed_attempt(challenge)
            raise self._invalid_mfa() from exc
        method = MfaMethod(
            user_id=principal.user_id,
            type=MfaMethodType.WEBAUTHN.value,
            name=str(challenge.context.get("name") or "Security key")[:120],
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=[],
        )
        challenge.consumed_at = datetime.now(UTC)
        self._session.add(method)
        recovery_codes = await self._rotate_recovery_codes(principal.user_id)
        self._audit("MFA_ENROLLED", principal.user_id, {"type": method.type})
        await self._session.commit()
        return self._method_response(method), recovery_codes

    async def webauthn_authentication_options(
        self,
        challenge_id: UUID,
        *,
        user_id: UUID | None = None,
        purpose: str | None = None,
        purpose_prefix: str | None = None,
    ) -> dict[str, object]:
        challenge = await self._challenge(challenge_id, user_id=user_id, purpose=purpose)
        if purpose_prefix is not None and not challenge.purpose.startswith(purpose_prefix):
            raise self._invalid_mfa()
        methods = (
            await self._session.scalars(
                select(MfaMethod).where(
                    MfaMethod.user_id == challenge.user_id,
                    MfaMethod.type == MfaMethodType.WEBAUTHN.value,
                    MfaMethod.disabled_at.is_(None),
                )
            )
        ).all()
        try:
            from webauthn import generate_authentication_options, options_to_json
            from webauthn.helpers.structs import (
                PublicKeyCredentialDescriptor,
                UserVerificationRequirement,
            )
        except ImportError as exc:
            raise AppError(503, "WEBAUTHN_UNAVAILABLE", "WebAuthn is unavailable.") from exc
        options = generate_authentication_options(
            rp_id=self._settings.webauthn_rp_id,
            challenge=secrets.token_bytes(32),
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=method.credential_id or b"") for method in methods
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        challenge.challenge = options.challenge
        await self._session.commit()
        return cast(dict[str, object], json.loads(options_to_json(options)))

    async def verify_login(
        self,
        *,
        challenge_id: UUID,
        method_type: str,
        code: str | None,
        credential: dict[str, object] | None,
        auth: AuthService,
    ) -> TokenResponse:
        challenge = await self._challenge(challenge_id, purpose="LOGIN")
        user = await self._session.get(User, challenge.user_id, with_for_update=True)
        if user is None or not user.is_active:
            raise self._invalid_mfa()
        await self._verify_factor(
            challenge,
            method_type=method_type,
            code=code,
            credential=credential,
        )
        now = datetime.now(UTC)
        challenge.consumed_at = now
        user.last_login_at = now
        context = challenge.context
        response = auth.issue_mfa_session(
            user,
            now=now,
            source_ip=str(context.get("source_ip") or "") or None,
            user_agent=str(context.get("user_agent") or "") or None,
            device_label=str(context.get("device_label") or "") or None,
        )
        self._audit("AUTH_LOGIN", user.id, {"mfa": True})
        await self._session.commit()
        return response

    async def create_step_up(self, action: str) -> MfaChallenge:
        principal = self._require_principal()
        methods, _remaining = await self.methods()
        if not methods:
            raise AppError(403, "MFA_ENROLLMENT_REQUIRED", "Enroll MFA before continuing.")
        challenge = MfaChallenge(
            user_id=principal.user_id,
            purpose=f"STEP_UP:{action}",
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.mfa_challenge_ttl_seconds),
            max_attempts=self._settings.mfa_max_attempts,
            context={"action": action},
        )
        self._session.add(challenge)
        await self._session.commit()
        return challenge

    async def verify_step_up(
        self,
        *,
        challenge_id: UUID,
        action: str,
        method_type: str,
        code: str | None,
        credential: dict[str, object] | None,
    ) -> tuple[str, int]:
        principal = self._require_principal()
        challenge = await self._challenge(
            challenge_id,
            user_id=principal.user_id,
            purpose=f"STEP_UP:{action}",
        )
        await self._verify_factor(
            challenge,
            method_type=method_type,
            code=code,
            credential=credential,
        )
        challenge.consumed_at = datetime.now(UTC)
        user = await self._session.get(User, principal.user_id)
        if user is None:
            raise self._invalid_mfa()
        token, expires = self._tokens.create_step_up_token(
            user,
            action=action,
            challenge_id=challenge.id,
        )
        self._audit("MFA_STEP_UP", user.id, {"action": action})
        await self._session.commit()
        return token, expires

    async def regenerate_recovery_codes(self, code: str) -> list[str]:
        principal = self._require_principal()
        if not await self._verify_any_code(principal.user_id, code, consume_recovery=True):
            raise self._invalid_mfa()
        codes = await self._rotate_recovery_codes(principal.user_id)
        self._audit("MFA_RECOVERY_CODES_REGENERATED", principal.user_id)
        await self._session.commit()
        return codes

    async def disable_method(self, method_id: UUID, code: str) -> None:
        principal = self._require_principal()
        if not await self._verify_any_code(principal.user_id, code, consume_recovery=True):
            raise self._invalid_mfa()
        method = await self._session.scalar(
            select(MfaMethod)
            .where(
                MfaMethod.id == method_id,
                MfaMethod.user_id == principal.user_id,
                MfaMethod.disabled_at.is_(None),
            )
            .with_for_update()
        )
        if method is None:
            raise AppError(404, "MFA_METHOD_NOT_FOUND", "The MFA method was not found.")
        remaining = await self._session.scalar(
            select(func.count())
            .select_from(MfaMethod)
            .where(
                MfaMethod.user_id == principal.user_id,
                MfaMethod.disabled_at.is_(None),
                MfaMethod.id != method.id,
            )
        )
        policy = await self._session.get(SecurityPolicy, "default")
        policy_required = (
            self._settings.environment == "production"
            or self._settings.admin_mfa_required
            or bool(policy and policy.admin_mfa_required)
        )
        if (
            policy_required
            and principal.role in {UserRole.SUPER_ADMIN, UserRole.OPERATOR}
            and not remaining
        ):
            raise AppError(409, "LAST_MFA_METHOD_REQUIRED", "Administrators must retain MFA.")
        method.disabled_at = datetime.now(UTC)
        user = await self._session.get(User, principal.user_id, with_for_update=True)
        if user is not None:
            user.session_epoch += 1
            await self._session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == principal.user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        self._audit("MFA_DISABLED", principal.user_id, {"type": method.type})
        await self._session.commit()

    async def sessions(self) -> list[SessionResponse]:
        principal = self._require_principal()
        now = datetime.now(UTC)
        rows = (
            await self._session.scalars(
                select(RefreshToken)
                .where(
                    RefreshToken.user_id == principal.user_id,
                    RefreshToken.parent_id.is_(None),
                    RefreshToken.expires_at > now,
                )
                .order_by(RefreshToken.created_at.desc())
            )
        ).all()
        active_families = set(
            (
                await self._session.scalars(
                    select(RefreshToken.family_id).where(
                        RefreshToken.user_id == principal.user_id,
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > now,
                    )
                )
            ).all()
        )
        return [
            SessionResponse(
                id=row.family_id,
                device_label=row.device_label,
                created_ip=row.created_ip,
                user_agent=row.user_agent,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                assurance_level=row.assurance_level,
                current=row.family_id == principal.session_id,
            )
            for row in rows
            if row.family_id in active_families
        ]

    async def revoke_session(self, family_id: UUID) -> None:
        principal = self._require_principal()
        cursor = cast(
            CursorResult[Any],
            await self._session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == principal.user_id,
                    RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        if not cursor.rowcount:
            raise AppError(404, "SESSION_NOT_FOUND", "The session was not found.")
        self._audit("AUTH_SESSION_REVOKED", principal.user_id, {"session_id": str(family_id)})
        await self._session.commit()

    async def revoke_other_sessions(self) -> None:
        principal = self._require_principal()
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == principal.user_id,
                RefreshToken.family_id != principal.session_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        self._audit("AUTH_OTHER_SESSIONS_REVOKED", principal.user_id)
        await self._session.commit()

    async def login_events(self, limit: int = 50) -> list[LoginEventResponse]:
        principal = self._require_principal()
        rows = (
            await self._session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.actor_user_id == principal.user_id,
                    AuditLog.action.in_(
                        ["AUTH_LOGIN", "AUTH_LOGIN_MFA_CHALLENGE", "AUTH_REFRESH_REUSE"]
                    ),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            LoginEventResponse(
                id=row.id,
                created_at=row.created_at,
                outcome=row.result,
                source_ip=row.source_ip,
                user_agent=row.user_agent,
                error_code=row.error_code,
            )
            for row in rows
        ]

    async def _verify_factor(
        self,
        challenge: MfaChallenge,
        *,
        method_type: str,
        code: str | None,
        credential: dict[str, object] | None,
    ) -> None:
        if method_type == "TOTP" and code is not None:
            if await self._verify_any_code(challenge.user_id, code, consume_recovery=False):
                return
        elif method_type == "RECOVERY" and code is not None:
            if await self._verify_recovery(challenge.user_id, code):
                return
        elif method_type == "WEBAUTHN" and credential is not None:
            if await self._verify_webauthn(challenge, credential):
                return
        await self._failed_attempt(challenge)
        raise self._invalid_mfa()

    async def _verify_webauthn(
        self,
        challenge: MfaChallenge,
        credential: dict[str, object],
    ) -> bool:
        raw_id = credential.get("rawId") or credential.get("id")
        if not isinstance(raw_id, str):
            return False
        try:
            credential_id = base64.urlsafe_b64decode(raw_id + "=" * (-len(raw_id) % 4))
        except ValueError:
            return False
        method = await self._session.scalar(
            select(MfaMethod)
            .where(
                MfaMethod.user_id == challenge.user_id,
                MfaMethod.credential_id == credential_id,
                MfaMethod.disabled_at.is_(None),
            )
            .with_for_update()
        )
        if method is None or method.public_key is None or challenge.challenge is None:
            return False
        try:
            from webauthn import verify_authentication_response

            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge.challenge,
                expected_rp_id=self._settings.webauthn_rp_id,
                expected_origin=self._settings.webauthn_origin,
                credential_public_key=method.public_key,
                credential_current_sign_count=method.sign_count,
                require_user_verification=True,
            )
        except Exception:
            return False
        method.sign_count = verified.new_sign_count
        method.last_used_at = datetime.now(UTC)
        return True

    async def _verify_any_code(
        self,
        user_id: UUID,
        code: str,
        *,
        consume_recovery: bool,
    ) -> bool:
        methods = (
            await self._session.scalars(
                select(MfaMethod).where(
                    MfaMethod.user_id == user_id,
                    MfaMethod.type == MfaMethodType.TOTP.value,
                    MfaMethod.disabled_at.is_(None),
                )
            )
        ).all()
        for method in methods:
            if self._verify_totp_method(method, code):
                method.last_used_at = datetime.now(UTC)
                return True
        return await self._verify_recovery(user_id, code) if consume_recovery else False

    def _verify_totp_method(self, method: MfaMethod, code: str) -> bool:
        if (
            method.secret_ciphertext is None
            or method.secret_nonce is None
            or method.key_version is None
        ):
            return False
        secret = self._cipher.decrypt(
            EncryptedMfaSecret(
                ciphertext=method.secret_ciphertext,
                nonce=method.secret_nonce,
                key_version=method.key_version,
            ),
            user_id=method.user_id,
            method_id=method.id,
        )
        return verify_totp(secret, code)

    async def _verify_recovery(self, user_id: UUID, code: str) -> bool:
        digest = hash_recovery_code(
            code,
            self._settings.app_secret_key.get_secret_value(),
        )
        stored = await self._session.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user_id,
                RecoveryCode.code_hash == digest,
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if stored is None:
            return False
        stored.used_at = datetime.now(UTC)
        return True

    async def _rotate_recovery_codes(self, user_id: UUID) -> list[str]:
        await self._session.execute(
            update(RecoveryCode)
            .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        codes = [
            f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}" for _ in range(10)
        ]
        self._session.add_all(
            [
                RecoveryCode(
                    user_id=user_id,
                    code_hash=hash_recovery_code(
                        code,
                        self._settings.app_secret_key.get_secret_value(),
                    ),
                )
                for code in codes
            ]
        )
        return codes

    async def _challenge(
        self,
        challenge_id: UUID,
        *,
        user_id: UUID | None = None,
        purpose: str | None = None,
    ) -> MfaChallenge:
        challenge = await self._session.get(MfaChallenge, challenge_id, with_for_update=True)
        now = datetime.now(UTC)
        if (
            challenge is None
            or (user_id is not None and challenge.user_id != user_id)
            or (purpose is not None and challenge.purpose != purpose)
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
            or challenge.attempts >= challenge.max_attempts
        ):
            raise self._invalid_mfa()
        return challenge

    async def _failed_attempt(self, challenge: MfaChallenge) -> None:
        challenge.attempts += 1
        self._audit("MFA_CHALLENGE_FAILED", challenge.user_id)
        await self._session.commit()

    def _audit(
        self,
        action: str,
        user_id: UUID,
        details: dict[str, object] | None = None,
    ) -> None:
        add_audit_event(
            self._session,
            action=action,
            outcome="DENIED" if action.endswith("_FAILED") else "SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=user_id,
            actor_role=self._principal.role if self._principal is not None else None,
            target_type="user",
            target_id=user_id,
            details=details,
        )

    def _require_principal(self) -> Principal:
        if self._principal is None:
            raise AppError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        return self._principal

    @staticmethod
    def _method_response(method: MfaMethod) -> MfaMethodResponse:
        return MfaMethodResponse(
            id=method.id,
            type=method.type,
            name=method.name,
            enrolled_at=method.enrolled_at,
            last_used_at=method.last_used_at,
        )

    @staticmethod
    def _invalid_mfa() -> AppError:
        return AppError(401, "INVALID_MFA_CHALLENGE", "The MFA challenge is invalid or expired.")
