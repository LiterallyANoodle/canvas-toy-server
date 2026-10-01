"""Import the drawings from before the database (T-0049).

    docker compose exec dragon-mail python -m app.import_legacy            # dry run: shows the plan
    docker compose exec dragon-mail python -m app.import_legacy --apply    # does it

Reads every image in the folder (default /data/images/import), takes each one's time from
its file name, and adds them as drawings numbered from 1 (or --start) in time order. Each is
re-saved as a PNG like a new drawing, at its own size. Nothing is written without --apply,
and nothing at all if any of those numbers is already taken.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from . import images
from .config import Settings

SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# 2024-05-01 13-45-07, 20240501_134507, 2024-05-01T13:45:07, "2024-05-01 at 1.45.07 PM", ...
STAMP = re.compile(r"(?P<y>20\d\d)\D?(?P<mo>\d\d)\D?(?P<d>\d\d)\D{0,6}?(?P<h>\d{1,2})\D?(?P<mi>\d\d)"
                   r"(?:\D?(?P<s>\d\d))?(?:\D{0,2}(?P<ampm>[AaPp][Mm]))?")
MAX_SIDE = 4000


def time_from_name(name: str, tz) -> datetime | None:
    m = STAMP.search(name)
    if not m:
        return None
    hour = int(m["h"])
    if m["ampm"]:
        hour = hour % 12 + (12 if m["ampm"].lower() == "pm" else 0)
    try:
        local = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), hour, int(m["mi"]), int(m["s"] or 0), tzinfo=tz)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def plan(folder: Path, tz, start: int) -> tuple[list[tuple[Path, datetime]], list[str]]:
    found, problems = [], []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        if path.suffix.lower() not in SUFFIXES:
            problems.append(f"skipped (not an image): {path.name}")
            continue
        when = time_from_name(path.name, tz)
        if when is None:
            problems.append(f"no date and time in the name: {path.name}")
            continue
        found.append((path, when))
    found.sort(key=lambda pw: (pw[1], pw[0].name))
    return found, problems


def load(path: Path) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE
    with Image.open(path) as img:
        img.load()
        if img.width > MAX_SIDE or img.height > MAX_SIDE:
            raise ValueError(f"{path.name} is {img.width}x{img.height}, bigger than {MAX_SIDE}px")
        return images.flatten(img)


async def apply(settings: Settings, found: list[tuple[Path, datetime]], start: int) -> None:
    from psycopg_pool import AsyncConnectionPool
    from .db import Drawings, migrate

    pool = AsyncConnectionPool(settings.conninfo, min_size=1, max_size=2, open=False)
    await pool.open(wait=True, timeout=30)
    written: list[Path] = []
    try:
        await migrate(pool)
        rows = []
        for number, (path, when) in enumerate(found, start):
            img = load(path)
            drawing_id = uuid.uuid4()
            out = settings.images_dir / f"{drawing_id}.png"
            out.write_bytes(images.to_png_bytes(img, when))
            written.append(out)
            rows.append((drawing_id, number, when, img.width, img.height))
        await Drawings(pool).import_numbered(rows)
    except BaseException:
        for out in written:                    # files only stay if their rows went in
            out.unlink(missing_ok=True)
        raise
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", type=Path, default=Path("/data/images/import"))
    ap.add_argument("--tz", default="UTC", help="the timezone the file names are in, e.g. America/Chicago")
    ap.add_argument("--start", type=int, default=1, help="the first number to give out (default 1)")
    ap.add_argument("--apply", action="store_true", help="actually import (without it: just show the plan)")
    args = ap.parse_args(argv)

    tz = ZoneInfo(args.tz)
    found, problems = plan(args.folder, tz, args.start)
    for number, (path, when) in enumerate(found, args.start):
        try:
            with Image.open(path) as img:
                size = f"{img.width}x{img.height}"
        except Exception as exc:
            size = f"UNREADABLE ({type(exc).__name__})"
            problems.append(f"can't read: {path.name}")
        print(f"No. {number:>3}  {when:%Y-%m-%d %H:%M:%S} UTC  {size:>10}  {path.name}")
    for p in problems:
        print(f"!! {p}")
    if not found:
        print("Nothing to import.")
        return 1
    if problems:
        print("Fix the files marked !! (or move them out of the folder), then run this again.")
        return 1
    if not args.apply:
        print(f"\nDry run: nothing changed. If this looks right, run again with --apply.")
        return 0
    try:
        asyncio.run(apply(Settings(), found, args.start))
    except ValueError as exc:
        print(f"Nothing imported: {exc}. (Delete those drawings in /admin first, or use --start.)")
        return 1
    print(f"\nImported {len(found)} drawings: No. {args.start} to {args.start + len(found) - 1}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
