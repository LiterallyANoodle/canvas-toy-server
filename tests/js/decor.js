const { JSDOM } = require("jsdom");
const FIX = process.env.JS_FIXTURES;                 // written by tests/test_browser.py
const fs = require("fs");
const api = JSON.parse(fs.readFileSync(FIX + "/api2.json")).before;
const audios = [];
const dom = new JSDOM(fs.readFileSync(FIX + "/decor.html", "utf8"), {
  url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously",
  beforeParse(w) {
    w.fetch = (url) => { const n = url.split("/").pop();
      return Promise.resolve(api[n] ? { ok: true, json: () => Promise.resolve(api[n]) } : { ok: false }); };
    w.Image = class { set src(v) { setTimeout(() => this.onload && this.onload(), 0); } };
    w.Audio = class {
      constructor(src) { this._src = src || ""; this.paused = true; this.plays = 0; this.listeners = {}; audios.push(this); }
      set src(v) { this._src = v; } get src() { return this._src; }
      getAttribute(k) { return k === "src" ? (this._src || null) : null; }
      addEventListener(t, f) { this.listeners[t] = f; }
      load() {}
      play() { this.paused = false; this.plays++; return Promise.resolve(); }
      pause() { this.paused = true; }
    };
  },
});
const w = dom.window, doc = w.document, $ = (id) => doc.getElementById(id);
const click = (el, opts = {}) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...opts }));
const tick = () => new Promise((r) => setTimeout(r, 20));
let fails = 0;
const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
(async () => {
  const music = audios[0];
  check(music && music.paused && !music.src, "music idle until asked");
  click($("quartet")); await tick();
  check(!music.paused && music.src.startsWith("/music/spring.mp3?v="), "quartet starts Spring");
  check($("quartet").classList.contains("on") && $("quartet").getAttribute("aria-pressed") === "true", "quartet glows on");
  music.listeners.ended(); await tick();
  check(music.src.startsWith("/music/summer.mp3?v=") && !music.paused, "Spring ends -> Summer");
  music.listeners.ended(); music.listeners.ended(); music.listeners.ended(); await tick();   // summer -> autumn -> winter -> minuet
  check(music.src.startsWith("/music/minuet.mp3?v="), "Winter ends -> the Boccherini minuet");
  music.listeners.ended(); await tick();
  check(music.src.startsWith("/music/spring.mp3?v="), "the minuet ends -> back to Spring");
  click($("quartet")); await tick();
  check(music.paused && !$("quartet").classList.contains("on"), "quartet stops it");

  const grunts = () => audios.filter((a) => a._src.startsWith("/sounds/hmmm.mp3?v=") && a.plays > 0).length;
  click(doc.body); await tick();
  check(grunts() === 0, "no grunt before the wine");
  click($("spike")); await tick();
  check(doc.body.classList.contains("wine") && $("spike").classList.contains("on"), "Spike: wine mode on");
  check(grunts() === 0, "taking the glass isn't a sip");
  click(doc.body); click($("lot")); await tick();
  check(grunts() === 2, "every click is a hmmm (2 clicks -> 2)");
  click($("nav-next")); await tick();
  check(grunts() === 3 && $("lot").textContent === "Lot No. 2", "a nav click sips AND still flips");
  click($("spike")); await tick();
  check(!doc.body.classList.contains("wine") && grunts() === 3, "putting the glass down: no hmmm, wine off");
  click(doc.body); await tick();
  check(grunts() === 3, "silence after");
  check(!music.paused === false, "music still stopped");
  console.log(fails ? fails + " FAILED" : "ALL PASS");
  process.exit(fails ? 1 : 0);
})();
