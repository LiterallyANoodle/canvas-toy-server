"""The HTTP surface, with fake repositories and a fake Discord (no Postgres needed)."""
import ipaddress
import uuid
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import AdminComment, AdminDrawing, Ban, Comment, Drawing, ModAction
from app.main import create_app
from tests.test_units import canvas_like, data_url


class FakeDrawings:
    """rows: internal seq -> (id, ip, created_at, hidden). Public numbers are positions by age."""

    def __init__(self, fail=False):
        self.rows: dict[int, tuple] = {}
        self.sizes: dict[int, tuple] = {}
        self.reasons: dict[int, str] = {}
        self.fail = fail
        self.seq = 0

    def _ranked(self):
        return sorted(self.rows, key=lambda k: (self.rows[k][2], k))

    def _pos(self, seq):
        return self._ranked().index(seq) + 1

    def _find(self, drawing_id):
        return next((k for k, r in self.rows.items() if r[0] == drawing_id), None)

    async def add(self, drawing_id, ip, created_at, width=None, height=None):
        if self.fail:
            raise RuntimeError("db down")
        self.seq += 1
        self.rows[self.seq] = (drawing_id, ip, created_at, False)
        self.sizes[self.seq] = (width, height)
        return self._pos(self.seq)

    async def by_number(self, number):
        ranked = self._ranked()
        if not 1 <= number <= len(ranked):
            return None
        k = ranked[number - 1]
        r = self.rows[k]
        return None if r[3] else Drawing(r[0], number, r[2], *self.sizes.get(k, (None, None)))

    async def exists_visible(self, drawing_id):
        return any(r[0] == drawing_id and not r[3] for r in self.rows.values())

    async def ping(self):
        return not self.fail

    def _visible(self):
        return [i + 1 for i, k in enumerate(self._ranked()) if not self.rows[k][3]]

    async def neighbours(self, number):
        vis = self._visible()
        return (max((n for n in vis if n < number), default=None), min((n for n in vis if n > number), default=None))

    async def count(self):
        return len(self.rows)

    async def first_number(self):
        return min(self._visible(), default=None)

    async def admin_page(self, limit, offset):
        out = [AdminDrawing(self.rows[k][0], i + 1, self.rows[k][2], self.rows[k][1], self.rows[k][3], 0,
                            self.reasons.get(k, "")) for i, k in enumerate(self._ranked())][::-1]
        return out[offset:offset + limit]

    async def get(self, drawing_id):
        k = self._find(drawing_id)
        return Drawing(self.rows[k][0], self._pos(k), self.rows[k][2]) if k is not None else None

    async def exists(self, drawing_id):
        return self._find(drawing_id) is not None

    async def set_hidden(self, drawing_id, hidden, reason=""):
        k = self._find(drawing_id)
        if k is None:
            return None
        r = self.rows[k]
        self.rows[k] = (r[0], r[1], r[2], hidden)
        self.reasons[k] = reason
        return self._pos(k)

    async def delete(self, drawing_id):
        k = self._find(drawing_id)
        if k is None:
            return None
        pos = self._pos(k)
        del self.rows[k]
        return pos


class FakeComments:
    def __init__(self, drawings):
        self.drawings, self.rows = drawings, {}

    async def add(self, drawing_id, body, ip, created_at, name=None):
        cid = len(self.rows) + 1
        self.rows[cid] = dict(drawing_id=drawing_id, body=body, ip=ip, created_at=created_at, hidden=False,
                              name=name, reason="")
        return cid

    async def get(self, comment_id):
        c = self.rows.get(comment_id)
        return Comment(comment_id, c["created_at"], c["body"], c["name"]) if c else None

    async def for_drawing(self, drawing_id):
        return [Comment(i, c["created_at"], c["body"], c["name"]) for i, c in sorted(self.rows.items())
                if c["drawing_id"] == drawing_id and not c["hidden"]]

    async def admin_page(self, limit, offset):
        num = {r[0]: n for n, r in self.drawings.rows.items()}
        out = [AdminComment(i, num.get(c["drawing_id"], 0), c["created_at"], c["body"], c["ip"], c["hidden"],
                            c["name"], c["reason"])
               for i, c in sorted(self.rows.items(), reverse=True)]
        return out[offset:offset + limit]

    async def set_hidden(self, comment_id, hidden, reason=""):
        if comment_id not in self.rows:
            return False
        self.rows[comment_id].update(hidden=hidden, reason=reason)
        return True

    async def delete(self, comment_id):
        return self.rows.pop(comment_id, None) is not None


class FakeModLog:
    def __init__(self):
        self.rows = []

    async def add(self, admin, action, target, reason):
        self.rows.append(ModAction(datetime.now(timezone.utc), admin, action, target, reason))

    async def recent(self, limit):
        return list(reversed(self.rows))[:limit]


class FakeBans:
    def __init__(self):
        self.rows = {}
        self.next_id = 1

    def _ban(self, i):
        b = self.rows[i]
        return Ban(i, b["network"], b["scope"], b["reason"], b["created_at"], b["expires_at"],
                   b["subject_kind"], b["subject_text"], b["subject_at"])

    def _covering(self, ip, scope):
        return [i for i, b in self.rows.items() if b["scope"] in ("all", scope)
                and ipaddress.ip_address(ip) in ipaddress.ip_network(b["network"])]

    async def active_for(self, ip, scope):
        now = datetime.now(timezone.utc)
        live = [i for i in self._covering(ip, scope) if self.rows[i]["expires_at"] > now]
        return self._ban(max(live, key=lambda i: self.rows[i]["expires_at"])) if live else None

    async def ended_untold_for(self, ip, scope):
        now = datetime.now(timezone.utc)
        done = [i for i in self._covering(ip, scope)
                if self.rows[i]["expires_at"] <= now and self.rows[i]["told"] is None]
        return self._ban(max(done, key=lambda i: self.rows[i]["expires_at"])) if done else None

    async def mark_told(self, ban_id):
        if ban_id in self.rows:
            self.rows[ban_id]["told"] = datetime.now(timezone.utc)

    async def by_id(self, ban_id):
        return self._ban(ban_id) if ban_id in self.rows else None

    async def add(self, network, scope, expires_at, reason, subject_kind="", subject_text="", subject_at=None):
        bid, self.next_id = self.next_id, self.next_id + 1
        self.rows[bid] = dict(network=network, scope=scope, expires_at=expires_at, reason=reason,
                              created_at=datetime.now(timezone.utc), subject_kind=subject_kind,
                              subject_text=subject_text, subject_at=subject_at, told=None)
        return bid

    async def active(self):
        now = datetime.now(timezone.utc)
        return [self._ban(i) for i, b in self.rows.items() if b["expires_at"] > now]

    async def lift(self, ban_id):
        return await self.forget(ban_id)

    async def forget(self, ban_id):
        return self.rows.pop(ban_id, None) is not None

    async def purge_done(self):
        now = datetime.now(timezone.utc)
        gone = [i for i, b in self.rows.items() if b["expires_at"] <= now and b["told"] is not None]
        for i in gone:
            del self.rows[i]
        return len(gone)


class FakeWebhook:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    async def __call__(self, url, png, filename, content):
        self.calls.append((url, filename, content))
        return self.ok


@pytest.fixture
def make(tmp_path):
    def _make(drawings=None, webhook=None, comments=None, bans=None, admin_verifier=None, modlog=None, **overrides):
        settings = Settings(images_dir=tmp_path / "images", discord_webhook_url="https://discord.invalid/x",
                            **overrides)
        drawings = drawings or FakeDrawings()
        comments = comments or FakeComments(drawings)
        bans = bans or FakeBans()
        modlog = modlog or FakeModLog()
        webhook = webhook or FakeWebhook()
        client = TestClient(create_app(settings, drawings=drawings, comments=comments, bans=bans, modlog=modlog,
                                       webhook=webhook, admin_verifier=admin_verifier))
        client.__enter__()                                   # run the lifespan
        client.comments, client.bans, client.modlog = comments, bans, modlog
        return client, drawings, webhook, settings
    return _make


def submit(client, body, ip="203.0.113.7"):
    return client.post("/submit", content=body, headers={"CF-Connecting-IP": ip,
                                                         "Content-type": "text/html; charset=UTF-8"})


def test_the_drawing_page_is_served_with_its_look(make):
    client, *_ = make()
    r = client.get("/draw")
    assert r.status_code == 200 and "<canvas" in r.text and 'fetch("/submit"' in r.text
    assert client.get("/Assets/fonts/Ciircuit-Regular.ttf").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/dragon-gallery"
    assert 'href="/dragon-gallery"' in r.text


def test_a_drawing_is_saved_recorded_and_forwarded(make):
    client, drawings, webhook, settings = make()
    r = submit(client, data_url(canvas_like()))
    assert r.status_code == 200 and "#1" in r.text
    (drawing_id, ip, _, _), = drawings.rows.values()
    assert ip == "203.0.113.7"
    saved = Image.open(settings.images_dir / f"{drawing_id}.png")
    assert saved.mode == "RGB" and saved.getpixel((0, 0)) == (255, 255, 255)
    assert webhook.calls[0][1] == f"{drawing_id}.png"


def test_error_messages_reveal_no_internals(make):
    client, *_ = make(drawings=FakeDrawings(fail=True))
    r = submit(client, data_url(canvas_like()))
    assert r.status_code == 500
    assert "db" not in r.text.lower() and "discord" not in r.text.lower()


def test_a_failed_db_write_leaves_no_orphan_file(make):
    client, _, _, settings = make(drawings=FakeDrawings(fail=True))
    submit(client, data_url(canvas_like()))
    assert list(settings.images_dir.glob("*.png")) == []


def test_discord_failure_still_saves_and_tells_the_user_nothing_internal(make):
    client, drawings, _, _ = make(webhook=FakeWebhook(ok=False))
    r = submit(client, data_url(canvas_like()))
    assert r.status_code == 200 and "discord" not in r.text.lower()
    assert len(drawings.rows) == 1


def test_garbage_gets_a_400(make):
    client, drawings, *_ = make()
    r = submit(client, b"data:image/png;base64,nope")
    assert r.status_code == 400 and "drawing" in r.text
    assert drawings.rows == {}


def test_an_oversized_body_is_refused_before_decoding(make):
    client, drawings, *_ = make(max_body_bytes=1000)
    r = submit(client, b"data:image/png;base64," + b"A" * 5000)
    assert r.status_code == 413 and drawings.rows == {}


def test_per_ip_rate_limit(make):
    client, *_ = make(rate_limit_per_ip=2)
    body = data_url(canvas_like())
    assert [submit(client, body).status_code for _ in range(3)] == [200, 200, 429]
    assert submit(client, body, ip="198.51.100.1").status_code == 200




def test_gallery_number_must_be_an_integer(make):
    # The original pasted this path segment into SQL.
    client, *_ = make()
    assert client.get("/dragon-gallery/image/1;DROP TABLE drawings").status_code == 422


def test_images_are_served_by_uuid_only(make):
    client, drawings, *_ = make()
    submit(client, data_url(canvas_like()))
    (drawing_id, *_), = drawings.rows.values()
    r = client.get(f"/images/{drawing_id}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    for bad in ("../app/main.py", f"{drawing_id}.txt", "not-a-uuid.png", f"{uuid.uuid4()}.png"):
        assert client.get(f"/images/{bad}").status_code == 404, bad


def test_healthz_reflects_the_database(make):
    client, *_ = make()
    assert client.get("/healthz").json() == {"ok": True}
    client2, *_ = make(drawings=FakeDrawings(fail=True))
    assert client2.get("/healthz").status_code == 503


# --- D-0006 review fixes -------------------------------------------------
def test_a_submission_without_the_client_ip_header_is_refused(make):
    # Otherwise every client bypassing Cloudflare would share the proxy's IP.
    client, drawings, *_ = make()
    r = client.post("/submit", content=data_url(canvas_like()))
    assert r.status_code == 400 and drawings.rows == {}


def test_the_header_requirement_can_be_turned_off(make):
    client, drawings, *_ = make(require_client_ip_header=False)
    assert client.post("/submit", content=data_url(canvas_like())).status_code == 200


def test_rate_limit_is_checked_before_the_body_is_read(make):
    # A limited client sending an oversized body gets 429, not 413: the body was never read.
    client, *_ = make(rate_limit_per_ip=1, max_body_bytes=1000)
    assert submit(client, b"data:image/png;base64,AAAA").status_code == 400   # uses the one slot
    assert submit(client, b"A" * 5000).status_code == 429
