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
    mod_reason: str = ""


@dataclass(frozen=True)
class Comment:
    id: int
    created_at: datetime
    body: str
    name: str | None = None


@dataclass(frozen=True)
class AdminComment:
    id: int
    drawing_number: int
    created_at: datetime
    body: str
    ip: str
    hidden: bool
    name: str | None = None
    mod_reason: str = ""


@dataclass(frozen=True)
class ModAction:
    at: datetime
    admin: str
    action: str
    target: str
    reason: str


@dataclass(frozen=True)
class Ban:
    id: int
    network: str
    scope: str
    reason: str
    created_at: datetime
    expires_at: datetime
    subject_kind: str = ""            # "comment", "drawing" or "" (a ban typed in by hand)
    subject_text: str = ""            # the comment's text, when it was for a comment
    subject_at: datetime | None = None


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


# Public numbers are positions, not ids (operator, msg 542): No. 1 is always the oldest drawing
# that still exists (hidden ones count; deleted ones don't), so deleting a drawing renumbers the
# ones after it. The `number` column is only an internal tie-breaker now.
RANKED = ("WITH ranked AS (SELECT d.*, row_number() OVER (ORDER BY d.created_at, d.number) AS pos"
          " FROM drawings d) ")


class Drawings:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def add(self, drawing_id: uuid.UUID, ip: str | None, created_at: datetime,
                  width: int | None = None, height: int | None = None) -> int:
        """Insert and return the new drawing's gallery number."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO drawings (id, ip, created_at, width, height) VALUES (%s, %s, %s, %s, %s)",
                (drawing_id, ip, created_at, width, height))
            return await self._position(conn, drawing_id)

    async def add_many(self, rows: list[tuple[uuid.UUID, datetime, int, int]]) -> None:
        """Old drawings (id, created_at, width, height), no IP, all or nothing. They take their
        places by date among the drawings already there."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                for drawing_id, created_at, width, height in rows:
                    await conn.execute(
                        "INSERT INTO drawings (id, ip, created_at, width, height) VALUES (%s, NULL, %s, %s, %s)",
                        (drawing_id, created_at, width, height))

    @staticmethod
    async def _position(conn, drawing_id: uuid.UUID) -> int | None:
        cur = await conn.execute(RANKED + "SELECT pos FROM ranked WHERE id = %s", (drawing_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def by_number(self, number: int) -> Drawing | None:
        """A visible drawing by gallery number, or None."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                RANKED + "SELECT id, pos, created_at, width, height FROM ranked WHERE pos = %s AND NOT hidden",
                (number,))
            row = await cur.fetchone()
            return Drawing(*row) if row else None

    async def exists_visible(self, drawing_id: uuid.UUID) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM drawings WHERE id = %s AND NOT hidden", (drawing_id,))
            return await cur.fetchone() is not None

    async def neighbours(self, number: int) -> tuple[int | None, int | None]:
        """The visible drawings just before and just after `number` (hidden ones skipped)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                RANKED + "SELECT (SELECT max(pos) FROM ranked WHERE pos < %s AND NOT hidden),"
                         "       (SELECT min(pos) FROM ranked WHERE pos > %s AND NOT hidden)",
                (number, number))
            row = await cur.fetchone()
            return (row[0], row[1]) if row else (None, None)

    async def count(self) -> int:
        """How many drawings exist (hidden ones included: they keep their numbers)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) FROM drawings")
            return int((await cur.fetchone())[0])

    async def first_number(self) -> int | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(RANKED + "SELECT min(pos) FROM ranked WHERE NOT hidden")
            row = await cur.fetchone()
            return row[0] if row else None

    # --- admin ---
    async def admin_page(self, limit: int, offset: int) -> list[AdminDrawing]:
        """Newest first, hidden ones included."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                RANKED + "SELECT d.id, d.pos, d.created_at, host(d.ip), d.hidden,"
                "       (SELECT count(*) FROM comments c WHERE c.drawing_id = d.id), d.mod_reason"
                " FROM ranked d ORDER BY d.pos DESC LIMIT %s OFFSET %s",
                (limit, offset))
            return [AdminDrawing(*r) for r in await cur.fetchall()]

    async def get(self, drawing_id: uuid.UUID) -> Drawing | None:
        """Any drawing by id, hidden or not, with its current number."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(RANKED + "SELECT id, pos, created_at, width, height FROM ranked WHERE id = %s",
                                     (drawing_id,))
            row = await cur.fetchone()
            return Drawing(*row) if row else None

    async def exists(self, drawing_id: uuid.UUID) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM drawings WHERE id = %s", (drawing_id,))
            return await cur.fetchone() is not None

    async def set_hidden(self, drawing_id: uuid.UUID, hidden: bool, reason: str = "") -> int | None:
        """Returns the drawing's number, or None if it's gone."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("UPDATE drawings SET hidden = %s, mod_reason = %s WHERE id = %s",
                                     (hidden, reason, drawing_id))
            return await self._position(conn, drawing_id) if cur.rowcount == 1 else None

    async def delete(self, drawing_id: uuid.UUID) -> int | None:
        """Deletes the row (its comments go with it) and returns the number it had. The caller
        removes the file."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                number = await self._position(conn, drawing_id)
                cur = await conn.execute("DELETE FROM drawings WHERE id = %s", (drawing_id,))
                return number if cur.rowcount == 1 else None

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

    async def add(self, drawing_id: uuid.UUID, body: str, ip: str, created_at: datetime,
                  name: str | None = None) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO comments (drawing_id, body, ip, created_at, name) VALUES (%s, %s, %s, %s, %s)"
                " RETURNING id", (drawing_id, body, ip, created_at, name or None))
            row = await cur.fetchone()
            return int(row[0])

    async def for_drawing(self, drawing_id: uuid.UUID) -> list[Comment]:
        """Visible comments, oldest first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, created_at, body, name FROM comments WHERE drawing_id = %s AND NOT hidden"
                " ORDER BY created_at, id", (drawing_id,))
            return [Comment(*r) for r in await cur.fetchall()]

    async def get(self, comment_id: int) -> Comment | None:
        """Any comment by id, hidden or not."""
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT id, created_at, body, name FROM comments WHERE id = %s", (comment_id,))
            row = await cur.fetchone()
            return Comment(*row) if row else None

    async def admin_page(self, limit: int, offset: int) -> list[AdminComment]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                RANKED + "SELECT c.id, d.pos, c.created_at, c.body, host(c.ip), c.hidden, c.name, c.mod_reason"
                " FROM comments c JOIN ranked d ON d.id = c.drawing_id"
                " ORDER BY c.id DESC LIMIT %s OFFSET %s", (limit, offset))
            return [AdminComment(*r) for r in await cur.fetchall()]

    async def set_hidden(self, comment_id: int, hidden: bool, reason: str = "") -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("UPDATE comments SET hidden = %s, mod_reason = %s WHERE id = %s",
                                     (hidden, reason, comment_id))
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
                "SELECT id, network::text, scope, reason, created_at, expires_at, subject_kind, subject_text, subject_at FROM bans"
                " WHERE network >>= %s::inet AND scope IN ('all', %s)"
                "   AND lifted_at IS NULL AND expires_at > now()"
                " ORDER BY expires_at DESC LIMIT 1", (ip, scope))
            row = await cur.fetchone()
            return Ban(*row) if row else None

    async def ended_untold_for(self, ip: str, scope: str) -> Ban | None:
        """The latest ban covering `ip` for `scope` that ran out before the visitor was ever told."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, network::text, scope, reason, created_at, expires_at, subject_kind, subject_text, subject_at FROM bans"
                " WHERE network >>= %s::inet AND scope IN ('all', %s)"
                "   AND lifted_at IS NULL AND expires_at <= now() AND notified_at IS NULL"
                " ORDER BY expires_at DESC LIMIT 1", (ip, scope))
            row = await cur.fetchone()
            return Ban(*row) if row else None

    async def mark_told(self, ban_id: int) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE bans SET notified_at = now() WHERE id = %s", (ban_id,))

    async def by_id(self, ban_id: int) -> Ban | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT id, network::text, scope, reason, created_at, expires_at, subject_kind, subject_text, subject_at FROM bans" " WHERE id = %s", (ban_id,))
            row = await cur.fetchone()
            return Ban(*row) if row else None

    async def add(self, network: str, scope: str, expires_at: datetime, reason: str,
                  subject_kind: str = "", subject_text: str = "", subject_at: datetime | None = None) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO bans (network, scope, expires_at, reason, subject_kind, subject_text, subject_at)"
                " VALUES (%s::cidr, %s, %s, %s, %s, %s, %s) RETURNING id",
                (network, scope, expires_at, reason, subject_kind, subject_text, subject_at))
            row = await cur.fetchone()
            return int(row[0])

    async def active(self) -> list[Ban]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, network::text, scope, reason, created_at, expires_at, subject_kind, subject_text, subject_at FROM bans"
                " WHERE lifted_at IS NULL AND expires_at > now() ORDER BY expires_at")
            return [Ban(*r) for r in await cur.fetchall()]

    async def lift(self, ban_id: int) -> bool:
        """Lifting erases the ban (the moderation log keeps the record)."""
        return await self.forget(ban_id)

    async def forget(self, ban_id: int) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM bans WHERE id = %s", (ban_id,))
            return cur.rowcount == 1

    async def purge_done(self) -> int:
        """Erase bans that are over and that the visitor already knows about (operator, msg 539)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM bans WHERE lifted_at IS NOT NULL OR (expires_at <= now() AND notified_at IS NOT NULL)")
            return cur.rowcount


class ModLog:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def add(self, admin: str, action: str, target: str, reason: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO mod_log (admin, action, target, reason) VALUES (%s, %s, %s, %s)",
                               (admin, action, target, reason))

    async def recent(self, limit: int) -> list[ModAction]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT at, admin, action, target, reason FROM mod_log ORDER BY id DESC LIMIT %s",
                                     (limit,))
            return [ModAction(*r) for r in await cur.fetchall()]
