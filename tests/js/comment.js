const { JSDOM } = require("jsdom");
const fs = require("fs");
const data = JSON.parse(fs.readFileSync(__dirname + "/api2.json"));
let posted = false; const posts = [];
const audios = [];
const dom = new JSDOM(fs.readFileSync(__dirname + "/decor.html", "utf8"), {
  url: "https://canvas.example/dragon-gallery/image/1", runScripts: "dangerously",
  beforeParse(w) {
    w.fetch = (url, opts = {}) => {
      if (opts.method === "POST") { posts.push({ url, body: String(opts.body), hdr: opts.headers }); posted = true;
        return Promise.resolve({ ok: true, json: () => Promise.resolve(data.post) }); }
      const n = url.split("/").pop(); const api = posted ? data.after : data.before;
      return Promise.resolve(api[n] ? { ok: true, json: () => Promise.resolve(api[n]) } : { ok: false });
    };
    w.Image = class { set src(v) { setTimeout(() => this.onload && this.onload(), 0); } };
    w.Audio = class {
      constructor(src) { this._src = src || ""; this.paused = true; this.plays = 0; this.listeners = {}; audios.push(this); }
      set src(v) { this._src = v; } get src() { return this._src; }
      getAttribute(k) { return k === "src" ? (this._src || null) : null; }
      addEventListener(t, f) { this.listeners[t] = f; } load() {}
      play() { this.paused = false; this.plays++; return Promise.resolve(); } pause() { this.paused = true; }
    };
    w.HTMLFormElement.prototype.submit = function () { throw new Error("plain submit used (page would reload)"); };
    w.HTMLElement.prototype.scrollIntoView = function () {};
  },
});
const w = dom.window, doc = w.document, $ = (id) => doc.getElementById(id);
const click = (el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
const tick = () => new Promise((r) => setTimeout(r, 30));
let fails = 0; const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) fails++; };
(async () => {
  const music = audios[0];
  const seq = [];
  for (let i = 0; i < 6; i++) { click($("quartet")); await tick(); seq.push(music.src.split("/").pop().split("?")[0]); click($("quartet")); await tick(); }
  check(seq.join(",") === "spring.mp3,summer.mp3,autumn.mp3,winter.mp3,minuet.mp3,spring.mp3", "each switch-on plays the next season: " + seq.join(","));
  click($("quartet")); await tick();                    // music on (summer)
  click($("spike")); await tick();                      // wine on
  const form = $("comment-form");
  form.querySelector("textarea").value = "<b>bravo</b>"; form.querySelector("input[name=name]").value = "Sir";
  form.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true })); await tick(); await tick();
  check(posts.length === 1 && posts[0].url.endsWith("/dragon-gallery/image/1/comments") && posts[0].hdr["X-Requested-With"] === "fetch", "posted via fetch with the header");
  check(/body=%3Cb%3Ebravo%3C%2Fb%3E/.test(posts[0].body) && /name=Sir/.test(posts[0].body), "form fields sent");
  check(!music.paused, "music still playing after posting");
  check(doc.body.classList.contains("wine"), "wine mode still on after posting");
  const list = $("comment-list").textContent;
  check(list.includes("<b>bravo</b>") && !$("comment-list").querySelector("b"), "new comment shown, as text");
  check($("notices").textContent.includes("Your comment is up"), "notice shown");
  check(form.querySelector("textarea").value === "", "textarea cleared");
  console.log(fails ? fails + " FAILED" : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
