"""Postgres access: numbered SQL migrations and the drawings, comments and bans repositories.

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
    width: int | None = None          # None: the standard canvas
    height: int | None = None


@dataclass(frozen=True)
class AdminDrawing:
    id: uuid.UUID
    number: int
    created_at: datetime
    ip: str | None                    # None for drawings imported from before the database
    hidden: bool
    comment_count: int


@dataclass(frozen=True)
class Comment:
    id: int
    created_at: datetime
    body: str


@dataclass(frozen=True)
class AdminComment:
    id: int
    drawing_number: int
    created_at: datetime
    body: str
    ip: str
    hidden: bool


@dataclass(frozen=True)
class Ban:
    id: int
    network: str
    scope: str
    reason: str
    created_at: datetime
    expires_at: datetime


BAN_SCOPES = ("all", "draw", "comment")


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

    async def add(self, drawing_id: uuid.UUID, ip: str, created_at: datetime,
                  width: int | None = None, height: int | None = None) -> int:
        """Insert and return the new gallery number."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO drawings (id, ip, created_at, width, height) VALUES (%s, %s, %s, %s, %s)"
                " RETURNING number",
                (drawing_id, ip, created_at, width, height))
            row = await cur.fetchone()
            return int(row[0])

    async def by_number(self, number: int) -> Drawing | None:
        """A visible drawing by gallery number, or None."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, number, created_at, width, height FROM drawings WHERE number = %s AND NOT hidden",
                (number,))
            row = await cur.fetchone()
            return Drawing(*row) if row else None

    async def exists_visible(self, drawing_id: uuid.UUID) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM drawings WHERE id = %s AND NOT hidden", (drawing_id,))
            return await cur.fetchone() is not None

    async def neighbours(self, number: int) -> tuple[int | None, int | None]:
        """The visible drawings just before and just after `number` (gaps skipped)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT (SELECT max(number) FROM drawings WHERE number < %s AND NOT hidden),"
                "       (SELECT min(number) FROM drawings WHERE number > %s AND NOT hidden)",
                (number, number))
            row = await cur.fetchone()
            return (row[0], row[1]) if row else (None, None)

    async def first_number(self) -> int | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT min(number) FROM drawings WHERE NOT hidden")
            row = await cur.fetchone()
            return row[0] if row else None

    # --- admin ---
    async def admin_page(self, limit: int, offset: int) -> list[AdminDrawing]:
        """Newest first, hidden ones included."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT d.id, d.number, d.created_at, host(d.ip), d.hidden,"
                "       (SELECT count(*) FROM comments c WHERE c.drawing_id = d.id)"
                " FROM drawings d ORDER BY d.number DESC LIMIT %s OFFSET %s",
                (limit, offset))
            return [AdminDrawing(*r) for r in await cur.fetchall()]

    async def exists(self, drawing_id: uuid.UUID) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM drawings WHERE id = %s", (drawing_id,))
            return await cur.fetchone() is not None

    async def set_hidden(self, drawing_id: uuid.UUID, hidden: bool) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("UPDATE drawings SET hidden = %s WHERE id = %s", (hidden, drawing_id))
            return cur.rowcount == 1

    async def delete(self, drawing_id: uuid.UUID) -> bool:
        """Deletes the row (its comments go with it). The caller removes the file."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM drawings WHERE id = %s", (drawing_id,))
            return cur.rowcount == 1

    async def import_numbered(self, rows: list[tuple[uuid.UUID, int, datetime, int, int]]) -> None:
        """Insert old drawings (id, number, created_at, width, height) under the given numbers, in one
        transaction, and move the number counter past them. Refuses if any number is taken."""
        numbers = [r[1] for r in rows]
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("LOCK TABLE drawings IN EXCLUSIVE MODE")
                cur = await conn.execute("SELECT number FROM drawings WHERE number = ANY(%s) ORDER BY number",
                                         (numbers,))
                taken = [r[0] for r in await cur.fetchall()]
                if taken:
                    raise ValueError(f"numbers already in use: {taken}")
                for drawing_id, number, created_at, width, height in rows:
                    await conn.execute(
                        "INSERT INTO drawings (id, number, created_at, ip, width, height)"
                        " OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, NULL, %s, %s)",
                        (drawing_id, number, created_at, width, height))
                await conn.execute("SELECT setval(pg_get_serial_sequence('drawings', 'number'),"
                                   " (SELECT max(number) FROM drawings))")

    async def ping(self) -> bool:
        try:
            async with self.pool.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False


class Comments:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def add(self, drawing_id: uuid.UUID, body: str, ip: str, created_at: datetime) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO comments (drawing_id, body, ip, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                (drawing_id, body, ip, created_at))
            row = await cur.fetchone()
            return int(row[0])

    async def for_drawing(self, drawing_id: uuid.UUID) -> list[Comment]:
        """Visible comments, oldest first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, created_at, body FROM comments WHERE drawing_id = %s AND NOT hidden"
                " ORDER BY created_at, id", (drawing_id,))
            return [Comment(*r) for r in await cur.fetchall()]

    async def admin_page(self, limit: int, offset: int) -> list[AdminComment]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT c.id, d.number, c.created_at, c.body, host(c.ip), c.hidden"
                " FROM comments c JOIN drawings d ON d.id = c.drawing_id"
                " ORDER BY c.id DESC LIMIT %s OFFSET %s", (limit, offset))
            return [AdminComment(*r) for r in await cur.fetchall()]

    async def set_hidden(self, comment_id: int, hidden: bool) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("UPDATE comments SET hidden = %s WHERE id = %s", (hidden, comment_id))
            return cur.rowcount == 1

    async def delete(self, comment_id: int) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM comments WHERE id = %s", (comment_id,))
            return cur.rowcount == 1


class Bans:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def active_for(self, ip: str, scope: str) -> Ban | None:
        """The longest-running active ban covering `ip` for `scope` ('draw' or 'comment')."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, network::text, scope, reason, created_at, expires_at FROM bans"
                " WHERE network >>= %s::inet AND scope IN ('all', %s)"
                "   AND lifted_at IS NULL AND expires_at > now()"
                " ORDER BY expires_at DESC LIMIT 1", (ip, scope))
            row = await cur.fetchone()
            return Ban(*row) if row else None

    async def add(self, network: str, scope: str, expires_at: datetime, reason: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO bans (network, scope, expires_at, reason) VALUES (%s::cidr, %s, %s, %s)"
                " RETURNING id", (network, scope, expires_at, reason))
            row = await cur.fetchone()
            return int(row[0])

    async def active(self) -> list[Ban]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, network::text, scope, reason, created_at, expires_at FROM bans"
                " WHERE lifted_at IS NULL AND expires_at > now() ORDER BY expires_at")
            return [Ban(*r) for r in await cur.fetchall()]

    async def lift(self, ban_id: int) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE bans SET lifted_at = now() WHERE id = %s AND lifted_at IS NULL", (ban_id,))
            return cur.rowcount == 1
