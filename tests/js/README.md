# Browser-behaviour checks (jsdom)

Harnesses that run the pages' own scripts in jsdom. `tests/test_browser.py` renders the pages
they need from `create_app()` with the fakes from `tests/test_app.py`, writes them to a temp
directory, and runs each harness under Node with `JS_FIXTURES` pointing there. They run with
the rest of the suite in CI and gate merges the same way (T-0062).

- `flip.js`: in-place flipping, history/back button, jump box, hidden lots and comments.
- `decor.js`: the quartet playlist and wine mode (no grunt on picking up/putting down the glass).
- `comment.js`: posting a comment without a reload; the season cycle on each switch-on.
- `tuck.js`: phone corners hidden while the drawing is in their strip.
- `hover.js`: the plaque date's hover shows the time, also after a flip.
- `refresh.js`: the refresh link updates comments and the list in place (T-0063).
- `send.js`: the drawing page goes to the new lot after a successful send (T-0064). Loads
  `app/static/draw.html` directly, no fixtures.
- `undo.js`: the drawing page's ctrl+z / ctrl+y (and ctrl+shift+z, cmd on a Mac) call undo/redo (T-0069),
  and undo after Clear brings the drawing back (T-0071).
  Also loads `app/static/draw.html` directly.

Locally: `npm ci` here once, then `python -m pytest tests/test_browser.py`. Without Node or
jsdom the tests skip; CI sets `REQUIRE_JS_TESTS=1` so they fail instead.

A harness prints one `PASS`/`FAIL` line per check and exits non-zero on any failure; pytest
shows that output when one fails.
