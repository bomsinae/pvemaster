from secrets import token_urlsafe
from uuid import uuid4

from app.security.credentials import CredentialCipher
from app.security.provisioning_secrets import ProvisioningSecretCipher


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


def test_windows_initial_password_cipher_uses_request_scoped_associated_data() -> None:
    cipher = ProvisioningSecretCipher("test-key-that-is-at-least-32-bytes")
    cluster_id = uuid4()
    request_id = uuid4()
    encrypted = cipher.encrypt(
        "Windows-Initial-Password1!",
        cluster_id=cluster_id,
        request_id=request_id,
    )

    assert encrypted.ciphertext != b"Windows-Initial-Password1!"
    assert (
        cipher.decrypt(
            encrypted,
            cluster_id=cluster_id,
            request_id=request_id,
        )
        == "Windows-Initial-Password1!"
    )
