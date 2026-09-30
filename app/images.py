"""Turn a submitted canvas data URL into a checked, flattened, time-stamped PNG.

Pure functions (no I/O besides the bytes given), so they're easy to test.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
from io import BytesIO

from PIL import Image, PngImagePlugin

PREFIX = "data:image/png;base64,"
_EXIF_IFD = 0x8769
_DATETIME = 0x0132            # 0th IFD
_DATETIME_ORIGINAL = 0x9003   # Exif IFD
_DATETIME_DIGITIZED = 0x9004  # Exif IFD


class InvalidImage(ValueError):
    """The body isn't an acceptable drawing. The message is safe to show the user."""


def decode(body: bytes, max_width: int, max_height: int) -> Image.Image:
    """Parse `data:image/png;base64,...`, verify it's a real PNG within the size limit."""
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError:
        raise InvalidImage("That didn't look like a drawing.") from None
    if not text.startswith(PREFIX):
        raise InvalidImage("That didn't look like a drawing.")
    try:
        raw = base64.b64decode(text[len(PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        raise InvalidImage("That didn't look like a drawing.") from None

    # Refuse decompression bombs before decoding pixels: Pillow raises past this.
    Image.MAX_IMAGE_PIXELS = max_width * max_height
    try:
        with Image.open(BytesIO(raw)) as probe:
            probe.verify()                     # structure + CRC check; the object is unusable after
        img = Image.open(BytesIO(raw))
        img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImage("That drawing is too big.") from None
    except Exception:
        raise InvalidImage("That didn't look like a drawing.") from None
    if img.format != "PNG":
        raise InvalidImage("That didn't look like a drawing.")
    if img.width > max_width or img.height > max_height:
        raise InvalidImage("That drawing is too big.")
    return img


def flatten(img: Image.Image) -> Image.Image:
    """Transparent pixels become white, as the original server did."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def to_png_bytes(img: Image.Image, when: datetime) -> bytes:
    """Encode as PNG with the creation time in EXIF and in a PNG text chunk."""
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()
    exif[_DATETIME] = stamp
    exif.get_ifd(_EXIF_IFD).update({_DATETIME_ORIGINAL: stamp, _DATETIME_DIGITIZED: stamp})
    info = PngImagePlugin.PngInfo()
    info.add_text("Creation Time", stamp)
    out = BytesIO()
    img.save(out, format="PNG", exif=exif, pnginfo=info)
    return out.getvalue()
