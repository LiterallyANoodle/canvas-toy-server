"""Pure pieces: image handling, client IP, rate limits."""
import base64
from datetime import datetime, timezone
from io import BytesIO

import pytest
from PIL import Image

from app import images
from app.clientip import client_ip
from app.ratelimit import RateLimiter


def data_url(img: Image.Image, fmt="PNG") -> bytes:
    buf = BytesIO()
    img.save(buf, format=fmt)
    return b"data:image/png;base64," + base64.b64encode(buf.getvalue())


def canvas_like(w=500, h=500) -> Image.Image:
    """What the page's canvas.toDataURL() sends: RGBA, mostly transparent."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(100, 200):
        img.putpixel((x, 150), (200, 30, 30, 255))
    return img


# --- images ---------------------------------------------------------------
def test_a_canvas_drawing_decodes():
    img = images.decode(data_url(canvas_like()), 500, 500)
    assert img.size == (500, 500)


@pytest.mark.parametrize("body", [
    b"",                                                   # empty
    b"hello",                                              # not a data URL
    b"data:image/jpeg;base64,AAAA",                        # wrong type
    b"data:image/png;base64,!!!not base64!!!",             # bad base64
    b"data:image/png;base64," + base64.b64encode(b"not a png at all"),
    "data:image/png;base64,é".encode(),               # non-ascii
])
def test_garbage_is_refused_with_a_user_safe_message(body):
    with pytest.raises(images.InvalidImage) as err:
        images.decode(body, 500, 500)
    assert "drawing" in str(err.value)


def test_a_jpeg_dressed_as_png_is_refused():
    with pytest.raises(images.InvalidImage):
        images.decode(data_url(Image.new("RGB", (10, 10)), fmt="JPEG"), 500, 500)


def test_oversized_dimensions_are_refused():
    with pytest.raises(images.InvalidImage, match="too big"):
        images.decode(data_url(canvas_like(501, 500)), 500, 500)


def test_transparency_becomes_white():
    flat = images.flatten(canvas_like())
    assert flat.mode == "RGB"
    assert flat.getpixel((0, 0)) == (255, 255, 255)        # was transparent
    assert flat.getpixel((150, 150)) == (200, 30, 30)       # the stroke survives


def test_png_carries_the_creation_time():
    when = datetime(2026, 9, 30, 1, 2, 3, tzinfo=timezone.utc)
    png = images.to_png_bytes(images.flatten(canvas_like()), when)
    out = Image.open(BytesIO(png))
    assert out.format == "PNG"
    assert out.info.get("Creation Time") == "2026:09:30 01:02:03"
    assert out.getexif()[0x0132] == "2026:09:30 01:02:03"


# --- client IP ------------------------------------------------------------
def test_cloudflare_header_wins():
    assert client_ip({"CF-Connecting-IP": "203.0.113.7"}, "172.18.0.5", "CF-Connecting-IP") == "203.0.113.7"


def test_x_forwarded_for_is_never_trusted():
    # A client can send this header; trusting it would make IP bans trivial to dodge.
    assert client_ip({"X-Forwarded-For": "1.2.3.4"}, "172.18.0.5", "CF-Connecting-IP") == "172.18.0.5"


def test_a_malformed_header_falls_back_to_the_peer():
    assert client_ip({"CF-Connecting-IP": "'; DROP TABLE drawings;--"}, "172.18.0.5",
                     "CF-Connecting-IP") == "172.18.0.5"


def test_ipv6_is_normalized():
    assert client_ip({"CF-Connecting-IP": "2001:DB8::1"}, None, "CF-Connecting-IP") == "2001:db8::1"


# --- rate limits ----------------------------------------------------------
class Clock:
    t = 0.0
    def __call__(self):
        return self.t


def test_per_ip_limit_and_window():
    c = Clock()
    rl = RateLimiter(period_s=60, global_limit=100, per_ip_limit=2, clock=c)
    assert rl.allow("a") and rl.allow("a")
    assert not rl.allow("a")
    assert rl.allow("b"), "another IP isn't affected"
    c.t = 61
    assert rl.allow("a"), "the window slides"


def test_global_limit():
    rl = RateLimiter(period_s=60, global_limit=3, per_ip_limit=10, clock=Clock())
    assert all(rl.allow(ip) for ip in ("a", "b", "c"))
    assert not rl.allow("d")


def test_refused_requests_do_not_extend_the_wait():
    c = Clock()
    rl = RateLimiter(period_s=60, global_limit=100, per_ip_limit=1, clock=c)
    assert rl.allow("a")
    c.t = 30
    assert not rl.allow("a")
    c.t = 61
    assert rl.allow("a")


def test_sweep_forgets_idle_ips():
    c = Clock()
    rl = RateLimiter(period_s=60, global_limit=10_000, per_ip_limit=5, clock=c)
    for i in range(50):
        rl.allow(f"10.0.0.{i}")
    c.t = 61
    rl.sweep()
    assert rl._by_ip == {}, "memory tracks recent clients only"


def test_client_ip_can_require_the_header():
    assert client_ip({}, "172.18.0.5", "CF-Connecting-IP", require_header=True) is None
    assert client_ip({"CF-Connecting-IP": "garbage"}, "172.18.0.5", "CF-Connecting-IP", require_header=True) is None
