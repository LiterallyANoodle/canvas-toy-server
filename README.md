# Dragon Mail (canvas-toy-server)

A little canvas toy: people draw on a web page and send the drawing to me. The drawing
is saved, numbered, and forwarded to Discord.

This repo is the whole thing now: the drawing page (`app/static/draw.html`, web-1.0
look and all), the server, and the gallery. The original prototypes live on under
`legacy/` (`legacy/server` is this repo's first version; `legacy/gallery` was merged
in from `canvas-toy-gallery` with its history).

## What it serves

| | |
|---|---|
| `GET /` | the gallery |
| `GET /draw` | the drawing page |
| `POST /submit` | the page's canvas as a `data:image/png;base64,...` body |
| `GET /dragon-gallery` | the first drawing in the gallery |
| `GET /dragon-gallery/image/{n}` | drawing number *n* in its gold frame, with comments underneath |
| `POST /dragon-gallery/image/{n}/comments` | a comment (a name is optional) |
| `GET /images/{uuid}.png` | a saved drawing |
| `GET /admin` | hide or delete drawings and comments, and set timed IP bans |
| `GET /healthz` | ok if the app can reach its database |

`/admin` sits behind Cloudflare Access, and the app checks Access's signed token itself
(`app/admin_auth.py`), so it stays shut to anything that reaches the container another
way. It's off until `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` are set.

Bans are timeouts: each one has an end (a preset or a custom length, up to 10 years). They
cover one address or a range, and block drawing, commenting, or both. A ban made from a
comment or drawing keeps a copy of what it was for. The banned visitor, and only they, sees
the reason, the end time and that copy when they try to post, and once more after the ban
ends if they never saw it; then the ban is erased. Every admin action, with its reason, is
kept in the moderation log. Times on the pages show in the viewer's own time zone.

## Importing the drawings from before the database

```sh
docker compose exec dragon-mail python -m app.import_legacy           # dry run: shows the plan
docker compose exec dragon-mail python -m app.import_legacy --apply
```

It reads `/data/images/import` (the images volume's `import/` folder), takes each time from
the file name (UTC unless `--tz` says otherwise), and numbers them from 1 in time order.
Each keeps its own size; the gallery shows a smaller one small in the frame.

## Running it

It runs as a container (`ghcr.io/literallyanoodle/dragon-mail`, built by GitHub
Actions from `master`) next to Postgres. See `deploy/compose.yml` and `.env.example`.

Behind Cloudflare, the client's IP is taken from `CF-Connecting-IP` only, never from
`X-Forwarded-For`, which anyone can fake.

## Developing

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                # the database tests need TEST_DATABASE_URL
```
