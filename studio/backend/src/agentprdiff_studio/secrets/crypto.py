"""Fernet-backed encrypt / decrypt for secret values.

The key is resolved once on first use and cached on the module. Studio
generates and persists a key into ``data_dir/.secret_key`` on first run if
none was supplied via ``STUDIO_SECRET_KEY`` — see :meth:`Settings.resolve_secret_key`.

We deliberately stay with Fernet (AES-128-CBC + HMAC) rather than libsodium
for one reason: it's already in ``cryptography``, which is in our dep tree
for FastAPI. If the threat model grows (multi-tenant, HSM-backed keys, key
rotation), this module is the one place to swap.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from ..settings import get_settings


class CryptoError(RuntimeError):
    """A ciphertext failed to decrypt — tampered, truncated, or wrong key."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    key = get_settings().resolve_secret_key()
    # Fernet accepts bytes or urlsafe str; settings stores str.
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt(value: str) -> bytes:
    """Encrypt ``value`` to a Fernet token. Bytes return type plays nicely
    with SQLAlchemy's ``LargeBinary`` / SQLite ``BLOB`` storage."""
    return _cipher().encrypt(value.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    """Decrypt a Fernet token back to its plaintext string.

    Raises :class:`CryptoError` if the token is malformed or signed with a
    different key.
    """
    try:
        return _cipher().decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("could not decrypt: invalid token or wrong key") from exc


def reset_cipher_cache() -> None:
    """Test hook: drop the cached cipher so settings changes take effect."""
    _cipher.cache_clear()
