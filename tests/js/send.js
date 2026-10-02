const { JSDOM, VirtualConsole } = require("jsdom"); const fs = require("fs");
const html = fs.readFileSync(__dirname + "/../../app/static/draw.html", "utf8");
function run(resp) {
  return new Promise((done) => {
    const alerts = [], navs = [];
    const vc = new VirtualConsole(); vc.on("jsdomError", (e) => { if (/navigation/i.test(e.message)) navs.push("nav"); });
    const dom = new JSDOM(html, { url: "https://canvas.example/draw", runScripts: "dangerously", virtualConsole: vc,
      beforeParse(w) {
        w.alert = (m) => alerts.push(m);
        w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, { get: () => () => {} });
        w.HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,AAAA";
        w.fetch = () => Promise.resolve({ ok: resp.ok, status: resp.status, text: () => Promise.resolve(resp.text),
          headers: { get: (h) => resp.headers[h] || null } });
      } });
    dom.window.sendImage();
    setTimeout(() => done({ alerts, navs }), 50);
  });
}
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
(async () => {
  let r = await run({ ok: true, status: 200, text: "Got it", headers: { "X-Drawing-Url": "/dragon-gallery/d/abc" } });
  check(r.alerts.length === 0 && r.navs.length === 1, "success: no popup, navigates to the lot");
  r = await run({ ok: true, status: 200, text: "Got it\nHeads up: You were in a timeout", headers: { "X-Drawing-Url": "/dragon-gallery/d/abc", "X-Show-Message": "1" } });
  check(r.alerts.length === 1 && r.alerts[0].includes("Heads up") && r.navs.length === 1, "ended-ban notice: shown, then navigates");
  r = await run({ ok: false, status: 429, text: "Too many drawings right now!", headers: {} });
  check(r.alerts.length === 1 && r.alerts[0].includes("Too many") && r.navs.length === 0, "error: popup, stays put");
  console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
