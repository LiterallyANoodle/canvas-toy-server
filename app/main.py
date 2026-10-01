"""Dragon Mail: the drawing toy's web app (T-0049).

  GET  /draw                                   the drawing page (web-1.0 look unchanged)
  POST /submit                                 body = canvas data URL; saves, records, forwards to Discord
  GET  /dragon-gallery                         -> the first drawing
  GET  /dragon-gallery/image/{number}          the gallery page: the drawing in its frame, comments below
  POST /dragon-gallery/image/{number}/comments an anonymous comment (form post)
  GET  /images/{uuid}.png                      a saved drawing (visible ones only)
  GET  /admin, POST /admin/...                 drawings, comments and timed IP bans (Cloudflare Access)
  GET  /healthz                                liveness + database
"""
from __future__ import annotations

import ipaddress
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import images
from .admin_auth import AccessDenied, AccessVerifier
from .clientip import client_ip
from .config import Settings
from .db import BAN_SCOPES
from .ratelimit import RateLimiter
from .webhook import send_to_discord

log = logging.getLogger("dragonmail")
HERE = Path(__file__).parent
STATIC = HERE / "static"
templates = Jinja2Templates(directory=HERE / "templates")

# Fixed texts for the ?c= / ?n= query keys, so nothing a visitor types is echoed back.
COMMENT_NOTICES = {
    "posted": ("Thanks! Your comment is up.", False),
    "empty": ("The comment was empty.", True),
    "long": ("That comment is too long.", True),
    "slow": ("Too many comments right now! Please wait a while and try again.", True),
    "banned": ("You're in a timeout and can't comment right now.", True),
    "error": ("Something went wrong. Please try again later.", True),
}
ADMIN_NOTICES = {
    "hidden": "Hidden.", "unhidden": "Visible again.", "deleted": "Deleted.", "banned": "Ban added.",
    "lifted": "Ban lifted.", "missing": "That's already gone.", "badnet": "That isn't an IP address or range.",
    "widenet": "That range is too wide (the widest allowed is /16 for IPv4, /32 for IPv6).",
}
BAN_DURATIONS = {"1h": ("1 hour", timedelta(hours=1)), "1d": ("1 day", timedelta(days=1)),
                 "7d": ("1 week", timedelta(days=7)), "30d": ("30 days", timedelta(days=30)),
                 "365d": ("1 year", timedelta(days=365))}
ADMIN_PAGE_SIZE = 50
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_comment(raw: str) -> str:
    """Newlines kept (at most two in a row), other control characters dropped, ends trimmed."""
    text = CONTROL_CHARS.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def ban_message(ban) -> str:
    return f"You're in a timeout until {ban.expires_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC.\n"


def parse_png_name(name: str) -> uuid.UUID:
    """Only <uuid>.png, so no path can be smuggled in."""
    stem, dot, ext = name.rpartition(".")
    try:
        drawing_id = uuid.UUID(stem)
    except ValueError:
        raise HTTPException(404) from None
    if ext != "png" or not dot:
        raise HTTPException(404)
    return drawing_id


def create_app(settings: Settings | None = None, drawings=None, comments=None, bans=None,
               webhook=send_to_discord, admin_verifier=None) -> FastAPI:
    """The repositories, `webhook` and `admin_verifier` are injectable so tests can run
    without Postgres, Discord or Cloudflare."""
    settings = settings or Settings()
    limiter = RateLimiter(settings.rate_period_s, settings.rate_limit_global, settings.rate_limit_per_ip)
    comment_limiter = RateLimiter(settings.comment_rate_period_s, settings.comment_rate_limit_global,
                                  settings.comment_rate_limit_per_ip)
    state: dict = {"drawings": drawings, "comments": comments, "bans": bans}
    if admin_verifier is None and settings.admin_enabled:
        admin_verifier = AccessVerifier(settings.cf_access_team_domain, settings.cf_access_aud,
                                        settings.admin_emails)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if state["drawings"] is None:
            from psycopg_pool import AsyncConnectionPool
            from .db import Bans, Comments, Drawings, migrate
            pool = AsyncConnectionPool(settings.conninfo, min_size=1, max_size=5, open=False)
            await pool.open(wait=True, timeout=30)
            await migrate(pool)
            state.update(drawings=Drawings(pool), comments=Comments(pool), bans=Bans(pool))
        settings.images_dir.mkdir(parents=True, exist_ok=True)
        yield
        if pool is not None:
            await pool.close()

    app = FastAPI(title="Dragon Mail", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/admin"):
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
        return response

    def visitor_ip(request: Request) -> str | None:
        return client_ip(request.headers, request.client.host if request.client else None,
                         settings.client_ip_header, settings.require_client_ip_header)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/draw")

    @app.get("/draw", include_in_schema=False)
    async def draw():
        return FileResponse(STATIC / "draw.html", media_type="text/html")

    app.mount("/Assets", StaticFiles(directory=STATIC / "Assets"), name="assets")

    @app.post("/submit")
    async def submit(request: Request):
        ip = visitor_ip(request)
        if ip is None:
            return PlainTextResponse("Something went wrong. Please try again later.\n", status_code=400)
        ban = await state["bans"].active_for(ip, "draw")
        if ban is not None:
            return PlainTextResponse(ban_message(ban), status_code=403)
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
            number = await state["drawings"].add(drawing_id, ip, now, img.width, img.height)
        except Exception:
            path.unlink(missing_ok=True)
            log.exception("could not record drawing")
            return PlainTextResponse("Something went wrong saving your drawing. Please try again later.\n",
                                     status_code=500)

        caption = f"Drawing #{number} ({now:%Y-%m-%d %H:%M:%S} UTC)"
        if settings.public_base_url:
            caption += f" {settings.public_base_url}/dragon-gallery/image/{number}"
        sent = await webhook(settings.discord_webhook_url, png, f"{drawing_id}.png", caption)
        if settings.discord_webhook_url and not sent:
            log.warning("drawing #%s saved but not forwarded to Discord", number)
        return PlainTextResponse(f"Got it, thank you! Your drawing is #{number}.\n")

    def gallery_page(request: Request, status_code=200, **context) -> HTMLResponse:
        context.setdefault("drawing", None)
        context.setdefault("empty_message", "")
        return templates.TemplateResponse(request, "gallery.html", context, status_code=status_code)

    @app.get("/dragon-gallery", include_in_schema=False)
    async def gallery_start(request: Request):
        first = await state["drawings"].first_number()
        if first is None:
            return gallery_page(request, empty_message="No drawings yet. Be the first!")
        return RedirectResponse(f"/dragon-gallery/image/{first}", status_code=302)

    @app.get("/dragon-gallery/image/{number}", include_in_schema=False)
    async def gallery_image(request: Request, number: int, c: str = ""):
        drawing = await state["drawings"].by_number(number)
        if drawing is None:
            return gallery_page(request, status_code=404, empty_message=f"There's no drawing No. {number}.")
        prev, next_ = await state["drawings"].neighbours(number)
        notice, notice_bad = COMMENT_NOTICES.get(c, ("", False))
        # Shown at its real size against the standard canvas (an odd-sized one looks small in the frame).
        w, h = drawing.width or settings.max_width, drawing.height or settings.max_height
        standard = w >= settings.max_width or h >= settings.max_height
        scale_pct = 100 if standard else round(100 * w / settings.max_width, 2)
        return gallery_page(request, drawing=drawing, prev=prev, next=next_, standard=standard, scale_pct=scale_pct,
                            comments=await state["comments"].for_drawing(drawing.id),
                            notice=notice, notice_bad=notice_bad, max_chars=settings.comment_max_chars)

    @app.post("/dragon-gallery/image/{number}/comments", include_in_schema=False)
    async def add_comment(request: Request, number: int):
        def back(key: str) -> RedirectResponse:
            return RedirectResponse(f"/dragon-gallery/image/{number}?c={key}#comments", status_code=303)

        ip = visitor_ip(request)
        if ip is None:
            return back("error")
        drawing = await state["drawings"].by_number(number)
        if drawing is None:
            raise HTTPException(404)
        ban = await state["bans"].active_for(ip, "comment")
        if ban is not None:
            return back("banned")
        # The cap is enforced while reading, whatever Content-Length claims (D-0007 #2).
        cap = 4 * settings.comment_max_chars + 1024
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > cap:
                return back("long")
        form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=10)
        if form.get("website", [""])[0]:              # the hidden field only bots fill in
            return back("posted")
        body = clean_comment(form.get("body", [""])[0])
        if not body:
            return back("empty")
        if len(body) > settings.comment_max_chars:
            return back("long")
        if not comment_limiter.allow(ip):
            return back("slow")
        try:
            await state["comments"].add(drawing.id, body, ip, datetime.now(timezone.utc))
        except Exception:
            log.exception("could not record comment")
            return back("error")
        return back("posted")

    @app.get("/images/{name}")
    async def image_file(name: str):
        drawing_id = parse_png_name(name)
        if not await state["drawings"].exists_visible(drawing_id):
            raise HTTPException(404)
        path = settings.images_dir / f"{drawing_id}.png"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    # --- admin -------------------------------------------------------------
    async def require_admin(request: Request) -> str:
        if admin_verifier is None:
            raise HTTPException(404)                  # switched off until Access is configured
        try:
            return await admin_verifier(request.headers)
        except AccessDenied as exc:
            log.warning("admin refused: %s", exc)
            raise HTTPException(403) from None

    def same_origin(request: Request) -> bool:
        """Admin forms only count when posted from this site (no cross-site form tricks). Browsers
        send Origin on every POST; Referer is not accepted instead (D-0007 #3). Defense in depth:
        every admin action also needs a valid Access token."""
        source = request.headers.get("origin") or ""
        host = request.headers.get("host", "")
        return bool(source) and source != "null" and urlsplit(source).netloc == host

    async def admin_action(request: Request) -> None:
        await require_admin(request)
        if not same_origin(request):
            raise HTTPException(403)

    def to_admin(key: str, section: str) -> RedirectResponse:
        return RedirectResponse(f"/admin?n={key}#{section}", status_code=303)

    @app.get("/admin", include_in_schema=False)
    async def admin_home(request: Request, n: str = "", dpage: int = 0, cpage: int = 0):
        email = await require_admin(request)
        dpage, cpage = max(dpage, 0), max(cpage, 0)
        return templates.TemplateResponse(request, "admin.html", {
            "admin": email, "notice": ADMIN_NOTICES.get(n, ""),
            "drawings": await state["drawings"].admin_page(ADMIN_PAGE_SIZE, dpage * ADMIN_PAGE_SIZE),
            "comments": await state["comments"].admin_page(ADMIN_PAGE_SIZE, cpage * ADMIN_PAGE_SIZE),
            "bans": await state["bans"].active(),
            "durations": [(k, label) for k, (label, _) in BAN_DURATIONS.items()],
            "dpage": dpage, "cpage": cpage, "page_size": ADMIN_PAGE_SIZE,
        })

    @app.get("/admin/images/{name}", include_in_schema=False)
    async def admin_image(request: Request, name: str):
        await require_admin(request)
        drawing_id = parse_png_name(name)
        if not await state["drawings"].exists(drawing_id):
            raise HTTPException(404)
        path = settings.images_dir / f"{drawing_id}.png"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.post("/admin/drawings/{drawing_id}/{action}", include_in_schema=False)
    async def admin_drawing(request: Request, drawing_id: uuid.UUID, action: str):
        await admin_action(request)
        repo = state["drawings"]
        if action in ("hide", "unhide"):
            ok = await repo.set_hidden(drawing_id, action == "hide")
            return to_admin(("hidden" if action == "hide" else "unhidden") if ok else "missing", "drawings")
        if action == "delete":
            ok = await repo.delete(drawing_id)
            (settings.images_dir / f"{drawing_id}.png").unlink(missing_ok=True)
            return to_admin("deleted" if ok else "missing", "drawings")
        raise HTTPException(404)

    @app.post("/admin/comments/{comment_id}/{action}", include_in_schema=False)
    async def admin_comment(request: Request, comment_id: int, action: str):
        await admin_action(request)
        repo = state["comments"]
        if action in ("hide", "unhide"):
            ok = await repo.set_hidden(comment_id, action == "hide")
            return to_admin(("hidden" if action == "hide" else "unhidden") if ok else "missing", "comments")
        if action == "delete":
            return to_admin("deleted" if await repo.delete(comment_id) else "missing", "comments")
        raise HTTPException(404)

    @app.post("/admin/bans", include_in_schema=False)
    async def admin_add_ban(request: Request):
        await admin_action(request)
        form = await request.form()
        try:
            network = ipaddress.ip_network(str(form.get("network", "")).strip(), strict=False)
        except ValueError:
            return to_admin("badnet", "bans")
        if network.prefixlen < (16 if network.version == 4 else 32):
            return to_admin("widenet", "bans")
        duration = BAN_DURATIONS.get(str(form.get("duration", "")))
        scope = str(form.get("scope", "all"))
        if duration is None or scope not in BAN_SCOPES:
            raise HTTPException(400)
        reason = clean_comment(str(form.get("reason", "")))[:200]
        await state["bans"].add(str(network), scope, datetime.now(timezone.utc) + duration[1], reason)
        return to_admin("banned", "bans")

    @app.post("/admin/bans/{ban_id}/lift", include_in_schema=False)
    async def admin_lift_ban(request: Request, ban_id: int):
        await admin_action(request)
        return to_admin("lifted" if await state["bans"].lift(ban_id) else "missing", "bans")

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        ok = await state["drawings"].ping()
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    return app


# For `uvicorn app.main:app`. Creating it touches no database; the lifespan does that.
app = create_app()
