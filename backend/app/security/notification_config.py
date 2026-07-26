import json
import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


@dataclass(frozen=True)
class EncryptedNotificationConfig:
    ciphertext: bytes
    nonce: bytes
    key_version: str


class NotificationConfigCipher:
    key_version = "v1"

    def __init__(self, application_key: str) -> None:
        self._key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"pvemaster-notification-config-v1",
            info=b"notification-channel-secret",
        ).derive(application_key.encode())

    def encrypt(
        self, config: dict[str, object], *, channel_id: UUID
    ) -> EncryptedNotificationConfig:
        nonce = os.urandom(12)
        return EncryptedNotificationConfig(
            ciphertext=AESGCM(self._key).encrypt(
                nonce,
                json.dumps(config, separators=(",", ":")).encode(),
                self._aad(channel_id, self.key_version),
            ),
            nonce=nonce,
            key_version=self.key_version,
        )

    def decrypt(
        self, encrypted: EncryptedNotificationConfig, *, channel_id: UUID
    ) -> dict[str, object]:
        raw = AESGCM(self._key).decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            self._aad(channel_id, encrypted.key_version),
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("notification configuration must be an object")
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def _aad(channel_id: UUID, version: str) -> bytes:
        return f"{channel_id}:{version}".encode()
