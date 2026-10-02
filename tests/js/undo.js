const { JSDOM } = require("jsdom"); const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../../app/static/draw.html", "utf8");
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };

// Load the drawing page with the given canvas context; resolves after the page's own init().
function load(ctx, before = () => {}) {
  return new Promise((done) => {
    const dom = new JSDOM(html, { url: "https://canvas.example/draw", runScripts: "dangerously",
      beforeParse(w) { w.HTMLCanvasElement.prototype.getContext = () => ctx; before(w); } });
    dom.window.addEventListener("load", () => setTimeout(() => done(dom.window), 0));
  });
}

// ctrl+z / ctrl+y call undo/redo (T-0069).
async function keys() {
  const w = await load(new Proxy({}, { get: () => () => {} })), doc = w.document;
  const calls = [];
  w.undoDraw = () => calls.push("undo"); w.redoDraw = () => calls.push("redo");
  const press = (key, o = {}) => { const e = new w.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...o }); doc.body.dispatchEvent(e); return e; };
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
}

// Clear is an undoable step (T-0071). The fake canvas hands out numbered snapshots and
// records which one is put back.
async function clear() {
  let snap = 0; const put = [], errors = [];
  const ctx = new Proxy({ getImageData: () => ({ snap: snap++ }), putImageData: (img) => put.push(img && img.snap) },
                        { get: (t, k) => (k in t ? t[k] : () => {}) });
  const w = await load(ctx, (w) => { w.confirm = () => true; });
  const step = (f) => { try { f(); } catch (e) { errors.push(e.message); } };
  w.getBrushPosition("down", { clientX: 5, clientY: 5 }); w.getBrushPosition("up", {});   // 0 blank, 1 drawn
  step(w.clearCanvas);                                                                    // 2 cleared
  step(w.undoDraw);
  check(put.join() === "1" && errors.length === 0, "undo after Clear brings the drawing back");
  step(w.undoDraw); step(w.redoDraw); step(w.redoDraw); step(w.undoDraw);
  check(put.join() === "1,0,1,2,1" && errors.length === 0, "undo/redo keep working across a Clear (" + put.join() + (errors.length ? "; " + errors[0] : "") + ")");
}

(async () => {
  await keys(); await clear();
  console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
