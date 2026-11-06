// Pieces the device pages share: a poller that runs only while its page is showing, labelled readouts, badges,
// an operation tracker (progress and outcome of a long-running operation, from the event stream), a small SVG line
// chart and a canvas renderer for the guide-star cut-out. Markup is made with DOM calls only (the CSP forbids the rest).

import { el } from "./dom.js";

const SVG = "http://www.w3.org/2000/svg";
export const svg = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
};

/** Calls ``tick`` every ``ms`` while started (and the stream is live), once straight away. Skips a tick still running. */
export function poller(ctx, tick, ms) {
  let timer = null;
  let busy = false;
  const run = async () => {
    if (busy) return;
    busy = true;
    try { await tick(); } finally { busy = false; }
  };
  return {
    start() { run(); timer = setInterval(() => { if (ctx.live) run(); }, ms); },
    stop() { clearInterval(timer); timer = null; },
    now: run,
  };
}

export const readout = (name) => {
  const value = el("div", { className: "value" }, "—");
  return { box: el("div", {}, el("div", { className: "name" }, name), value), set: (text) => { value.textContent = text ?? "—"; } };
};

export function badges(container, items) {
  container.replaceChildren(...items.map(([text, tone]) => el("span", { className: `badge ${tone || ""}` }, text)));
}

export const num = (value, digits = 1, unit = "") =>
  value == null || Number.isNaN(Number(value)) ? "—" : `${Number(value).toFixed(digits)}${unit}`;

/** A message line under a form: ``say(text)`` or ``say(text, true)`` for an error. */
export function messenger(node) {
  return (text, error = false) => { node.textContent = text; node.className = error ? "message error" : "message"; };
}

/** What the person sees for an operation's answer. */
export function describe(out, done) {
  if (out.status === "ok") return out.reason === "started" ? "Started." : (done || "Done.");
  return out.reason ? `${out.status}: ${out.reason}` : out.status;
}

/** Follows long-running operations whose name starts with ``prefix``: progress bar, message and the final outcome. */
export function operationTracker(ctx, prefix, bar, text) {
  let active = "";
  ctx.on("operation", ({ kind, payload }) => {
    if (!String(payload.operation || "").startsWith(prefix)) return;
    if (kind === "OperationStarted") { active = payload.correlation_id; bar.hidden = false; bar.removeAttribute("value"); text.textContent = "Started."; }
    else if (kind === "OperationProgress") {
      if (payload.fraction != null) bar.value = payload.fraction;
      if (payload.message) text.textContent = payload.message;
    } else if (kind === "OperationFinished") {
      bar.hidden = true;
      text.textContent = payload.outcome === "ok" ? "Finished." : `${payload.outcome}${payload.reason ? `: ${payload.reason}` : ""}`;
      text.className = payload.outcome === "ok" ? "message" : "message error";
      active = "";
    }
  });
  return { get active() { return active; } };
}

/** A multi-series line chart. ``series`` is ``[{label, points: [y...], tone}]`` drawn over equally spaced x. */
export function lineChart(series, { height = 140, unit = "", zero = true } = {}) {
  const width = 600;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
  const all = series.flatMap((s) => s.points).filter((v) => Number.isFinite(v));
  if (!all.length) {
    const t = svg("text", { x: width / 2, y: height / 2, class: "chart-empty", "text-anchor": "middle" });
    t.textContent = "No data yet";
    root.append(t);
    return root;
  }
  let lo = zero ? Math.min(...all, 0) : Math.min(...all), hi = zero ? Math.max(...all, 0) : Math.max(...all);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.1;
  lo -= pad; hi += pad;
  const y = (v) => height - ((v - lo) / (hi - lo)) * height;
  if (lo < 0 && hi > 0) root.append(svg("line", { x1: 0, x2: width, y1: y(0), y2: y(0), class: "chart-zero" }));
  for (const s of series) {
    const n = s.points.length;
    const d = s.points.map((v, i) => (Number.isFinite(v) ? `${i ? "L" : "M"}${(n > 1 ? (i / (n - 1)) * width : width / 2).toFixed(1)} ${y(v).toFixed(1)}` : "")).join(" ");
    root.append(svg("path", { d, class: `chart-line ${s.tone || "a"}`, fill: "none" }));
  }
  const top = svg("text", { x: 4, y: 12, class: "chart-label" });
  top.textContent = `${hi.toFixed(1)}${unit}`;
  const bottom = svg("text", { x: 4, y: height - 4, class: "chart-label" });
  bottom.textContent = `${lo.toFixed(1)}${unit}`;
  root.append(top, bottom);
  return root;
}

export function legend(series) {
  return el("div", { className: "badges" }, ...series.map((s) => el("span", { className: `badge key ${s.tone || "a"}` }, s.label)));
}

/** Paint the guide-star cut-out (uint16 little-endian, row-major, base64) onto a canvas, auto-stretched. */
export function paintStar(canvas, star) {
  const { width, height } = star;
  const raw = Uint8Array.from(atob(star.pixels), (c) => c.charCodeAt(0));
  const values = new Uint16Array(raw.buffer, raw.byteOffset, Math.floor(raw.byteLength / 2));
  let lo = 65535, hi = 0;
  for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const span = Math.max(1, hi - lo);
  canvas.width = width;
  canvas.height = height;
  const ctx2 = canvas.getContext("2d");
  const image = ctx2.createImageData(width, height);
  for (let i = 0; i < width * height; i++) {
    const g = Math.round(255 * Math.sqrt(((values[i] ?? lo) - lo) / span));      // a square-root stretch shows faint stars
    image.data.set([g, g, g, 255], i * 4);
  }
  ctx2.putImageData(image, 0, 0);
  if (star.star_pos) {
    ctx2.strokeStyle = "#ffffff";
    ctx2.strokeRect(star.star_pos[0] - 6, star.star_pos[1] - 6, 12, 12);
  }
}

/** A frame's histogram ({bins, counts}) as a filled, log-scaled area: a few bright stars must not flatten the sky. */
export function histogramChart(hist, { height = 90 } = {}) {
  const width = 600;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
  const counts = hist?.counts || [];
  if (!counts.length) return root;
  const logs = counts.map((c) => Math.log1p(c));
  const top = Math.max(...logs) || 1;
  const x = (i) => ((i / (counts.length - 1)) * width).toFixed(1);
  const d = `M0 ${height} ${logs.map((v, i) => `L${x(i)} ${(height - (v / top) * (height - 4)).toFixed(1)}`).join(" ")} L${width} ${height} Z`;
  root.append(svg("path", { d, class: "hist-area" }));
  return root;
}
