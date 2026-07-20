from secrets import token_urlsafe
from uuid import uuid4

from app.security.credentials import CredentialCipher


def test_credential_cipher_encrypts_and_decrypts_secret() -> None:
    application_key = token_urlsafe(48)
    secret = token_urlsafe(32)
    cluster_id = uuid4()
    credential_id = uuid4()
    cipher = CredentialCipher(application_key)

    encrypted = cipher.encrypt(
        secret,
        cluster_id=cluster_id,
        credential_id=credential_id,
    )

    assert secret.encode() not in encrypted.ciphertext
    assert (
        cipher.decrypt(
            encrypted,
            cluster_id=cluster_id,
            credential_id=credential_id,
        )
        == secret
    )
