// The Pier screen shell: the control lease, the live event stream, the operation catalogue and a bottom/side tab bar.
// Pages (mount.js, operations.js ...) are given a context and draw themselves; nothing here knows what a mount is.
// The Master decides what is allowed; this only presents it. All markup is built with DOM calls, never from strings.

import { el } from "./dom.js";
import { mountPage } from "./mount.js";
import { cameraPage } from "./camera.js";
import { focuserPage } from "./focuser.js";
import { guidingPage } from "./guiding.js";
import { solvePage } from "./solve.js";
import { sessionsPage } from "./sessions.js";
import { historyPage } from "./history.js";
import { statusPage, operationsPage } from "./operations.js";


const PAGES = [
  { id: "mount", label: "Mount", icon: "⌖", build: mountPage },
  { id: "camera", label: "Camera", icon: "◉", build: cameraPage },
  { id: "focuser", label: "Focus", icon: "◎", build: focuserPage },
  { id: "guiding", label: "Guiding", icon: "✛", build: guidingPage },
  { id: "solve", label: "Solve", icon: "✵", build: solvePage },
  { id: "sessions", label: "Session", icon: "▶", build: sessionsPage },
  { id: "history", label: "History", icon: "≡", build: historyPage },
  { id: "status", label: "Status", icon: "▤", build: statusPage },
  { id: "operations", label: "More", icon: "☰", build: operationsPage },
];

export function openPier(api, pier, root, tabs) {
  const state = new Map();
  const catalogue = new Map();
  const listeners = { state: new Set(), catalogue: new Set(), event: new Set(), live: new Set(), operation: new Set() };
  let stream = null;
  let closed = false;
  let live = false;
  const path = (rest) => `/api/v1/pier/${encodeURIComponent(pier.id)}/${rest}`;
  const emit = (name, value) => listeners[name].forEach((fn) => fn(value));

  // -- the context pages use ---------------------------------------------------------------------------
  const ctx = {
    pier, state, catalogue,
    on(name, fn) { listeners[name].add(fn); return () => listeners[name].delete(fn); },
    get live() { return live; },
    /** Run one operation. A dangerous one answers with a confirmation token; the person is asked, then it re-sends. */
    async run(name, args = {}) {
      const key = crypto.randomUUID();
      const send = (token) => api(path(`ops/${name}`), {
        method: "POST", body: JSON.stringify({ args }),
        headers: { "Idempotency-Key": key, ...(token ? { "X-Confirm-Token": token } : {}) },
      });
      let { body } = await send("");
      if (body.confirm_token) {
        if (!window.confirm(`${body.reason || "Are you sure?"}`)) return { status: "cancelled", reason: "Cancelled." };
        ({ body } = await send(body.confirm_token));
      }
      return body;
    },
    /** True when the Master would run this operation for this browser now (device present, lease held). */
    can(name) { return catalogue.get(name)?.available === true; },
    why(name) { return catalogue.get(name)?.reason || ""; },
    refreshCatalogue: loadCatalogue,
  };

  // -- layout --------------------------------------------------------------------------------------------
  const leaseBar = el("div", { className: "notice lease" });
  const body = el("div");
  root.replaceChildren(leaseBar, body);
  document.getElementById("pier-name").textContent = pier.name || pier.id;

  const built = new Map();
  let active = "";
  function show(id) {
    active = id;
    for (const page of PAGES) {
      const instance = built.get(page.id);
      if (instance) { instance.el.hidden = page.id !== id; if (page.id === id) instance.show?.(); else instance.hide?.(); }
    }
    for (const button of tabs.children) button.setAttribute("aria-current", button.dataset.page === id ? "page" : "false");
    sessionStorage.setItem("wc-page", id);
  }
  for (const page of PAGES) {
    const instance = page.build(ctx);
    instance.el.hidden = true;
    built.set(page.id, instance);
    body.append(instance.el);
    tabs.append(el("button", { type: "button", onclick: () => show(page.id), title: page.label },
      el("span", { className: "icon", ariaHidden: "true" }, page.icon), el("span", {}, page.label)));
    tabs.lastChild.dataset.page = page.id;
  }
  tabs.hidden = false;
  document.body.classList.add("has-nav");

  // -- lease -----------------------------------------------------------------------------------------------
  function drawLease(message, tone) {
    leaseBar.className = `notice lease ${tone || ""}`;
    leaseBar.replaceChildren(el("div", {}, message || "Viewing. Take control to operate."));
    const row = el("div", { className: "lease-buttons" });
    for (const [label, action, cls] of [["Take control", "acquire", "primary"], ["Release", "release", ""]]) {
      row.append(el("button", { type: "button", className: cls, onclick: async () => {
        const { body: out } = await api(path(`lease/${action}`), { method: "POST", body: "{}" });
        if (out.status === "ok") drawLease(action === "release" ? "Control released. You are viewing." : "You have control.", "");
        else drawLease(out.reason || "Could not change control.", "warn");
        await loadCatalogue();
      } }, label));
    }
    leaseBar.append(row);
  }

  // -- data ---------------------------------------------------------------------------------------------------
  async function loadState() {
    const { body: out } = await api(path("state"));
    state.clear();
    for (const [key, value] of Object.entries(out.values || {})) state.set(key, value);
    emit("state", null);
  }

  async function loadCatalogue() {
    const { body: out } = await api(path("catalogue"));
    catalogue.clear();
    for (const op of out.catalogue?.operations || []) catalogue.set(op.name, op);
    emit("catalogue", null);
  }

  function setLive(value) {
    live = value;
    document.body.classList.toggle("offline", !value);
    emit("live", value);
  }

  function listen() {
    stream = new EventSource("/api/v1/events");
    const mine = (e) => {
      const d = JSON.parse(e.data);
      const where = d.pier ?? d.payload?.pier;                  // events carry their Pier in the payload
      return where === undefined || where === "" || where === pier.id ? d : null;
    };
    stream.addEventListener("state", (e) => {
      const d = mine(e);
      if (d && d.key) { state.set(d.key, d.value); emit("state", d); }
    });
    stream.addEventListener("lease", (e) => { const d = mine(e); if (d) { emit("event", `lease: ${d.holder || "free"} (${d.reason || ""})`); loadCatalogue(); } });
    stream.addEventListener("event", (e) => {
      const d = mine(e);
      if (!d) return;
      emit("event", `${d.name} ${d.payload?.operation || d.source || ""}`);
      if (String(d.name).startsWith("Operation")) emit("operation", { kind: d.name, payload: d.payload || {} });
    });
    stream.addEventListener("resync", () => { emit("event", "missed some updates; refreshing"); loadState(); });
    stream.addEventListener("hello", () => { setLive(true); loadState(); loadCatalogue(); });
    for (const name of ["down", "signed-out"]) stream.addEventListener(name, (e) => {
      setLive(false);                                  // what is on screen is stale until the stream is back
      emit("event", `${name}: ${JSON.parse(e.data).reason || ""}`);
      if (name === "signed-out") close();
    });
    stream.onerror = () => setLive(false);
  }

  function close() {
    closed = true;
    stream?.close();
    for (const instance of built.values()) instance.hide?.();
    tabs.replaceChildren();
    tabs.hidden = true;
    document.body.classList.remove("has-nav");
  }

  drawLease();
  loadCatalogue();
  const remembered = sessionStorage.getItem("wc-page");
  show(PAGES.some((p) => p.id === remembered) ? remembered : PAGES[0].id);
  listen();
  return { close, get closed() { return closed; } };
}
