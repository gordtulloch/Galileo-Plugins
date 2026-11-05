// The Solve page: take a frame and plate-solve it, optionally sync the mount, or centre on a J2000 position by
// repeating solve-and-correct. Built on solve.* operations; progress arrives on the event stream.

import { el } from "./dom.js";
import { badges, describe, messenger, num, operationTracker, poller } from "./common.js";
import { formatDec, formatRa, parseSexagesimal } from "./mount.js";

export function solvePage(ctx) {
  const notice = el("div", { className: "notice warn", hidden: true });
  const flags = el("div", { className: "badges" });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);
  const bar = el("progress", { max: 1, hidden: true });
  const progressText = el("div", { className: "message" });
  const result = el("div", { className: "card" }, el("div", { className: "dim" }, "No solve yet."));
  const log = el("ul", { className: "log" });

  const field = (label, props) => { const input = el("input", props); return { input, node: el("label", {}, label, input) }; };
  const exposure = field("Exposure (s)", { type: "number", inputMode: "decimal", min: "0.001", max: "600", step: "any", value: "5" });
  const accuracy = field("Accuracy (″)", { type: "number", inputMode: "decimal", min: "1", max: "3600", step: "any", value: "30" });
  const iterations = field("Max attempts", { type: "number", inputMode: "numeric", min: "1", max: "20", step: "1", value: "5" });
  const binning = field("Binning", { type: "number", inputMode: "numeric", min: "1", max: "8", step: "1", value: "1" });
  const gain = field("Gain", { type: "number", inputMode: "numeric", min: "0", max: "10000", step: "1", value: "0" });
  const filter = field("Filter (optional)", { type: "text", autocomplete: "off" });
  const ra = field("RA (hours, J2000)", { type: "text", placeholder: "hh:mm:ss or hours", autocomplete: "off" });
  const dec = field("Dec (degrees, J2000)", { type: "text", placeholder: "±dd:mm:ss or degrees", autocomplete: "off" });
  const name = field("Target name (optional)", { type: "text", autocomplete: "off" });

  const capture = el("button", { type: "button", className: "primary" }, "Solve");
  const sync = el("button", { type: "button" }, "Solve and sync");
  const centre = el("button", { type: "button" }, "Centre on target");
  const stop = el("button", { type: "button", className: "danger" }, "Stop");

  const root = el("div", {}, el("h2", {}, "Plate solving"), notice,
    el("div", { className: "card" }, flags,
      el("div", { className: "row" }, capture, sync, stop), bar, progressText, messageNode),
    el("h3", {}, "Centre on a position"),
    el("div", { className: "card" },
      el("div", { className: "row" }, ra.node, dec.node), el("div", { className: "row" }, name.node), el("div", { className: "row" }, centre)),
    el("details", { className: "card" }, el("summary", {}, "Solve settings"),
      el("div", { className: "row" }, exposure.node, accuracy.node), el("div", { className: "row" }, iterations.node, binning.node),
      el("div", { className: "row" }, gain.node, filter.node)),
    el("h3", {}, "Last result"), result, el("h3", {}, "Log"), el("div", { className: "card" }, log));
  operationTracker(ctx, "solve.", bar, progressText);

  const common = () => {
    const args = {
      exposure_s: parseFloat(exposure.input.value) || 5, accuracy_arcsec: parseFloat(accuracy.input.value) || 30,
      max_iterations: parseInt(iterations.input.value, 10) || 5, binning: parseInt(binning.input.value, 10) || 1,
      gain: parseInt(gain.input.value, 10) || 0 };
    if (filter.input.value.trim()) args.filter = filter.input.value.trim();
    return args;
  };

  let last = null;
  function drawResult(status) {
    const summary = status?.last;
    if (!summary) { result.replaceChildren(el("div", { className: "dim" }, "No solve yet.")); log.replaceChildren(); return; }
    const best = [...(summary.solutions || [])].reverse().find((s) => s.success);
    const rows = best ? el("div", { className: "readout" },
      ...[["Right ascension", formatRa(best.ra_deg / 15)], ["Declination", formatDec(best.dec_deg)],
          ["Rotation", num(best.rotation_deg, 1, "°")], ["Scale", num(best.scale_arcsec_px, 2, " ″/px")]].map(([n, v]) =>
        el("div", {}, el("div", { className: "name" }, n), el("div", { className: "value" }, v)))) :
      el("div", {}, summary.stopped ? "Stopped." : `Not solved${summary.solutions?.at(-1)?.failure_reason ? `: ${summary.solutions.at(-1).failure_reason}` : "."}`);
    result.replaceChildren(rows, el("div", { className: "badges" },
      el("span", { className: `badge ${summary.solved ? "on" : "warn"}` }, summary.solved ? "Solved" : "Not solved"),
      el("span", { className: "badge" }, `${(summary.solutions || []).length} attempt(s)`)));
    log.replaceChildren(...(summary.log || []).slice().reverse().map((l) => el("li", {}, l)));
  }

  function draw() {
    const known = ctx.catalogue.has("solve.status");
    notice.hidden = !known || ctx.can("solve.capture") || !ctx.why("solve.capture");
    notice.textContent = `Solving is not available: ${ctx.why("solve.capture")}.`;
    badges(flags, [[last?.running ? "Solving…" : "Idle", last?.running ? "warn" : ""]]);
    capture.disabled = !ctx.can("solve.capture");
    sync.disabled = !ctx.can("solve.sync");
    centre.disabled = !ctx.can("solve.centre");
    stop.disabled = !ctx.can("solve.stop");
  }

  const poll = poller(ctx, async () => {
    if (ctx.catalogue.has("solve.status")) {
      const out = await ctx.run("solve.status");
      if (out.status === "ok") { last = out.value; drawResult(last); }
    }
    draw();
  }, 2500);

  async function act(opName, args) {
    say("Starting…");
    const out = await ctx.run(opName, args);
    say(describe(out), out.status !== "ok");
  }
  capture.addEventListener("click", () => act("solve.capture", common()));
  sync.addEventListener("click", () => {
    if (window.confirm("Solve a frame and tell the mount where it is really pointing?")) act("solve.sync", common());
  });
  centre.addEventListener("click", () => {
    const hours = parseSexagesimal(ra.input.value), degrees = parseSexagesimal(dec.input.value);
    if (!(hours >= 0 && hours < 24)) return say("RA must be between 0 and 24 hours.", true);
    if (!(degrees >= -90 && degrees <= 90)) return say("Dec must be between −90 and +90 degrees.", true);
    const args = { ...common(), ra_deg: hours * 15, dec_deg: degrees };
    if (name.input.value.trim()) args.name = name.input.value.trim();
    act("solve.centre", args);
  });
  stop.addEventListener("click", async () => { const out = await ctx.run("solve.stop"); say(describe(out, "Stopping."), out.status !== "ok"); });

  ctx.on("catalogue", draw);
  ctx.on("operation", ({ kind, payload }) => { if (kind === "OperationFinished" && String(payload.operation).startsWith("solve.")) poll.now(); });
  draw();
  return { el: root, show() { poll.start(); }, hide() { poll.stop(); } };
}
