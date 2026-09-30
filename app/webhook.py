"""Forward a saved drawing to the operator's Discord channel."""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("dragonmail.webhook")


async def send_to_discord(url: str, png: bytes, filename: str, content: str,
                          client: httpx.AsyncClient | None = None) -> bool:
    """POST the image to a Discord webhook. True on 2xx. Never raises: a Discord outage
    must not lose a drawing that's already saved. The URL is a secret (it carries the
    webhook token), so it's never logged."""
    if not url:
        return False
    files = {"file": (filename, png, "image/png")}
    data = {"payload_json": json.dumps({"content": content})}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(url, data=data, files=files)
        else:
            r = await client.post(url, data=data, files=files)
    except httpx.HTTPError as exc:
        log.warning("discord webhook failed: %s", type(exc).__name__)
        return False
    if not r.is_success:
        log.warning("discord webhook returned HTTP %s", r.status_code)
    return r.is_success
