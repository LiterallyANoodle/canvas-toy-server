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
| `GET /draw` | the drawing page |
| `POST /submit` | the page's canvas as a `data:image/png;base64,...` body |
| `GET /dragon-gallery/image/{n}` | JSON for drawing number *n* |
| `GET /images/{uuid}.png` | a saved drawing |
| `GET /healthz` | ok if the app can reach its database |

Coming next: an image-board-style gallery, anonymous comments, and an admin page
with timed IP bans.

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
