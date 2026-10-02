const { JSDOM } = require("jsdom");
const FIX = process.env.JS_FIXTURES;                 // written by tests/test_browser.py
const fs = require("fs");
function load(file, narrowMatches) {
  return new JSDOM(fs.readFileSync(FIX + "/" + file, "utf8"), {
    url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously",
    beforeParse(w) {
      w.innerHeight = 700;
      w.matchMedia = () => ({ matches: narrowMatches, addEventListener() {} });
      w.fetch = () => Promise.resolve({ ok: false });
      w.Audio = class { constructor() { this.paused = true; } addEventListener() {} load() {} play() { return Promise.resolve(); } pause() {} getAttribute() { return null; } };
      w.Image = class {};
      w.__frameBottom = 900;                            // drawing reaches below the fold: in the way
      w.HTMLElement.prototype.getBoundingClientRect = function () {
        return this.classList && this.classList.contains("framed") ? { bottom: w.__frameBottom, top: w.__frameBottom - 650 } : { bottom: 0, top: 0 };
      };
      Object.defineProperty(w.HTMLElement.prototype, "offsetHeight", { get() { return this.classList && this.classList.contains("corner") ? 77 : 0; } });
    },
  });
}
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
const tucked = (d) => ["quartet", "spike"].map((id) => d.window.document.getElementById(id).classList.contains("tucked"));
let d = load("decor.html", true);
check(tucked(d).every(Boolean), "phone, drawing in view: both corners tucked");
d.window.__frameBottom = 650; d.window.dispatchEvent(new d.window.Event("scroll"));
check(tucked(d).every(Boolean), "drawing's bottom still inside the corner strip (650 > 700-89): tucked");
d.window.__frameBottom = 500; d.window.dispatchEvent(new d.window.Event("scroll"));
check(tucked(d).every((t) => !t), "scrolled past the drawing: both corners show");
d.window.__frameBottom = 800; d.window.dispatchEvent(new d.window.Event("scroll"));
check(tucked(d).every(Boolean), "scrolled back up to the drawing: tucked again");
d = load("decor.html", false);
check(tucked(d).every((t) => !t), "desktop: never tucked");
d = load("empty.html", true);
check(tucked(d).every((t) => !t), "phone, page without a drawing: shown");
console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
