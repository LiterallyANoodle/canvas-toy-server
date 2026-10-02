const { JSDOM } = require("jsdom"); const fs = require("fs");
const FIX = process.env.JS_FIXTURES;                 // written by tests/test_browser.py
const api = JSON.parse(fs.readFileSync(FIX + "/refresh_api.json"));
const audios = []; const gets = [];
const dom = new JSDOM(fs.readFileSync(FIX + "/refresh.html", "utf8"), { url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously",
  beforeParse(w) {
    w.fetch = (u, o = {}) => { gets.push(u); const n = u.split("/").pop(); return Promise.resolve(api[n] ? { ok: true, status: 200, json: () => Promise.resolve(api[n]) } : { ok: false, status: 404 }); };
    w.Image = class { set src(v) { setTimeout(() => this.onload && this.onload(), 0); } };
    w.Audio = class { constructor(s) { this._src = s || ""; this.paused = true; audios.push(this); } set src(v) { this._src = v; } get src() { return this._src; }
      addEventListener() {} load() {} play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; } getAttribute() { return this._src || null; } };
  } });
const w = dom.window, doc = w.document, $ = (id) => doc.getElementById(id);
const click = (el, o = {}) => { const e = new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...o }); el.dispatchEvent(e); return e; };
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
(async () => {
  click($("quartet")); click($("spike"));
  const ta = $("comment-form").querySelector("textarea"); ta.value = "half-typed";
  check($("total").textContent === "2" && $("comment-list").textContent.includes("No comments yet."), "before: 2 drawings, no comments");
  const ev = click($("refresh")); await new Promise((r) => setTimeout(r, 40));
  check(ev.defaultPrevented && gets.includes("/dragon-gallery/api/image/1"), "refresh fetched the data instead of reloading");
  check($("total").textContent === "3" && $("nav-last").dataset.n === "3", "the drawing list updated (3 now, last -> 3)");
  check($("comment-list").textContent.includes("someone else's <i>comment</i>") && !$("comment-list").querySelector("i"), "the new comment appeared, as text");
  check(!audios[0].paused && doc.body.classList.contains("wine"), "music and wine mode untouched");
  check(ta.value === "half-typed", "the half-typed comment is kept");
  check(w.location.pathname === "/dragon-gallery/image/1" && $("lot").textContent === "Lot No. 1", "still on No. 1");
  const before = gets.length; const ctrl = click($("refresh"), { ctrlKey: true });
  check(!ctrl.defaultPrevented && gets.length === before, "ctrl-click left to the browser");
  console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
