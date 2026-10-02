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
            await conn.execute("DROP TABLE IF EXISTS comments, bans, mod_log, drawings, schema_migrations")
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
    assert applied == ["001_drawings.sql", "002_comments_bans.sql", "003_names_reasons.sql"]
    assert loop.run_until_complete(migrate(pool)) == [], "re-running applies nothing"


def test_numbers_are_unique_under_concurrency(repo):
    loop, drawings, *_ = repo
    now = datetime.now(timezone.utc)
    async def many():
        return await asyncio.gather(*(drawings.add(uuid.uuid4(), "203.0.113.7", now) for _ in range(20)))
    loop.run_until_complete(many())
    # The number reported back can briefly repeat under a race (it's counted before the others
    # commit), but once they're in, the gallery's numbers are exactly 1..20, each once.
    assert sorted(d.number for d in loop.run_until_complete(drawings.admin_page(50, 0))) == list(range(1, 21))


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
    assert loop.run_until_complete(drawings.by_number(n)).hidden          # keeps its slot (msg 557)
    assert not loop.run_until_complete(drawings.exists_visible(did))      # but its image isn't served


def test_an_invalid_ip_is_rejected_by_the_database(repo):
    loop, drawings, *_ = repo
    with pytest.raises(Exception):
        loop.run_until_complete(drawings.add(uuid.uuid4(), "not-an-ip", datetime.now(timezone.utc)))


# --- phases 2-4 (T-0049) ----------------------------------------------------
def _run(loop, coro):
    return loop.run_until_complete(coro)


def test_neighbours_and_ends_include_hidden_slots(repo):
    loop, drawings, *_ = repo
    now = datetime.now(timezone.utc)
    ids = [uuid.uuid4() for _ in range(4)]
    for i in ids:
        _run(loop, drawings.add(i, "203.0.113.7", now, 500, 500))
    _run(loop, drawings.set_hidden(ids[1], True))
    assert _run(loop, drawings.neighbours(1)) == (None, 2)
    assert _run(loop, drawings.neighbours(3)) == (2, 4)
    _run(loop, drawings.set_hidden(ids[0], True))
    assert _run(loop, drawings.first_number()) == 1 and _run(loop, drawings.last_number()) == 4
    assert _run(loop, drawings.by_number(1)).hidden
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
    assert [(c.body, c.hidden) for c in _run(loop, comments.for_drawing(did))] == [("first", True), ("second", False)]
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


def test_numbers_are_positions_by_age(repo):
    loop, drawings, *_ = repo
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    ids = [uuid.uuid4() for _ in range(3)]
    for i, did in enumerate(ids):
        assert _run(loop, drawings.add(did, "203.0.113.7", now + timedelta(seconds=i))) == i + 1
    assert _run(loop, drawings.delete(ids[0])) == 1
    assert _run(loop, drawings.by_number(1)).id == ids[1]
    assert _run(loop, drawings.add(uuid.uuid4(), "203.0.113.7", now + timedelta(seconds=9))) == 3
    old = [(uuid.uuid4(), datetime(2025, 10, 25, 22, 49, tzinfo=timezone.utc), 500, 500),
           (uuid.uuid4(), datetime(2025, 10, 26, tzinfo=timezone.utc), 120, 80)]
    _run(loop, drawings.add_many(old))
    assert _run(loop, drawings.by_number(1)).id == old[0][0]
    small = _run(loop, drawings.by_number(2))
    assert (small.id, small.width, small.height) == (old[1][0], 120, 80)
    assert _run(loop, drawings.get(ids[1])).number == 3
    _run(loop, drawings.set_hidden(ids[1], True))
    assert _run(loop, drawings.by_number(3)).hidden and _run(loop, drawings.neighbours(2)) == (1, 3)
    (d,) = [d for d in _run(loop, drawings.admin_page(10, 0)) if d.id == old[0][0]]
    assert d.number == 1 and d.ip is None
    with pytest.raises(Exception):                       # all or nothing
        _run(loop, drawings.add_many([(uuid.uuid4(), now, 1, 1), (old[0][0], now, 1, 1)]))
    assert len(_run(loop, drawings.admin_page(50, 0))) == 5
    assert _run(loop, drawings.count()) == 5
    assert _run(loop, drawings.last_number()) == 5


def test_names_reasons_and_the_mod_log(repo):
    loop, drawings, _, pool = repo
    from app.db import Comments, ModLog
    comments, modlog = Comments(pool), ModLog(pool)
    now = datetime.now(timezone.utc)
    did = uuid.uuid4()
    _run(loop, drawings.add(did, "203.0.113.7", now))
    cid = _run(loop, comments.add(did, "hi", "203.0.113.8", now, "Sir Dragon"))
    _run(loop, comments.add(did, "anon", "203.0.113.8", now, ""))
    assert [c.name for c in _run(loop, comments.for_drawing(did))] == ["Sir Dragon", None]
    with pytest.raises(Exception):                       # names are capped in the schema too
        _run(loop, comments.add(did, "x", "203.0.113.8", now, "n" * 41))
    _run(loop, comments.set_hidden(cid, True, "rude"))
    assert [c.mod_reason for c in _run(loop, comments.admin_page(10, 0)) if c.id == cid] == ["rude"]
    assert _run(loop, drawings.set_hidden(did, True, "spam")) == 1
    (d,) = _run(loop, drawings.admin_page(10, 0))
    assert d.mod_reason == "spam"
    assert _run(loop, drawings.set_hidden(uuid.uuid4(), True)) is None
    assert _run(loop, drawings.delete(did)) == 1 and _run(loop, drawings.delete(did)) is None
    _run(loop, modlog.add("noodle@example.com", "delete drawing", "#1", "spam"))
    (m,) = _run(loop, modlog.recent(10))
    assert (m.admin, m.action, m.target, m.reason) == ("noodle@example.com", "delete drawing", "#1", "spam")


def test_ban_subjects_telling_and_purging(repo):
    loop, _, _, pool = repo
    from datetime import timedelta
    from app.db import Bans
    bans = Bans(pool)
    now = datetime.now(timezone.utc)
    sent = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    live = _run(loop, bans.add("203.0.113.7/32", "all", now + timedelta(hours=1), "be nice", "comment", "rude", sent))
    got = _run(loop, bans.active_for("203.0.113.7", "comment"))
    assert (got.subject_kind, got.subject_text, got.subject_at) == ("comment", "rude", sent)
    assert _run(loop, bans.by_id(live)).reason == "be nice"

    async def end(ban_id):
        async with pool.connection() as conn:
            await conn.execute("UPDATE bans SET created_at = now() - interval '2 hours',"
                               " expires_at = now() - interval '1 minute' WHERE id = %s", (ban_id,))
    _run(loop, end(live))
    assert _run(loop, bans.active_for("203.0.113.7", "comment")) is None
    assert _run(loop, bans.ended_untold_for("203.0.113.7", "comment")).id == live
    assert _run(loop, bans.ended_untold_for("198.51.100.1", "comment")) is None

    told = _run(loop, bans.add("198.51.100.0/24", "draw", now + timedelta(hours=1), "", "drawing", "", sent))
    _run(loop, bans.mark_told(told))
    _run(loop, end(told))
    assert _run(loop, bans.ended_untold_for("198.51.100.9", "draw")) is None     # already knew
    assert _run(loop, bans.purge_done()) == 1 and _run(loop, bans.by_id(told)) is None
    assert _run(loop, bans.forget(live)) and _run(loop, bans.by_id(live)) is None
