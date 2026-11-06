// Drives the real pier.js against a tiny fake DOM and EventSource and prints a JSON report (used by test_wcon_stale.py).
import { pathToFileURL } from "node:url";
import path from "node:path";

class Node_ {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.hidden = false; this.textContent = ""; this.handlers = {}; this.classList = { toggle() {}, add() {}, remove() {} }; }
  append(...k) { this.children.push(...k); } replaceChildren(...k) { this.children = k; }
  get lastChild() { return this.children.at(-1); } prepend(...k) { this.children.unshift(...k); }
  setAttribute(k, v) { this.attrs[k] = v; } removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(n, f) { (this.handlers[n] ||= []).push(f); } remove() {}
  getContext() { return {}; }
}
const byId = {};
globalThis.document = { createElement: (t) => new Node_(t), createElementNS: (_, t) => new Node_(t),
  getElementById: (id) => (byId[id] ||= new Node_("div")), body: new Node_("body"), documentElement: new Node_("html") };
document.body.classList = { toggle() {}, add() {}, remove() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {} };
globalThis.localStorage = globalThis.sessionStorage;
globalThis.window = { confirm: () => true };
let es;
globalThis.EventSource = class { constructor() { this.l = {}; es = this; } addEventListener(n, f) { this.l[n] = f; } close() {} };
const sent = [];
const catalogue = { catalogue: { operations: [
  { name: "mount.slew", class: "control", available: true, args: [] },
  { name: "mount.abort", class: "emergency", available: true, args: [] }] } };
const api = async (p, o = {}) => {
  if (p.endsWith("/catalogue")) return { http: 200, body: catalogue };
  if (p.endsWith("/state")) return { http: 200, body: { values: { "mount.tracking": true } } };
  sent.push(p); return { http: 200, body: { status: "ok" } };
};
const { openPier } = await import(pathToFileURL(path.resolve(process.argv[2], "pier.js")).href);
let ctxRef;
const real = EventSource;
const root = new Node_("div"), tabs = new Node_("nav");
openPier(api, { id: "p1", name: "P1" }, root, tabs);
const emit = (name, data = {}) => es.l[name]({ data: JSON.stringify(data) });
const tick = () => new Promise((r) => setTimeout(r, 20));
const buttons = () => {
  const out = {};
  const walk = (x) => { if (!x || typeof x === "string") return;
    if (x.tag === "button" && x.type === "submit") out[x.children[0]] = x.disabled;
    x.children.forEach(walk); };
  walk(root); return out;
};
const stale = root.children[0];
const report = {};
await tick();
emit("hello"); await tick();
report.liveHidden = stale.hidden;
report.live = buttons();
emit("down", { reason: "x" }); await tick();
report.downShown = !stale.hidden; report.downText = stale.textContent;
report.down = buttons();
emit("hello"); await tick();
report.back = buttons(); report.backHidden = stale.hidden;
console.log(JSON.stringify(report));
process.exit(0);
