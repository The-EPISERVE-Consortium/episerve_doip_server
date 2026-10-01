"""Signed, expiring links for restricted components.

A link carries ``exp`` (unix seconds) and ``sig``, the hex HMAC-SHA256 of
``"<QID>\\n<component>\\n<exp>"`` under a secret shared between the party that
creates the link (the CKAN theme) and the DOIP server. The signature is bound to
one object and one component; it grants read access to that component only.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def _message(qid: str, component: str, exp: int) -> bytes:
    return f"{qid.upper()}\n{component}\n{int(exp)}".encode("utf-8")


def sign(secret: str, qid: str, component: str, exp: int) -> str:
    """Return the hex signature for a (QID, component, expiry) triple."""
    return hmac.new(secret.encode("utf-8"), _message(qid, component, exp), hashlib.sha256).hexdigest()


def verify(secret: str | None, qid: str, component: str, exp, sig, now: float | None = None) -> bool:
    """Return True if ``sig`` is valid for the triple and ``exp`` is still in the future.

    Args:
        secret: Shared secret; when empty or ``None`` nothing verifies.
        qid: Object identifier (case-insensitive).
        component: Component identifier exactly as requested.
        exp: Expiry as unix seconds (int or numeric string).
        sig: Hex signature presented by the caller.
        now: Current time override for tests.
    """
    if not secret or not isinstance(sig, str) or not sig:
        return False
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig.encode("utf-8"), sign(secret, qid, component, exp_int).encode("utf-8"))
