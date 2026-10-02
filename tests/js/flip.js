const { JSDOM } = require("jsdom");
const FIX = process.env.JS_FIXTURES;                 // written by tests/test_browser.py
const fs = require("fs");
const api = JSON.parse(fs.readFileSync(FIX + "/api.json"));
const html = fs.readFileSync(FIX + "/page1.html", "utf8");
const fetched = [];
const dom = new JSDOM(html, {
  url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously", pretendToBeVisual: true,
  beforeParse(w) {
    w.fetch = (url) => { fetched.push(url); const n = url.split("/").pop();
      return Promise.resolve(api[n] ? { ok: true, json: () => Promise.resolve(api[n]) } : { ok: false, status: 404 }); };
    w.Image = class { set src(v) { this._s = v; setTimeout(() => this.onload && this.onload(), 0); } get src() { return this._s; } };
  },
});
const w = dom.window, doc = w.document, $ = (id) => doc.getElementById(id);
const tick = () => new Promise((r) => setTimeout(r, 20));
let fails = 0;
const check = (cond, msg) => { console.log((cond ? "PASS " : "FAIL ") + msg); if (!cond) fails++; };
(async () => {
  check($("lot").textContent === "Lot No. 1", "starts on No. 1");
  check($("nav-first").classList.contains("off") && !$("nav-first").hasAttribute("href"), "first is off on No. 1");
  $("nav-next").dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  await tick();
  check(w.location.pathname === "/dragon-gallery/image/2", "URL is now /image/2 (" + w.location.pathname + ")");
  check($("lot").textContent === "Lot No. 2", "lot sign updated");
  check($("drawing").getAttribute("src") === api[2].image, "image swapped");
  check(doc.title === "Dragon Gallery #2", "title updated");
  check($("comment-form").getAttribute("action") === "/dragon-gallery/image/2/comments", "comment form posts to No. 2");
  const cm = $("comment-list").querySelector(".comment");
  check(cm && cm.textContent.includes("<b>bold?</b><img src=x onerror=alert(1)>"), "comment text shown literally");
  check(!$("comment-list").querySelector("b, img, i"), "no HTML from visitor text made it into the DOM");
  check(cm && cm.querySelector(".who").textContent.startsWith("Sir <i>Dragon</i> · "), "name shown literally");
  check(!$("nav-first").classList.contains("off") && $("nav-last").dataset.n === "3", "first/last live on No. 2");
  check(fetched.length === 1 && fetched[0] === "/dragon-gallery/api/image/2", "one JSON fetch");
  const gone = $("comment-list").querySelector(".comment.gone");
  check(gone && gone.textContent.includes("This comment has been hidden") && gone.querySelector("time"), "hidden comment: slot, notice, time");
  check(!$("comment-list").textContent.includes("secret words") && !$("comment-list").textContent.includes("Rude"), "hidden comment's words withheld");
  $("nav-last").dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  await tick();
  check($("lot").textContent === "Lot No. 3" && $("nav-last").classList.contains("off"), "last goes to No. 3 (hidden), last is then off");
  check($("drawing").hidden && !$("drawing").hasAttribute("src") && !$("hidden-note").hidden, "hidden lot: picture gone, grey card shown");
  check(!$("opening").classList.contains("standard"), "hidden lot: no white backing");
  check($("comment-list").textContent.includes("still here"), "hidden lot's comments still up");
  w.history.back(); await tick(); await tick();
  check($("lot").textContent === "Lot No. 2", "back button returns to No. 2 (" + $("lot").textContent + ")");
  check(!$("drawing").hidden && $("hidden-note").hidden && $("drawing").getAttribute("src") === api[2].image, "back on a normal lot: picture back, card gone");
  $("jump-n").value = "1";
  $("jump").dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
  await tick();
  check($("lot").textContent === "Lot No. 1" && w.location.pathname === "/dragon-gallery/image/1", "jump box flips in place");
  // a ctrl-click must be left to the browser (new tab)
  const before = fetched.length;
  const ev = new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ctrlKey: true });
  $("nav-next").dispatchEvent(ev);
  await tick();
  check(fetched.length === before && !ev.defaultPrevented, "ctrl-click left alone");
  console.log(fails ? fails + " FAILED" : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
