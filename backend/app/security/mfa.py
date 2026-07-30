import base64
import hmac
import os
import struct
from dataclasses import dataclass
from hashlib import sha1, sha256
from time import time
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


@dataclass(frozen=True)
class EncryptedMfaSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: str


class MfaSecretCipher:
    key_version = "v1"

    def __init__(self, application_key: str) -> None:
        self._key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"pvemaster-mfa-secrets-v1",
            info=b"user-mfa-secret",
        ).derive(application_key.encode())

    def encrypt(self, secret: str, *, user_id: UUID, method_id: UUID) -> EncryptedMfaSecret:
        nonce = os.urandom(12)
        aad = self._aad(user_id, method_id, self.key_version)
        return EncryptedMfaSecret(
            ciphertext=AESGCM(self._key).encrypt(nonce, secret.encode(), aad),
            nonce=nonce,
            key_version=self.key_version,
        )

    def decrypt(
        self,
        encrypted: EncryptedMfaSecret,
        *,
        user_id: UUID,
        method_id: UUID,
    ) -> str:
        return (
            AESGCM(self._key)
            .decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                self._aad(user_id, method_id, encrypted.key_version),
            )
            .decode()
        )

    @staticmethod
    def _aad(user_id: UUID, method_id: UUID, version: str) -> bytes:
        return f"{user_id}:{method_id}:{version}".encode()


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_uri(secret: str, *, account: str, issuer: str) -> str:
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def verify_totp(secret: str, code: str, *, at: int | None = None) -> bool:
    normalized = code.replace(" ", "").replace("-", "")
    if len(normalized) != 6 or not normalized.isdigit():
        return False
    counter = int((at if at is not None else time()) // 30)
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    for drift in (-1, 0, 1):
        digest = hmac.new(key, struct.pack(">Q", counter + drift), sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        expected = f"{binary % 1_000_000:06d}"
        if hmac.compare_digest(expected, normalized):
            return True
    return False


def hash_recovery_code(code: str, application_key: str) -> bytes:
    return hmac.new(application_key.encode(), code.strip().upper().encode(), sha256).digest()
