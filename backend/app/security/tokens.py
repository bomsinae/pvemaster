from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Any
from uuid import UUID, uuid4

import jwt

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import User


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_epoch: int
    session_id: UUID
    assurance_level: str
    mfa_authenticated_at: datetime | None


class TokenManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._key = settings.app_secret_key.get_secret_value()
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
        self.refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)

    def create_access_token(
        self,
        user: User,
        *,
        session_id: UUID,
        assurance_level: str = "PASSWORD",
        mfa_authenticated_at: datetime | None = None,
    ) -> tuple[str, int]:
        now = datetime.now(UTC)
        ttl_seconds = int(self._access_ttl.total_seconds())
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "type": "access",
            "epoch": user.session_epoch,
            "sid": str(session_id),
            "role": user.role,
            "aal": assurance_level,
            "mfa_at": (
                int(mfa_authenticated_at.timestamp()) if mfa_authenticated_at is not None else None
            ),
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + self._access_ttl,
            "iss": self._issuer,
            "aud": self._audience,
        }
        return jwt.encode(payload, self._key, algorithm="HS256"), ttl_seconds

    def decode_access_token(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["sub", "type", "epoch", "sid", "exp", "iat"]},
            )
            if payload["type"] != "access":
                raise jwt.InvalidTokenError
            return AccessClaims(
                user_id=UUID(str(payload["sub"])),
                session_epoch=int(payload["epoch"]),
                session_id=UUID(str(payload["sid"])),
                assurance_level=str(payload.get("aal", "PASSWORD")),
                mfa_authenticated_at=(
                    datetime.fromtimestamp(int(payload["mfa_at"]), tz=UTC)
                    if payload.get("mfa_at") is not None
                    else None
                ),
            )
        except (ValueError, TypeError, jwt.InvalidTokenError) as exc:
            raise AppError(
                status_code=401,
                code="INVALID_ACCESS_TOKEN",
                message="The access token is invalid or expired.",
            ) from exc

    def create_step_up_token(
        self,
        user: User,
        *,
        action: str,
        challenge_id: UUID,
    ) -> tuple[str, int]:
        now = datetime.now(UTC)
        ttl = timedelta(seconds=self._settings.step_up_ttl_seconds)
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "type": "step_up",
            "epoch": user.session_epoch,
            "action": action,
            "challenge_id": str(challenge_id),
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + ttl,
            "iss": self._issuer,
            "aud": self._audience,
        }
        return jwt.encode(payload, self._key, algorithm="HS256"), int(ttl.total_seconds())

    def verify_step_up_token(self, token: str, *, user: User, action: str) -> None:
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["sub", "type", "epoch", "action", "exp", "iat"]},
            )
            if (
                payload["type"] != "step_up"
                or UUID(str(payload["sub"])) != user.id
                or int(payload["epoch"]) != user.session_epoch
                or str(payload["action"]) != action
            ):
                raise jwt.InvalidTokenError
        except (ValueError, TypeError, jwt.InvalidTokenError) as exc:
            raise AppError(
                status_code=403,
                code="STEP_UP_REQUIRED",
                message="Recent MFA verification is required for this action.",
            ) from exc

    @staticmethod
    def create_refresh_secret() -> str:
        return token_urlsafe(48)

    @staticmethod
    def hash_refresh_secret(secret: str) -> bytes:
        return sha256(secret.encode()).digest()
