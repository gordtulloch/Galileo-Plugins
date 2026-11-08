// The Polar alignment page: the Solve screen's polar-alignment routine, for someone standing at the mount with a phone
// or tablet. polar.start measures with three plate solves and then keeps solving while the altitude and azimuth knobs
// are turned; the error, its trend and where the pole lies in the frame arrive as the observable state "polar.state"
// on every change, so the readouts follow the knobs without polling. polar.status is read once on opening and as a
// slow fallback in case the stream is down.

import { el } from "./dom.js";
import { badges, describe, lineChart, messenger, operationTracker, poller, svg } from "./common.js";

const BAND = { good: "on", fair: "warn", poor: "bad" };
const MIN_RANGE_ARCMIN = 5;               // the plot never zooms in tighter than this, so a small error looks small

/** An angle as a polar-alignment screen reads it: 45″, 3′12″ or 1°02′05″. */
export function formatAngle(arcsec) {
  const total = Math.round(Math.abs(arcsec));
  const degrees = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  if (degrees) return `${degrees}°${String(minutes).padStart(2, "0")}′${String(seconds).padStart(2, "0")}″`;
  if (minutes) return `${minutes}′${String(seconds).padStart(2, "0")}″`;
  return `${seconds}″`;
}

/** The pole (X) against the point the axis aims at (circle, fixed at the centre); bring the X into the circle. */
function targetPlot(offset) {
  const size = 240, mid = size / 2;
  const root = svg("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart polar-target", role: "img" });
  root.append(svg("line", { x1: 0, x2: size, y1: mid, y2: mid, class: "chart-zero" }),
    svg("line", { x1: mid, x2: mid, y1: 0, y2: size, class: "chart-zero" }),
    svg("circle", { cx: mid, cy: mid, r: 14, class: "polar-axis", fill: "none" }));
  if (!offset) {
    const t = svg("text", { x: mid, y: size - 10, class: "chart-empty", "text-anchor": "middle" });
    t.textContent = "No field geometry from this solver";
    root.append(t);
    return root;
  }
  const range = Math.max(MIN_RANGE_ARCMIN, Math.abs(offset[0]) * 1.2, Math.abs(offset[1]) * 1.2);
  const px = (v) => Math.max(10, Math.min(size - 10, mid + (v / range) * (mid - 12)));
  const x = px(offset[0]), y = px(offset[1]);
  root.append(svg("line", { x1: x - 8, y1: y - 8, x2: x + 8, y2: y + 8, class: "polar-pole" }),
    svg("line", { x1: x - 8, y1: y + 8, x2: x + 8, y2: y - 8, class: "polar-pole" }));
  const label = svg("text", { x: 4, y: 12, class: "chart-label" });
  label.textContent = `± ${range.toFixed(1)}′`;
  root.append(label);
  return root;
}

export function polarPage(ctx) {
  const notice = el("div", { className: "notice warn", hidden: true });
  const flags = el("div", { className: "badges" });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);
  const bar = el("progress", { max: 1, hidden: true });
  const progressText = el("div", { className: "message" });
  const phaseText = el("div", { className: "message", role: "status", "aria-live": "polite" });
  const total = el("div", { className: "polar-total", "aria-live": "polite" }, "—");
  const altitude = el("div", { className: "polar-advice" });
  const azimuth = el("div", { className: "polar-advice" });
  const hint = el("div", { className: "dim" });
  const plotBox = el("div", { className: "chart-box" });
  const trendBox = el("div", { className: "chart-box" });
  const log = el("ul", { className: "log" });

  const field = (label, props) => { const input = el("input", props); return { input, node: el("label", {}, label, input) }; };
  const exposure = field("Exposure (s)", { type: "number", inputMode: "decimal", min: "0.001", max: "600", step: "any", value: "5" });
  const settle = field("Settle (s)", { type: "number", inputMode: "decimal", min: "0", max: "60", step: "any", value: "1.5" });
  const step = field("Turn between solves (°)", { type: "number", inputMode: "decimal", min: "5", max: "60", step: "any", value: "30" });
  const direction = el("select", {}, el("option", { value: "east" }, "East"), el("option", { value: "west" }, "West"));
  const refraction = el("input", { type: "checkbox", checked: true });
  const zenith = el("input", { type: "checkbox", checked: true });

  const start = el("button", { type: "button", className: "primary" }, "Start polar alignment");
  const stop = el("button", { type: "button", className: "danger" }, "Stop");

  const root = el("div", {}, el("h2", {}, "Polar alignment"), notice,
    el("div", { className: "card" }, flags, el("div", { className: "row" }, start, stop), bar, progressText, messageNode),
    el("h3", {}, "Adjust"),
    el("div", { className: "card" }, phaseText, total, altitude, azimuth, hint),
    el("div", { className: "grid" },
      el("div", { className: "card" }, el("h3", {}, "Pole and axis"), plotBox),
      el("div", { className: "card" }, el("h3", {}, "Error over time"), trendBox)),
    el("details", { className: "card" }, el("summary", {}, "Settings"),
      el("div", { className: "row" }, exposure.node, settle.node), el("div", { className: "row" }, step.node,
        el("label", {}, "Turn the mount", direction)),
      el("label", { className: "check" }, refraction, " Allow for atmospheric refraction"),
      el("label", { className: "check" }, zenith, " Go to the zenith and check the mount first")),
    el("h3", {}, "Log"), el("div", { className: "card" }, log));
  operationTracker(ctx, "polar.", bar, progressText);

  let last = null;
  function draw() {
    const known = ctx.catalogue.has("polar.status");
    const why = ctx.why("polar.start");
    const running = !!last?.running;
    notice.hidden = !known || running || ctx.can("polar.start") || !why;
    notice.textContent = `Polar alignment is not available: ${why}.`;
    start.disabled = !ctx.can("polar.start");
    stop.disabled = !ctx.can("polar.stop");
    const band = last?.error?.quality;
    badges(flags, [[running ? (last.phase === "adjusting" ? "Adjusting" : "Measuring") : last?.phase === "finished" ? "Finished" : "Idle",
      running ? "warn" : ""], ...(band ? [[`Error ${band}`, BAND[band]]] : [])]);
  }

  function drawPicture(picture) {
    last = picture;
    const error = picture?.error;
    phaseText.textContent = picture?.phase === "measuring" && picture.step
      ? `Measuring (${picture.step} of ${picture.steps}): ${picture.message}` : picture?.message || "";
    total.textContent = error ? formatAngle(error.total_arcsec) : "—";
    total.className = `polar-total ${error?.quality || ""}`;
    altitude.textContent = error?.altitude_advice || "";
    azimuth.textContent = error?.azimuth_advice || "";
    hint.textContent = !error ? "" : picture.target_offset_arcmin
      ? "Turn the altitude and azimuth adjusters to bring the X (the pole) into the circle (where the axis points)."
      : "This solver gives no field geometry, so the X and circle can't be drawn; follow the adjustments above.";
    plotBox.replaceChildren(targetPlot(error ? picture.target_offset_arcmin : null));
    const series = [{ label: "Total error (″)", tone: "a", points: error?.trend || [] }];
    trendBox.replaceChildren(lineChart(series, { unit: "″", zero: true }));
    log.replaceChildren(...(picture?.log || []).slice().reverse().map((l) => el("li", {}, l)));
    draw();
  }

  async function read() {
    if (!ctx.catalogue.has("polar.status")) return draw();
    const out = await ctx.run("polar.status");
    if (out.status === "ok") drawPicture(out.value);
  }
  const poll = poller(ctx, read, 10000);                      // the state stream does the live work; this is the fallback

  async function act(opName, args) {
    say("Starting…");
    const out = await ctx.run(opName, args);
    say(describe(out), out.status !== "ok");
  }
  start.addEventListener("click", () => act("polar.start", {
    exposure_s: parseFloat(exposure.input.value) || 5, settle_s: Math.max(0, parseFloat(settle.input.value) || 0),
    step_deg: parseFloat(step.input.value) || 30, direction: direction.value,
    refraction: refraction.checked, slew_from_zenith: zenith.checked }));
  stop.addEventListener("click", async () => { const out = await ctx.run("polar.stop"); say(describe(out, "Stopping."), out.status !== "ok"); });

  ctx.on("catalogue", draw);
  ctx.on("state", (d) => { if (d?.key === "polar.state" && d.value) drawPicture(d.value); });
  ctx.on("operation", ({ kind, payload }) => { if (kind === "OperationFinished" && String(payload.operation).startsWith("polar.")) poll.now(); });
  draw();
  return { el: root, show() { poll.start(); }, hide() { poll.stop(); } };
}
