"""The HTTP surface, with a fake repository and a fake Discord (no Postgres needed)."""
import uuid
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import Drawing
from app.main import create_app
from tests.test_units import canvas_like, data_url


class FakeDrawings:
    def __init__(self, fail=False):
        self.rows: dict[int, tuple] = {}
        self.fail = fail

    async def add(self, drawing_id, ip, created_at):
        if self.fail:
            raise RuntimeError("db down")
        number = len(self.rows) + 1
        self.rows[number] = (drawing_id, ip, created_at, False)
        return number

    async def by_number(self, number):
        r = self.rows.get(number)
        return Drawing(r[0], number, r[2]) if r and not r[3] else None

    async def exists_visible(self, drawing_id):
        return any(r[0] == drawing_id and not r[3] for r in self.rows.values())

    async def ping(self):
        return not self.fail


class FakeWebhook:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    async def __call__(self, url, png, filename, content):
        self.calls.append((url, filename, content))
        return self.ok


@pytest.fixture
def make(tmp_path):
    def _make(drawings=None, webhook=None, **overrides):
        settings = Settings(images_dir=tmp_path / "images", discord_webhook_url="https://discord.invalid/x",
                            **overrides)
        drawings = drawings or FakeDrawings()
        webhook = webhook or FakeWebhook()
        client = TestClient(create_app(settings, drawings=drawings, webhook=webhook))
        client.__enter__()                                   # run the lifespan
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
    assert client.get("/", follow_redirects=False).headers["location"] == "/draw"


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


def test_gallery_api_keeps_the_original_shape(make):
    client, *_ = make()
    submit(client, data_url(canvas_like()))
    j = client.get("/dragon-gallery/image/1").json()
    assert j["message"] == "Successfully found image." and uuid.UUID(j["image"]) and j["number"] == 1
    assert client.get("/dragon-gallery/image/99").status_code == 404


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
