"""Recognising a team server, for the desktop application.

The desktop window normally shows this computer's server. A person whose team runs a
shared one can switch the window to it; before the shell does, the page asks this server
whether the address they typed is one (`POST /local/team-server-check`), and this module
answers in a sentence they can act on.

It fetches the address's health check (`GET /api/v1/health`) with **nothing of this
installation's** — no credential, no cookie — and with the operating system's certificate
store (`truststore`), so a team server signed by a company's own authority, which the
webview will trust, is trusted here too.
"""

from __future__ import annotations

import ssl
from urllib.parse import urlsplit

import httpx

from curvevision.schemas.system import TeamServerCheckOut

#: How long a person waits before being told nothing answered.
TIMEOUT_SECONDS = 10.0

#: Replaced in tests; `None` means the network.
TRANSPORT: httpx.AsyncBaseTransport | None = None


def origin_of(typed: str) -> str | None:
    """The origin a person meant, or `None` when what they typed is not a web address.

    A missing scheme means https; a path is dropped, because the application is served from
    the root of its origin; an address with a user name or password in it is refused,
    because the desktop shell would store it in plain text.
    """
    text = typed.strip()
    if not text:
        return None
    if "://" not in text:
        head = text.split("/", 1)[0]
        if ":" in head and not head.rsplit(":", 1)[1].isdigit():
            return None  # `javascript:…`, `mailto:…`: a scheme, not a host and a port
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}[scheme]
    return f"{scheme}://{host}" + (f":{port}" if port not in (None, default) else "")


def _ssl_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:  # pragma: no cover - a required dependency; certifi is the fallback
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


async def check(
    typed: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> TeamServerCheckOut:
    """Ask `typed` whether it is a CurveVision team server the desktop window can show."""
    origin = origin_of(typed)
    if origin is None:
        return refused(f"“{typed.strip()}” is not a web address. Try https://cv.example.com.")

    try:
        async with httpx.AsyncClient(
            transport=transport or TRANSPORT,
            verify=_ssl_context(),
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            trust_env=True,
        ) as client:
            response = await client.get(f"{origin}/api/v1/health")
    except httpx.HTTPError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "certificate" in str(exc).lower():
            return refused(
                f"{origin} answered with a certificate this computer does not trust. If your "
                "organisation issues its own, ask whoever runs the server how to install it."
            )
        return refused(f"Could not reach {origin}. Check the address and your network.")

    try:
        body = response.json()
    except ValueError:
        body = None
    if not (
        isinstance(body, dict)
        and isinstance(body.get("status"), str)
        and isinstance(body.get("version"), str)
        and isinstance(body.get("local_mode"), bool)
    ):
        return refused(f"{origin} answered, but it is not a CurveVision server.")

    # A redirect (http to https, most often) is followed, and where it ended is the server.
    served = origin_of(str(response.url)) or origin
    if body["local_mode"]:
        return refused(f"{served} is another computer's desktop installation, not a team server.")
    if body["status"] != "ok":
        return refused(
            f"{served} is a CurveVision server, but its database is unavailable. Try again "
            "later, or tell whoever runs it."
        )
    return TeamServerCheckOut(ok=True, url=served, version=body["version"])


def refused(reason: str) -> TeamServerCheckOut:
    return TeamServerCheckOut(ok=False, reason=reason)
