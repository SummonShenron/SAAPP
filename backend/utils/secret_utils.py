"""Encrypting secrets (API tokens) before they are stored, with the same TOKEN_ENCRYPTION_KEY the
Google OAuth tokens already use. A stored secret is only ever readable by this server, never by the
database, a backup, or a settings file on disk."""
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretStorageNotConfigured(RuntimeError):
    """TOKEN_ENCRYPTION_KEY is missing or not a valid Fernet key, so nothing can be stored safely."""


def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise SecretStorageNotConfigured("Token storage is not configured. Set TOKEN_ENCRYPTION_KEY.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as error:
        raise SecretStorageNotConfigured("TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from error


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    """The original secret, or None if it can't be decrypted (a changed key, or corrupted data).
    Never raises for bad data, so one unreadable record can't break a request that has a fallback."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
