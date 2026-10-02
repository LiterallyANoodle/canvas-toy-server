const { JSDOM } = require("jsdom"); const fs = require("fs");
const FIX = process.env.JS_FIXTURES;                 // written by tests/test_browser.py
const api = JSON.parse(fs.readFileSync(FIX + "/api2.json")).before;
const dom = new JSDOM(fs.readFileSync(FIX + "/decor.html", "utf8"), { url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously",
  beforeParse(w) { w.fetch = (u) => { const n = u.split("/").pop(); return Promise.resolve({ ok: true, json: () => Promise.resolve(api[n]) }); };
    w.Image = class { set src(v) { setTimeout(() => this.onload && this.onload(), 0); } };
    w.Audio = class { constructor() { this.paused = true; } addEventListener() {} load() {} play() { return Promise.resolve(); } pause() {} getAttribute() { return null; } }; } });
const w = dom.window, doc = w.document;
const t = () => doc.querySelector("#plaque time");
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m + "  [" + t().title + "]"); if (!c) fails++; };
check(/\d{1,2}:\d{2}:\d{2}/.test(t().title), "on load: hover shows a time with seconds");
doc.getElementById("nav-next").dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
setTimeout(() => { check(/\d{1,2}:\d{2}:\d{2}/.test(t().title) && doc.getElementById("lot").textContent === "Lot No. 2", "after an in-place flip: still a time");
  console.log(fails ? "FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0); }, 50);
