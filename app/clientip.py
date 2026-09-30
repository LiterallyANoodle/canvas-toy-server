"""Which IP made this request. Bans depend on this being hard to fake."""
from __future__ import annotations

import ipaddress


def client_ip(headers, peer: str | None, trusted_header: str, require_header: bool = False) -> str | None:
    """The IP from `trusted_header` (CF-Connecting-IP behind the Cloudflare tunnel) when it
    holds one valid address, else the socket peer.

    Never X-Forwarded-For: a client can put anything there. CF-Connecting-IP can't be
    forged through the tunnel because Cloudflare overwrites it at its edge. The one
    exposure is a request that reaches the app without passing Cloudflare (e.g. from
    the LAN straight into Caddy); the compose keeps the app off published ports, and
    the proxy in front should only accept this header from Cloudflare.

    With `require_header`, a missing or malformed header returns None instead of the
    peer, so callers can refuse rather than lump every such client into the proxy's IP.
    """
    value = (headers.get(trusted_header) or "").strip() if trusted_header else ""
    if value:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass                      # malformed: don't trust it, don't store it
    if require_header:
        return None
    return peer or "0.0.0.0"
