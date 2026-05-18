"""Secrets storage layer.

The :mod:`.crypto` submodule provides Fernet-based encrypt/decrypt over the
key resolved by :class:`~agentprdiff_studio.settings.Settings`. The CRUD API
in :mod:`agentprdiff_studio.api.secrets` is the public surface; everything
else inside Studio reaches for :func:`load_env_for_run` to materialize the
environment a run subprocess should see.
"""

from .crypto import CryptoError, decrypt, encrypt
from .resolve import load_env_for_run

__all__ = ["encrypt", "decrypt", "CryptoError", "load_env_for_run"]
