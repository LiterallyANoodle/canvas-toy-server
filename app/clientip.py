"""Which IP made this request. Bans depend on this being hard to fake."""
from __future__ import annotations

import ipaddress


def client_ip(headers, peer: str | None, trusted_header: str) -> str:
    """The IP from `trusted_header` (CF-Connecting-IP behind the Cloudflare tunnel) when it
    holds one valid address, else the socket peer.

    Never X-Forwarded-For: a client can put anything there. CF-Connecting-IP can't be
    forged through the tunnel because Cloudflare overwrites it at its edge. The one
    exposure is a request that reaches the app without passing Cloudflare (e.g. from
    the LAN straight into Caddy); the compose keeps the app off published ports.
    """
    value = (headers.get(trusted_header) or "").strip() if trusted_header else ""
    if value:
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass                      # malformed: don't trust it, don't store it
    return peer or "0.0.0.0"
