const { JSDOM } = require("jsdom"); const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../../app/static/draw.html", "utf8");
const dom = new JSDOM(html, { url: "https://canvas.example/draw", runScripts: "dangerously",
  beforeParse(w) { w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: () => () => {} }); } });
const w = dom.window, doc = w.document;
const calls = [];
w.undoDraw = () => calls.push("undo"); w.redoDraw = () => calls.push("redo");
const press = (key, o = {}) => { const e = new w.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...o }); doc.body.dispatchEvent(e); return e; };
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
w.addEventListener("load", () => setTimeout(() => {          // after the page's own init()
  let e = press("z", { ctrlKey: true });
  check(calls.join() === "undo" && e.defaultPrevented, "ctrl+z undoes (and the browser's own undo is held back)");
  calls.length = 0; e = press("y", { ctrlKey: true });
  check(calls.join() === "redo" && e.defaultPrevented, "ctrl+y redoes");
  calls.length = 0; press("Z", { ctrlKey: true, shiftKey: true });
  check(calls.join() === "redo", "ctrl+shift+z redoes too");
  calls.length = 0; press("z", { metaKey: true }); press("y", { metaKey: true });
  check(calls.join() === "undo,redo", "cmd+z / cmd+y on a Mac");
  calls.length = 0; press("z"); press("y"); e = press("s", { ctrlKey: true }); press("z", { ctrlKey: true, altKey: true });
  check(calls.length === 0 && !e.defaultPrevented, "plain z/y, other shortcuts and ctrl+alt+z are left alone");
  console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
}, 0));
