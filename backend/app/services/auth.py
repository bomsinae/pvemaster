import hmac
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import LoginThrottle, RefreshToken, User, UserRole
from app.schemas.auth import LoginRequest, TokenResponse
from app.security.passwords import PasswordManager
from app.security.tokens import TokenManager
from app.services.audit import add_audit_event


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        passwords: PasswordManager,
        tokens: TokenManager,
    ) -> None:
        self._session = session
        self._settings = settings
        self._passwords = passwords
        self._tokens = tokens

    async def login(
        self,
        request: LoginRequest,
        *,
        source: str,
        request_id: str,
    ) -> TokenResponse:
        now = datetime.now(UTC)
        key_hash = self._throttle_key(request.email, source)
        throttle = await self._session.scalar(
            select(LoginThrottle).where(LoginThrottle.key_hash == key_hash).with_for_update()
        )
        if (
            throttle is not None
            and throttle.locked_until is not None
            and throttle.locked_until > now
        ):
            self._passwords.verify_dummy(request.password.get_secret_value())
            add_audit_event(
                self._session,
                action="AUTH_LOGIN",
                outcome="DENIED",
                request_id=request_id,
                details={"reason": "RATE_LIMITED"},
                error_code="LOGIN_RATE_LIMITED",
            )
            await self._session.commit()
            raise self._rate_limited()

        user = await self._session.scalar(select(User).where(User.email == request.email))
        password = request.password.get_secret_value()
        password_valid = (
            self._passwords.verify(user.password_hash, password) if user is not None else False
        )
        if user is None:
            self._passwords.verify_dummy(password)
        if user is None or not password_valid or not user.is_active:
            locked = self._record_failure(throttle, key_hash, now)
            add_audit_event(
                self._session,
                action="AUTH_LOGIN",
                outcome="DENIED",
                request_id=request_id,
                actor_user_id=user.id if user is not None else None,
                actor_role=UserRole(user.role) if user is not None else None,
                details={"reason": "INVALID_CREDENTIALS"},
                error_code="INVALID_CREDENTIALS",
            )
            await self._session.commit()
            if locked:
                raise self._rate_limited()
            raise self._invalid_credentials()

        if throttle is not None:
            await self._session.delete(throttle)
        user.last_login_at = now
        token_response = self._issue_token_pair(user, now=now)
        add_audit_event(
            self._session,
            action="AUTH_LOGIN",
            outcome="SUCCEEDED",
            request_id=request_id,
            actor_user_id=user.id,
            actor_role=UserRole(user.role),
            target_type="user",
            target_id=user.id,
        )
        await self._session.commit()
        return token_response

    async def refresh(self, raw_token: str, *, request_id: str) -> TokenResponse:
        now = datetime.now(UTC)
        token_hash = self._tokens.hash_refresh_secret(raw_token)
        stored = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        if stored is None or not hmac.compare_digest(stored.token_hash, token_hash):
            raise self._invalid_refresh()
        user = await self._session.scalar(
            select(User).where(User.id == stored.user_id).with_for_update()
        )
        if user is None or not user.is_active:
            raise self._invalid_refresh()
        if stored.revoked_at is not None or stored.replaced_by_id is not None:
            stored.reuse_detected_at = now
            await self._revoke_family(stored.family_id, now)
            user.session_epoch += 1
            add_audit_event(
                self._session,
                action="AUTH_REFRESH_REUSE",
                outcome="DENIED",
                request_id=request_id,
                actor_user_id=user.id,
                actor_role=UserRole(user.role),
                target_type="refresh_token_family",
                target_id=stored.family_id,
            )
            await self._session.commit()
            raise AppError(
                status_code=401,
                code="REFRESH_TOKEN_REUSED",
                message="Refresh token reuse was detected; the session was revoked.",
            )
        if stored.expires_at <= now:
            stored.revoked_at = now
            await self._session.commit()
            raise self._invalid_refresh()

        raw_replacement = self._tokens.create_refresh_secret()
        replacement = RefreshToken(
            id=uuid4(),
            user_id=user.id,
            family_id=stored.family_id,
            token_hash=self._tokens.hash_refresh_secret(raw_replacement),
            parent_id=stored.id,
            expires_at=now + self._tokens.refresh_ttl,
        )
        self._session.add(replacement)
        await self._session.flush()
        stored.revoked_at = now
        stored.replaced_by_id = replacement.id
        access_token, expires_in = self._tokens.create_access_token(user)
        add_audit_event(
            self._session,
            action="AUTH_REFRESH",
            outcome="SUCCEEDED",
            request_id=request_id,
            actor_user_id=user.id,
            actor_role=UserRole(user.role),
            target_type="refresh_token_family",
            target_id=stored.family_id,
        )
        await self._session.commit()
        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_replacement,
            expires_in=expires_in,
        )

    async def logout(self, raw_token: str, *, request_id: str) -> None:
        now = datetime.now(UTC)
        stored = await self._session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == self._tokens.hash_refresh_secret(raw_token)
            )
        )
        if stored is None:
            return
        user = await self._session.get(User, stored.user_id)
        await self._revoke_family(stored.family_id, now)
        if user is not None:
            user.session_epoch += 1
            add_audit_event(
                self._session,
                action="AUTH_LOGOUT",
                outcome="SUCCEEDED",
                request_id=request_id,
                actor_user_id=user.id,
                actor_role=UserRole(user.role),
                target_type="refresh_token_family",
                target_id=stored.family_id,
            )
        await self._session.commit()

    def _issue_token_pair(self, user: User, *, now: datetime) -> TokenResponse:
        refresh_secret = self._tokens.create_refresh_secret()
        family_id = uuid4()
        self._session.add(
            RefreshToken(
                id=uuid4(),
                user_id=user.id,
                family_id=family_id,
                token_hash=self._tokens.hash_refresh_secret(refresh_secret),
                expires_at=now + self._tokens.refresh_ttl,
            )
        )
        access_token, expires_in = self._tokens.create_access_token(user)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_secret,
            expires_in=expires_in,
        )

    def _record_failure(
        self,
        throttle: LoginThrottle | None,
        key_hash: bytes,
        now: datetime,
    ) -> bool:
        from datetime import timedelta

        window = timedelta(seconds=self._settings.login_failure_window_seconds)
        if throttle is None:
            throttle = LoginThrottle(
                key_hash=key_hash,
                failure_count=0,
                window_started_at=now,
            )
            self._session.add(throttle)
        elif now - throttle.window_started_at >= window:
            throttle.failure_count = 0
            throttle.window_started_at = now
            throttle.locked_until = None
        throttle.failure_count += 1
        if throttle.failure_count >= self._settings.login_failure_limit:
            throttle.locked_until = now + timedelta(seconds=self._settings.login_lockout_seconds)
            return True
        return False

    async def _revoke_family(self, family_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def _throttle_key(self, email: str, source: str) -> bytes:
        key = self._settings.app_secret_key.get_secret_value().encode()
        return hmac.new(key, f"{email}\0{source}".encode(), sha256).digest()

    @staticmethod
    def _invalid_credentials() -> AppError:
        return AppError(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="The email or password is invalid.",
        )

    @staticmethod
    def _invalid_refresh() -> AppError:
        return AppError(
            status_code=401,
            code="INVALID_REFRESH_TOKEN",
            message="The refresh token is invalid or expired.",
        )

    @staticmethod
    def _rate_limited() -> AppError:
        return AppError(
            status_code=429,
            code="LOGIN_RATE_LIMITED",
            message="Too many login attempts. Try again later.",
        )
