from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from pipeline import config


def _key() -> bytes:
    # Derived from the admin password so links survive restarts without a
    # second secret to deploy; changing the password revokes every live link.
    return hashlib.sha256(b"beermap-admin-login\x00"
                          + config.ADMIN_PW.encode()).digest()


def _sign(expiry: int) -> str:
    return hmac.new(_key(), str(expiry).encode(), hashlib.sha256).hexdigest()[:32]


def make_token(now: datetime) -> str | None:
    """A signed `<expiry>.<sig>` token, or None while admin auth is disabled."""
    if not config.ADMIN_PW:
        return None
    expiry = int(now.timestamp()) + config.ADMIN_LINK_TTL_S
    return f"{expiry}.{_sign(expiry)}"


def verify_token(token: str, now: datetime) -> bool:
    if not config.ADMIN_PW or not token:
        return False
    expiry_s, _, sig = token.partition(".")
    if not expiry_s.isdigit() or not sig:
        return False
    if int(expiry_s) < now.timestamp():
        return False
    return secrets.compare_digest(sig, _sign(int(expiry_s)))


def token_expiry(token: str) -> int:
    expiry_s = token.partition(".")[0]
    return int(expiry_s) if expiry_s.isdigit() else 0
