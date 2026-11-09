"""Short-lived signed URLs, for a download a browser makes without our auth header.

A large export has to reach the browser's own download manager, which streams it to disk;
fetching it from the page holds it in memory, and an export with its images can be tens of
gigabytes. The download manager follows a link and cannot send an `Authorization` header,
so the link carries its own proof instead: an HMAC over what it opens and when it expires,
keyed with the server's secret.

A signature is bound to one purpose and one object: a link to one export opens no other,
and nothing else this server signs can be replayed as one.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(secret: str, purpose: str, subject: str, expires: int) -> str:
    message = f"{purpose}\n{subject}\n{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signed_query(secret: str, purpose: str, subject: str, ttl_seconds: int) -> dict[str, str]:
    """`expires` and `signature` query parameters valid for `ttl_seconds` from now."""
    expires = int(time.time()) + ttl_seconds
    return {"expires": str(expires), "signature": sign(secret, purpose, subject, expires)}


def verify(secret: str, purpose: str, subject: str, expires: int, signature: str) -> bool:
    """Whether `signature` is this server's for `subject`, and has not expired."""
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(sign(secret, purpose, subject, expires), signature)
