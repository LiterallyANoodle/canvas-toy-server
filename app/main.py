"""Dragon Mail: the drawing toy's web app (T-0049).

Phase 1 keeps the original behaviour, done safely:
  GET  /draw                          the drawing page (web-1.0 look unchanged)
  POST /submit                        body = canvas data URL; saves, records, forwards to Discord
  GET  /dragon-gallery/image/{number} the original gallery's JSON for one drawing
  GET  /images/{uuid}.png             a saved drawing (visible ones only)
  GET  /healthz                       liveness + database
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import images
from .clientip import client_ip
from .config import Settings
from .ratelimit import RateLimiter
from .webhook import send_to_discord

log = logging.getLogger("dragonmail")
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, drawings=None, webhook=send_to_discord) -> FastAPI:
    """`drawings` and `webhook` are injectable so tests can run without Postgres or Discord."""
    settings = settings or Settings()
    limiter = RateLimiter(settings.rate_period_s, settings.rate_limit_global, settings.rate_limit_per_ip)
    state: dict = {"drawings": drawings}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if state["drawings"] is None:
            from psycopg_pool import AsyncConnectionPool
            from .db import Drawings, migrate
            pool = AsyncConnectionPool(settings.conninfo, min_size=1, max_size=5, open=False)
            await pool.open(wait=True, timeout=30)
            await migrate(pool)
            state["drawings"] = Drawings(pool)
        settings.images_dir.mkdir(parents=True, exist_ok=True)
        yield
        if pool is not None:
            await pool.close()

    app = FastAPI(title="Dragon Mail", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/draw")

    @app.get("/draw", include_in_schema=False)
    async def draw():
        return FileResponse(STATIC / "draw.html", media_type="text/html")

    app.mount("/Assets", StaticFiles(directory=STATIC / "Assets"), name="assets")

    @app.post("/submit")
    async def submit(request: Request):
        ip = client_ip(request.headers, request.client.host if request.client else None,
                       settings.client_ip_header, settings.require_client_ip_header)
        if ip is None:
            return PlainTextResponse("Something went wrong. Please try again later.\n", status_code=400)
        # Before reading the body (D-0006 #5): a limited client costs no read, no buffer.
        if not limiter.allow(ip):
            return PlainTextResponse("Too many drawings right now! Please wait a while and try again.\n",
                                     status_code=429)
        # The size cap is enforced while reading, so an oversized body is never held whole.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            return PlainTextResponse("That drawing is too big.\n", status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > settings.max_body_bytes:
                return PlainTextResponse("That drawing is too big.\n", status_code=413)

        try:
            img = images.decode(bytes(body), settings.max_width, settings.max_height)
        except images.InvalidImage as exc:
            return PlainTextResponse(f"{exc}\n", status_code=400)

        now = datetime.now(timezone.utc)
        png = images.to_png_bytes(images.flatten(img), now)
        drawing_id = uuid.uuid4()
        # File first, then the row: a row must never point at a missing file.
        path = settings.images_dir / f"{drawing_id}.png"
        path.write_bytes(png)
        try:
            number = await state["drawings"].add(drawing_id, ip, now)
        except Exception:
            path.unlink(missing_ok=True)
            log.exception("could not record drawing")
            return PlainTextResponse("Something went wrong saving your drawing. Please try again later.\n",
                                     status_code=500)

        sent = await webhook(settings.discord_webhook_url, png, f"{drawing_id}.png",
                             f"Drawing #{number} ({now:%Y-%m-%d %H:%M:%S} UTC)")
        if settings.discord_webhook_url and not sent:
            log.warning("drawing #%s saved but not forwarded to Discord", number)
        return PlainTextResponse(f"Got it, thank you! Your drawing is #{number}.\n")

    @app.get("/dragon-gallery/image/{number}")
    async def gallery_image(number: int):
        """The original gallery API: JSON for one drawing, by its number."""
        drawing = await state["drawings"].by_number(number)
        if drawing is None:
            return JSONResponse({"status": "404", "message": "Gallery index does not exist."}, status_code=404)
        return {"message": "Successfully found image.", "timestamp": drawing.created_at.isoformat(),
                "image": str(drawing.id), "number": drawing.number}

    @app.get("/images/{name}")
    async def image_file(name: str):
        # Only <uuid>.png of a visible drawing, so no path can be smuggled in.
        stem, dot, ext = name.rpartition(".")
        try:
            drawing_id = uuid.UUID(stem)
        except ValueError:
            raise HTTPException(404) from None
        if ext != "png" or not dot:
            raise HTTPException(404)
        if not await state["drawings"].exists_visible(drawing_id):
            raise HTTPException(404)
        path = settings.images_dir / f"{drawing_id}.png"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        ok = await state["drawings"].ping()
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    return app


# For `uvicorn app.main:app`. Creating it touches no database; the lifespan does that.
app = create_app()
