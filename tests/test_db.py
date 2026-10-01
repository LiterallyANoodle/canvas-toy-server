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
            await conn.execute("DROP TABLE IF EXISTS comments, bans, drawings, schema_migrations")
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
    assert applied == ["001_drawings.sql", "002_comments_bans.sql"]
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


# --- phases 2-4 (T-0049) ----------------------------------------------------
def _run(loop, coro):
    return loop.run_until_complete(coro)


def test_neighbours_skip_hidden_and_first_number(repo):
    loop, drawings, *_ = repo
    now = datetime.now(timezone.utc)
    ids = [uuid.uuid4() for _ in range(4)]
    for i in ids:
        _run(loop, drawings.add(i, "203.0.113.7", now, 500, 500))
    _run(loop, drawings.set_hidden(ids[1], True))
    assert _run(loop, drawings.neighbours(1)) == (None, 3)
    assert _run(loop, drawings.neighbours(3)) == (1, 4)
    _run(loop, drawings.set_hidden(ids[0], True))
    assert _run(loop, drawings.first_number()) == 3
    got = _run(loop, drawings.by_number(3))
    assert (got.width, got.height) == (500, 500)


def test_comments_and_delete_cascade(repo):
    loop, drawings, _, pool = repo
    from app.db import Comments
    comments = Comments(pool)
    now = datetime.now(timezone.utc)
    did = uuid.uuid4()
    _run(loop, drawings.add(did, "203.0.113.7", now))
    a = _run(loop, comments.add(did, "first", "203.0.113.8", now))
    _run(loop, comments.add(did, "second", "2001:db8::2", now))
    assert [c.body for c in _run(loop, comments.for_drawing(did))] == ["first", "second"]
    _run(loop, comments.set_hidden(a, True))
    assert [c.body for c in _run(loop, comments.for_drawing(did))] == ["second"]
    page = _run(loop, comments.admin_page(10, 0))
    assert [(c.body, c.ip, c.hidden, c.drawing_number) for c in page] == \
        [("second", "2001:db8::2", False, 1), ("first", "203.0.113.8", True, 1)]
    (d,) = _run(loop, drawings.admin_page(10, 0))
    assert d.comment_count == 2 and d.ip == "203.0.113.7"
    assert _run(loop, drawings.delete(did))
    assert _run(loop, comments.admin_page(10, 0)) == []
    with pytest.raises(Exception):                       # the length check holds
        _run(loop, comments.add(uuid.uuid4(), "", "203.0.113.8", now))


def test_bans_cover_ranges_scopes_expiry_and_lifting(repo):
    loop, _, _, pool = repo
    from datetime import timedelta
    from app.db import Bans
    bans = Bans(pool)
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    b = _run(loop, bans.add("203.0.113.0/24", "comment", later, "spam"))
    assert _run(loop, bans.active_for("203.0.113.99", "comment")).id == b
    assert _run(loop, bans.active_for("203.0.113.99", "draw")) is None
    assert _run(loop, bans.active_for("198.51.100.1", "comment")) is None
    _run(loop, bans.add("2001:db8::1/128", "all", later, ""))
    assert _run(loop, bans.active_for("2001:db8::1", "draw")) is not None
    assert _run(loop, bans.active_for("203.0.113.99", "draw")) is None
    assert len(_run(loop, bans.active())) == 2
    assert _run(loop, bans.lift(b)) and not _run(loop, bans.lift(b))
    assert _run(loop, bans.active_for("203.0.113.99", "comment")) is None
    with pytest.raises(Exception):                       # a ban must end after it starts
        _run(loop, bans.add("192.0.2.1/32", "all", datetime(2000, 1, 1, tzinfo=timezone.utc), ""))


def test_import_numbered_takes_the_low_numbers_and_moves_the_counter(repo):
    loop, drawings, *_ = repo
    old = datetime(2024, 5, 1, tzinfo=timezone.utc)
    test_id = uuid.uuid4()
    _run(loop, drawings.add(test_id, "203.0.113.7", datetime.now(timezone.utc)))        # the test drawing, #1
    rows = [(uuid.uuid4(), n, old, 500, 500) for n in (1, 2, 3)]
    with pytest.raises(ValueError, match=r"\[1\]"):
        _run(loop, drawings.import_numbered(rows))
    assert _run(loop, drawings.first_number()) == 1 and _run(loop, drawings.by_number(2)) is None
    _run(loop, drawings.delete(test_id))
    rows[2] = (rows[2][0], 3, old, 120, 80)
    _run(loop, drawings.import_numbered(rows))
    got = _run(loop, drawings.by_number(3))
    assert (got.width, got.height) == (120, 80)
    (d,) = [d for d in _run(loop, drawings.admin_page(10, 0)) if d.number == 3]
    assert d.ip is None
    assert _run(loop, drawings.add(uuid.uuid4(), "203.0.113.7", datetime.now(timezone.utc))) == 4
