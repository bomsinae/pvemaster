import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: str


class CredentialCipher:
    key_version = "v1"

    def __init__(self, application_key: str) -> None:
        self._key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"pvemaster-pve-credentials-v1",
            info=b"cluster-api-token",
        ).derive(application_key.encode("utf-8"))

    def encrypt(self, secret: str, *, cluster_id: UUID, credential_id: UUID) -> EncryptedCredential:
        nonce = os.urandom(12)
        associated_data = self._associated_data(cluster_id, credential_id, self.key_version)
        ciphertext = AESGCM(self._key).encrypt(nonce, secret.encode("utf-8"), associated_data)
        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self.key_version,
        )

    def decrypt(
        self,
        encrypted: EncryptedCredential,
        *,
        cluster_id: UUID,
        credential_id: UUID,
    ) -> str:
        associated_data = self._associated_data(
            cluster_id,
            credential_id,
            encrypted.key_version,
        )
        plaintext = AESGCM(self._key).decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            associated_data,
        )
        return plaintext.decode("utf-8")

    @staticmethod
    def _associated_data(cluster_id: UUID, credential_id: UUID, key_version: str) -> bytes:
        return f"{cluster_id}:{credential_id}:{key_version}".encode()
