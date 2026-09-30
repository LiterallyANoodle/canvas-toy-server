"""The real SQL, against a real Postgres. Skipped unless TEST_DATABASE_URL is set (CI runs
a postgres:18 service for this); the fake-repository tests cover everything else."""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def repo():
    from psycopg_pool import AsyncConnectionPool
    from app.db import Drawings, migrate

    async def setup():
        pool = AsyncConnectionPool(URL, min_size=1, max_size=5, open=False)
        await pool.open(wait=True)
        async with pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS drawings, schema_migrations")
        applied = await migrate(pool)
        return pool, applied

    loop = asyncio.new_event_loop()
    pool, applied = loop.run_until_complete(setup())
    yield loop, Drawings(pool), applied, pool
    loop.run_until_complete(pool.close())
    loop.close()


def test_migrations_apply_once(repo):
    loop, _, applied, pool = repo
    from app.db import migrate
    assert applied == ["001_drawings.sql"]
    assert loop.run_until_complete(migrate(pool)) == [], "re-running applies nothing"


def test_numbers_are_unique_under_concurrency(repo):
    loop, drawings, *_ = repo
    now = datetime.now(timezone.utc)
    async def many():
        return await asyncio.gather(*(drawings.add(uuid.uuid4(), "203.0.113.7", now) for _ in range(20)))
    numbers = loop.run_until_complete(many())
    assert sorted(numbers) == list(range(1, 21))


def test_round_trip_and_hidden(repo):
    loop, drawings, _, pool = repo
    did = uuid.uuid4()
    n = loop.run_until_complete(drawings.add(did, "2001:db8::1", datetime.now(timezone.utc)))
    got = loop.run_until_complete(drawings.by_number(n))
    assert got.id == did and got.number == n
    assert loop.run_until_complete(drawings.exists_visible(did))
    async def hide():
        async with pool.connection() as conn:
            await conn.execute("UPDATE drawings SET hidden = true WHERE id = %s", (did,))
    loop.run_until_complete(hide())
    assert loop.run_until_complete(drawings.by_number(n)) is None
    assert not loop.run_until_complete(drawings.exists_visible(did))


def test_an_invalid_ip_is_rejected_by_the_database(repo):
    loop, drawings, *_ = repo
    with pytest.raises(Exception):
        loop.run_until_complete(drawings.add(uuid.uuid4(), "not-an-ip", datetime.now(timezone.utc)))
