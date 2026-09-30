"""Settings, all from the environment (12-factor); see .env.example for the full list."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    # Postgres. The password comes from the environment, never from a file in the repo.
    db_host: str = field(default_factory=lambda: os.environ.get("DB_HOST", "postgres"))
    db_port: int = field(default_factory=lambda: _int("DB_PORT", 5432))
    db_name: str = field(default_factory=lambda: os.environ.get("DB_NAME", "dragonmail"))
    db_user: str = field(default_factory=lambda: os.environ.get("DB_USER", "dragonmail"))
    db_password: str = field(default_factory=lambda: os.environ.get("DB_PASSWORD", ""))

    images_dir: Path = field(default_factory=lambda: Path(os.environ.get("IMAGES_DIR", "/data/images")))
    # Discord webhook: the full URL. Empty = don't forward.
    discord_webhook_url: str = field(default_factory=lambda: os.environ.get("DISCORD_WEBHOOK_URL", ""))

    max_width: int = field(default_factory=lambda: _int("MAX_WIDTH", 500))
    max_height: int = field(default_factory=lambda: _int("MAX_HEIGHT", 500))
    # A 500x500 RGBA PNG is at most ~1 MB raw; base64 adds a third. Anything bigger is not a drawing.
    max_body_bytes: int = field(default_factory=lambda: _int("MAX_BODY_BYTES", 2_000_000))

    # Rate limits over a sliding window, globally (as the original had) and per client IP.
    rate_period_s: int = field(default_factory=lambda: _int("RATE_PERIOD_S", 3600))
    rate_limit_global: int = field(default_factory=lambda: _int("RATE_LIMIT_GLOBAL", 100))
    rate_limit_per_ip: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_IP", 10))

    # The header carrying the real client IP. Behind the Cloudflare tunnel this is
    # CF-Connecting-IP, which Cloudflare sets at its edge (a client can't forge it
    # through the tunnel). X-Forwarded-For is deliberately NOT trusted: any client
    # can send it.
    client_ip_header: str = field(default_factory=lambda: os.environ.get("CLIENT_IP_HEADER", "CF-Connecting-IP"))
    # Refuse submissions that don't carry a valid client-IP header (D-0006 #4). Without it,
    # every client that bypasses Cloudflare would share the proxy's address as its "IP".
    require_client_ip_header: bool = field(
        default_factory=lambda: os.environ.get("REQUIRE_CLIENT_IP_HEADER", "1").strip() not in ("0", "false", "no"))

    @property
    def conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo
        return make_conninfo(host=self.db_host, port=self.db_port, dbname=self.db_name,
                             user=self.db_user, password=self.db_password)
