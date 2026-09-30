"""Postgres access: numbered SQL migrations and the drawings repository.

Every query is parameterized; no value is ever formatted into SQL text.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from importlib import resources

from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("dragonmail.db")


@dataclass(frozen=True)
class Drawing:
    id: uuid.UUID
    number: int
    created_at: datetime


async def migrate(pool: AsyncConnectionPool) -> list[str]:
    """Apply app/migrations/NNN_*.sql not yet applied, in order, each in its own
    transaction. Returns the names applied. A lock keeps two starting replicas apart."""
    files = sorted(f for f in resources.files("app.migrations").iterdir() if f.name.endswith(".sql"))
    applied: list[str] = []
    async with pool.connection() as conn:
        # Autocommit, so each conn.transaction() below is a real transaction (not a
        # savepoint inside one big implicit transaction). Restored before the connection
        # goes back to the pool.
        await conn.set_autocommit(True)
        await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                           "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        await conn.execute("SELECT pg_advisory_lock(hashtext('dragonmail-migrate'))")
        try:
            for f in files:
                cur = await conn.execute("SELECT 1 FROM schema_migrations WHERE name = %s", (f.name,))
                if await cur.fetchone():
                    continue
                async with conn.transaction():
                    await conn.execute(f.read_text())
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
                log.info("applied migration %s", f.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('dragonmail-migrate'))")
            await conn.set_autocommit(False)
    return applied


class Drawings:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def add(self, drawing_id: uuid.UUID, ip: str, created_at: datetime) -> int:
        """Insert and return the new gallery number."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO drawings (id, ip, created_at) VALUES (%s, %s, %s) RETURNING number",
                (drawing_id, ip, created_at))
            row = await cur.fetchone()
            return int(row[0])

    async def by_number(self, number: int) -> Drawing | None:
        """A visible drawing by gallery number, or None."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, number, created_at FROM drawings WHERE number = %s AND NOT hidden",
                (number,))
            row = await cur.fetchone()
            return Drawing(*row) if row else None

    async def exists_visible(self, drawing_id: uuid.UUID) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM drawings WHERE id = %s AND NOT hidden", (drawing_id,))
            return await cur.fetchone() is not None

    async def ping(self) -> bool:
        try:
            async with self.pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False
