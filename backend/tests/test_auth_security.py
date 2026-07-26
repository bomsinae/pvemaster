from secrets import token_urlsafe
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.security.access import Principal, require_service_role
from app.security.mfa import (
    EncryptedMfaSecret,
    MfaSecretCipher,
    generate_totp_secret,
    hash_recovery_code,
    verify_totp,
)
from app.security.passwords import PasswordManager
from app.security.tokens import TokenManager


def test_passwords_use_argon2id() -> None:
    manager = PasswordManager()
    encoded = manager.hash(token_urlsafe(24))

    assert encoded.startswith("$argon2id$")


def test_access_token_round_trip(settings: Settings) -> None:
    user = User(
        id=uuid4(),
        email="user@example.test",
        display_name="User",
        role=UserRole.CUSTOMER.value,
        password_hash="not-used",
        session_epoch=4,
    )
    manager = TokenManager(settings)

    session_id = uuid4()
    encoded, expires_in = manager.create_access_token(user, session_id=session_id)
    claims = manager.decode_access_token(encoded)

    assert claims.user_id == user.id
    assert claims.session_epoch == 4
    assert claims.session_id == session_id
    assert expires_in == settings.access_token_ttl_seconds


def test_refresh_secret_is_only_represented_by_hash() -> None:
    manager = TokenManager(
        Settings(
            _env_file=None,
            database_url=SecretStr("postgresql+asyncpg://user@localhost/test"),
            redis_url=SecretStr("redis://localhost/0"),
            app_secret_key=SecretStr(token_urlsafe(32)),
        )
    )
    secret = manager.create_refresh_secret()

    assert secret.encode() not in manager.hash_refresh_secret(secret)
    assert len(manager.hash_refresh_secret(secret)) == 32


def test_customer_is_denied_admin_service_role() -> None:
    principal = Principal(
        user_id=uuid4(),
        email="customer@example.test",
        role=UserRole.CUSTOMER,
        session_epoch=0,
    )

    with pytest.raises(AppError) as error:
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    assert error.value.status_code == 403


def test_mfa_secret_is_encrypted_with_user_and_method_context(settings: Settings) -> None:
    cipher = MfaSecretCipher(settings.app_secret_key.get_secret_value())
    user_id = uuid4()
    method_id = uuid4()
    encrypted = cipher.encrypt("totp-secret", user_id=user_id, method_id=method_id)

    assert b"totp-secret" not in encrypted.ciphertext
    assert cipher.decrypt(encrypted, user_id=user_id, method_id=method_id) == "totp-secret"
    with pytest.raises(InvalidTag):
        cipher.decrypt(
            EncryptedMfaSecret(
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                key_version=encrypted.key_version,
            ),
            user_id=uuid4(),
            method_id=method_id,
        )


def test_totp_accepts_only_the_current_window() -> None:
    secret = generate_totp_secret()
    at = 1_720_000_000

    def code(counter: int) -> str:
        import base64
        import hmac
        import struct
        from hashlib import sha1

        key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
        digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
        offset = digest[-1] & 15
        value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{value % 1_000_000:06d}"

    assert verify_totp(secret, code(at // 30), at=at)
    assert not verify_totp(secret, code(at // 30 + 3), at=at)
    assert not verify_totp(secret, "not-a-code", at=at)


def test_recovery_codes_are_keyed_one_way_hashes(settings: Settings) -> None:
    code = "ABCD-EFGH-IJKL"
    digest = hash_recovery_code(code, settings.app_secret_key.get_secret_value())

    assert code.encode() not in digest
    assert digest == hash_recovery_code(
        code.lower(),
        settings.app_secret_key.get_secret_value(),
    )


def test_step_up_token_is_bound_to_user_epoch_and_action(settings: Settings) -> None:
    user = User(
        id=uuid4(),
        email="admin@example.test",
        display_name="Administrator",
        role=UserRole.SUPER_ADMIN.value,
        password_hash="not-used",
        session_epoch=2,
    )
    manager = TokenManager(settings)
    token, expires_in = manager.create_step_up_token(
        user,
        action="BACKUP_RESTORE",
        challenge_id=uuid4(),
    )

    manager.verify_step_up_token(token, user=user, action="BACKUP_RESTORE")
    assert expires_in == settings.step_up_ttl_seconds
    with pytest.raises(AppError) as mismatch:
        manager.verify_step_up_token(token, user=user, action="FORCED_STOP")
    assert mismatch.value.code == "STEP_UP_REQUIRED"
    user.session_epoch += 1
    with pytest.raises(AppError):
        manager.verify_step_up_token(token, user=user, action="BACKUP_RESTORE")
