"""The page scripts, run in jsdom against pages the real app renders (T-0062).

The Python tests only see the HTML. These run the gallery's and the drawing page's own
scripts (flipping, comments, music/wine, corner tucking, hover, refresh, send) under Node,
so a template change that breaks a script fails here too. The harnesses are in tests/js/;
this module renders their fixtures from create_app() with the fakes and runs each one.

Without Node or jsdom (`npm ci` in tests/js) these skip locally; CI sets REQUIRE_JS_TESTS=1
so a missing toolchain fails instead of skipping.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_app import FakeBans, FakeComments, FakeDrawings, FakeModLog, FakeWebhook, submit
from tests.test_units import canvas_like, data_url

JS = Path(__file__).parent / "js"
HARNESSES = ["flip", "decor", "comment", "tuck", "hover", "refresh", "send", "undo"]


def app(images):
    drawings = FakeDrawings()
    comments = FakeComments(drawings)
    client = TestClient(create_app(Settings(images_dir=images, discord_webhook_url="https://discord.invalid/x"),
                                   drawings=drawings, comments=comments, bans=FakeBans(), modlog=FakeModLog(),
                                   webhook=FakeWebhook(), admin_verifier=None))
    client.__enter__()                                   # run the lifespan
    return client, drawings, comments


def draw(client, n):
    for _ in range(n):
        assert submit(client, data_url(canvas_like())).status_code == 200


def comment(client, number, body, ip, name="", fetch=False):
    headers = {"CF-Connecting-IP": ip, **({"X-Requested-With": "fetch"} if fetch else {})}
    r = client.post(f"/dragon-gallery/image/{number}/comments", data={"body": body, "name": name},
                    headers=headers, follow_redirects=False)
    assert r.status_code == (200 if fetch else 303)
    return r


def page(client, path="/dragon-gallery/image/1"):
    r = client.get(path)
    assert r.status_code == 200
    return r.text


def api(client, numbers):
    return {str(n): client.get(f"/dragon-gallery/api/image/{n}").json() for n in numbers}


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    out = tmp_path_factory.mktemp("js-fixtures")
    images = tmp_path_factory.mktemp("images")

    # flip.js: three lots; No. 2 has a hostile name/body and a hidden comment, No. 3 is hidden.
    client, drawings, comments = app(images / "flip")
    draw(client, 3)
    comment(client, 2, "<b>bold?</b><img src=x onerror=alert(1)>", "203.0.113.1", name="Sir <i>Dragon</i>")
    comment(client, 2, "secret words", "203.0.113.2", name="Rude")
    comment(client, 3, "still here", "203.0.113.3")
    comments.rows[2]["hidden"] = True
    drawings.rows[3] = drawings.rows[3][:3] + (True,)
    (out / "page1.html").write_text(page(client))
    (out / "api.json").write_text(json.dumps(api(client, [1, 2, 3])))

    # decor.js, comment.js, hover.js, tuck.js: two lots, no comments; then a comment posted the
    # way the page posts it.
    client, *_ = app(images / "decor")
    draw(client, 2)
    (out / "decor.html").write_text(page(client))
    before = api(client, [1, 2])
    post = comment(client, 1, "<b>bravo</b>", "203.0.113.4", name="Sir", fetch=True).json()
    (out / "api2.json").write_text(json.dumps({"before": before, "after": api(client, [1, 2]), "post": post}))

    # tuck.js: a page with no drawing on it.
    client, *_ = app(images / "empty")
    (out / "empty.html").write_text(page(client, "/dragon-gallery"))

    # refresh.js: the page shows two lots and no comments; meanwhile a third lot and a comment arrive.
    client, *_ = app(images / "refresh")
    draw(client, 2)
    (out / "refresh.html").write_text(page(client))
    draw(client, 1)
    comment(client, 1, "someone else's <i>comment</i>", "203.0.113.5")
    (out / "refresh_api.json").write_text(json.dumps(api(client, [1, 2, 3])))
    return out


def toolchain():
    """Why the harnesses can't run here, or None."""
    if shutil.which("node") is None:
        return "node is not installed"
    if not (JS / "node_modules" / "jsdom").is_dir():
        return "jsdom is not installed (run `npm ci` in tests/js)"
    return None


@pytest.mark.parametrize("name", HARNESSES)
def test_page_scripts(name, fixtures):
    missing = toolchain()
    if missing:
        if os.environ.get("REQUIRE_JS_TESTS"):
            pytest.fail(missing)
        pytest.skip(missing)
    r = subprocess.run(["node", str(JS / f"{name}.js")], cwd=JS, env={**os.environ, "JS_FIXTURES": str(fixtures)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{name}.js:\n{r.stdout}{r.stderr}"
