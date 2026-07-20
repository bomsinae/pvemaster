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


class TokenManager:
    def __init__(self, settings: Settings) -> None:
        self._key = settings.app_secret_key.get_secret_value()
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._access_ttl = timedelta(seconds=settings.access_token_ttl_seconds)
        self.refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)

    def create_access_token(self, user: User) -> tuple[str, int]:
        now = datetime.now(UTC)
        ttl_seconds = int(self._access_ttl.total_seconds())
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "type": "access",
            "epoch": user.session_epoch,
            "role": user.role,
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
                options={"require": ["sub", "type", "epoch", "exp", "iat"]},
            )
            if payload["type"] != "access":
                raise jwt.InvalidTokenError
            return AccessClaims(
                user_id=UUID(str(payload["sub"])),
                session_epoch=int(payload["epoch"]),
            )
        except (ValueError, TypeError, jwt.InvalidTokenError) as exc:
            raise AppError(
                status_code=401,
                code="INVALID_ACCESS_TOKEN",
                message="The access token is invalid or expired.",
            ) from exc

    @staticmethod
    def create_refresh_secret() -> str:
        return token_urlsafe(48)

    @staticmethod
    def hash_refresh_secret(secret: str) -> bytes:
        return sha256(secret.encode()).digest()
