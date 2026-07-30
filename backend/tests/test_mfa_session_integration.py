import base64
import hmac
import os
import struct
from datetime import UTC, datetime
from hashlib import sha1
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import (
    AuditLog,
    MfaChallenge,
    MfaMethod,
    RecoveryCode,
    RefreshToken,
    User,
    UserRole,
)
from app.security.passwords import PasswordManager

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _totp(secret: str) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded)
    counter = int(datetime.now(UTC).timestamp() // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


async def _reset(app: FastAPI, password: str) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            RecoveryCode,
            MfaChallenge,
            MfaMethod,
            RefreshToken,
            User,
        ):
            await session.execute(delete(model))
        session.add(
            User(
                email="mfa-admin@example.test",
                display_name="MFA Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=PasswordManager().hash(password),
                is_active=True,
            )
        )
        await session.commit()


async def _password_login(client: AsyncClient, password: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "mfa-admin@example.test", "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_totp_recovery_challenge_replay_and_immediate_session_revoke() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr(os.environ.get("AUTH_TEST_REDIS_URL", "redis://localhost:6379/15")),
        app_secret_key=SecretStr(token_urlsafe(32)),
        admin_mfa_required=True,
    )
    app = create_app(settings)
    password = token_urlsafe(24)
    await _reset(app, password)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            initial = await _password_login(client, password)
            headers = {"Authorization": f"Bearer {initial['access_token']}"}

            started = await client.post("/api/v1/auth/mfa/totp/start", headers=headers)
            assert started.status_code == 200, started.text
            enrollment = started.json()
            verified = await client.post(
                "/api/v1/auth/mfa/totp/verify",
                headers=headers,
                json={
                    "method_id": enrollment["method_id"],
                    "code": _totp(enrollment["secret"]),
                },
            )
            assert verified.status_code == 200, verified.text
            recovery_code = verified.json()["recovery_codes"][0]

            challenged = await _password_login(client, password)
            assert challenged["mfa_required"] is True
            rejected = await client.post(
                "/api/v1/auth/mfa/challenges/verify",
                json={
                    "challenge_id": challenged["challenge_id"],
                    "method_type": "TOTP",
                    "code": "000000",
                },
            )
            assert rejected.status_code == 401
            accepted = await client.post(
                "/api/v1/auth/mfa/challenges/verify",
                json={
                    "challenge_id": challenged["challenge_id"],
                    "method_type": "TOTP",
                    "code": _totp(enrollment["secret"]),
                },
            )
            assert accepted.status_code == 200, accepted.text
            replay = await client.post(
                "/api/v1/auth/mfa/challenges/verify",
                json={
                    "challenge_id": challenged["challenge_id"],
                    "method_type": "TOTP",
                    "code": _totp(enrollment["secret"]),
                },
            )
            assert replay.status_code == 401

            accepted_headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}
            protected_payload = {
                "email": "protected-customer@example.test",
                "display_name": "Protected Customer",
                "role": "CUSTOMER",
                "password": token_urlsafe(24),
            }
            step_up_required = await client.post(
                "/api/v1/admin/users",
                headers=accepted_headers,
                json=protected_payload,
            )
            assert step_up_required.status_code == 403
            assert step_up_required.json()["error"]["code"] == "STEP_UP_REQUIRED"
            step_up = await client.post(
                "/api/v1/auth/step-up/start",
                headers=accepted_headers,
                json={"action": "USER_SECURITY_WRITE"},
            )
            step_up_verified = await client.post(
                "/api/v1/auth/step-up/verify",
                headers=accepted_headers,
                json={
                    "challenge_id": step_up.json()["challenge_id"],
                    "action": "USER_SECURITY_WRITE",
                    "method_type": "TOTP",
                    "code": _totp(enrollment["secret"]),
                },
            )
            assert step_up_verified.status_code == 200, step_up_verified.text
            protected_created = await client.post(
                "/api/v1/admin/users",
                headers={
                    **accepted_headers,
                    "X-Step-Up-Token": step_up_verified.json()["step_up_token"],
                },
                json=protected_payload,
            )
            assert protected_created.status_code == 201, protected_created.text

            recovery_challenge = await _password_login(client, password)
            recovered = await client.post(
                "/api/v1/auth/mfa/challenges/verify",
                json={
                    "challenge_id": recovery_challenge["challenge_id"],
                    "method_type": "RECOVERY",
                    "code": recovery_code,
                },
            )
            assert recovered.status_code == 200, recovered.text
            recovered_headers = {"Authorization": f"Bearer {recovered.json()['access_token']}"}
            reused_challenge = await _password_login(client, password)
            reused = await client.post(
                "/api/v1/auth/mfa/challenges/verify",
                json={
                    "challenge_id": reused_challenge["challenge_id"],
                    "method_type": "RECOVERY",
                    "code": recovery_code,
                },
            )
            assert reused.status_code == 401

            sessions = await client.get("/api/v1/auth/sessions", headers=recovered_headers)
            current = next(item for item in sessions.json()["items"] if item["current"])
            revoked = await client.delete(
                f"/api/v1/auth/sessions/{current['id']}",
                headers=recovered_headers,
            )
            assert revoked.status_code == 204
            immediately_invalid = await client.get("/api/v1/auth/me", headers=recovered_headers)
            assert immediately_invalid.status_code == 401
    finally:
        await app.state.db_engine.dispose()
