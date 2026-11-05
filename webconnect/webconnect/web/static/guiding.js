// The Guiding page: PHD2 connection, guiding state, error (RMS) and trace, the guide star, and the usual commands.
// Built on guiding.* operations. Guiding settles in PHD2's own time, so the page watches guiding.status.

import { el } from "./dom.js";
import { badges, describe, legend, lineChart, messenger, num, paintStar, poller, readout } from "./common.js";

export function guidingPage(ctx) {
  const raRms = readout("RA error"), decRms = readout("Dec error"), total = readout("Total"), scale = readout("Pixel scale");
  const flags = el("div", { className: "badges" });
  const notice = el("div", { className: "notice warn", hidden: true });
  const chartBox = el("div", { className: "chart-box" });
  const log = el("ul", { className: "log" });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);

  const buttons = {
    connect: el("button", { type: "button" }, "Connect to PHD2"),
    disconnect: el("button", { type: "button" }, "Disconnect"),
    find: el("button", { type: "button" }, "Find star"),
    start: el("button", { type: "button", className: "primary" }, "Start guiding"),
    recal: el("button", { type: "button" }, "Recalibrate"),
    dither: el("button", { type: "button" }, "Dither"),
    stop: el("button", { type: "button", className: "danger" }, "Stop"),
  };
  const showStar = el("input", { type: "checkbox" });
  const canvas = el("canvas", { className: "star", width: 1, height: 1 });
  const starBox = el("div", { className: "card", hidden: true }, canvas);

  const root = el("div", {}, el("h2", {}, "Guiding"), notice,
    el("div", { className: "card" }, el("div", { className: "readout" }, raRms.box, decRms.box, total.box, scale.box), flags),
    el("h3", {}, "Error trace"), el("div", { className: "card" }, chartBox),
    el("h3", {}, "Control"),
    el("div", { className: "card" },
      el("div", { className: "row" }, buttons.connect, buttons.disconnect),
      el("div", { className: "row" }, buttons.find, buttons.start, buttons.recal, buttons.dither, buttons.stop),
      el("label", { className: "check" }, showStar, "Show the guide star"), messageNode),
    starBox, el("h3", {}, "PHD2 log"), el("div", { className: "card" }, log));

  let last = null;
  function draw() {
    const s = last || {};
    const known = ctx.catalogue.has("guiding.status");
    notice.hidden = !known || ctx.can("guiding.status");
    notice.textContent = `Guiding is not available: ${ctx.why("guiding.status")}.`;
    const unit = s.unit || "";
    raRms.set(s.rms ? num(s.rms.ra, 2, ` ${unit}`) : null);
    decRms.set(s.rms ? num(s.rms.dec, 2, ` ${unit}`) : null);
    total.set(s.rms ? num(s.rms.total, 2, ` ${unit}`) : null);
    scale.set(s.pixel_scale ? num(s.pixel_scale, 2, " ″/px") : null);
    badges(flags, [
      [s.connected ? `PHD2 ${s.phd_version || ""}`.trim() : (s.configured ? `PHD2 at ${s.host}:${s.port}` : "PHD2 not set up"), s.connected ? "on" : "warn"],
      [s.app_state || "Not connected", s.guiding ? "on" : ""],
      ...(s.settling ? [["Settling", "warn"]] : []),
      ...(s.connected ? [[s.calibrated ? "Calibrated" : "Not calibrated", s.calibrated ? "on" : "warn"]] : []),
    ]);

    const trace = s.trace || [];
    const series = [{ label: "RA", tone: "a", points: trace.map((r) => r[2]) }, { label: "Dec", tone: "b", points: trace.map((r) => r[3]) }];
    chartBox.replaceChildren(lineChart(series, { unit: ` ${unit}` }), legend(series));
    log.replaceChildren(...(s.log || []).slice().reverse().map((line) => el("li", {}, typeof line === "string" ? line : JSON.stringify(line))));

    buttons.connect.disabled = !ctx.can("guiding.connect") || s.connected === true;
    buttons.disconnect.disabled = !ctx.can("guiding.disconnect") || !s.connected;
    for (const key of ["find", "start", "recal", "dither", "stop"]) {
      const op = { find: "find_star", start: "start", recal: "recalibrate", dither: "dither", stop: "stop" }[key];
      buttons[key].disabled = !ctx.can(`guiding.${op}`);
    }
    buttons.connect.title = s.why_not_connect || "";
  }

  async function refreshStar() {
    starBox.hidden = !showStar.checked;
    if (!showStar.checked || !last?.connected) return;
    const out = await ctx.run("guiding.star");
    if (out.status === "ok" && out.value?.available) paintStar(canvas, out.value);
  }

  const poll = poller(ctx, async () => {
    if (ctx.can("guiding.status")) {
      const out = await ctx.run("guiding.status");
      if (out.status === "ok") last = out.value;
    }
    draw();
    await refreshStar();
  }, 2000);

  async function act(name, args, done) {
    say("Working…");
    const out = await ctx.run(name, args);
    say(describe(out, done), out.status !== "ok");
    await poll.now();
  }
  buttons.connect.addEventListener("click", () => act("guiding.connect", {}, "Connected to PHD2."));
  buttons.disconnect.addEventListener("click", () => act("guiding.disconnect", {}, "Disconnected."));
  buttons.find.addEventListener("click", () => act("guiding.find_star", {}, "Looking for a star."));
  buttons.start.addEventListener("click", () => act("guiding.start", {}, "Guiding requested; PHD2 will settle."));
  buttons.recal.addEventListener("click", () => act("guiding.recalibrate", {}, "Calibrating, then guiding."));
  buttons.dither.addEventListener("click", () => act("guiding.dither", {}, "Dither requested."));
  buttons.stop.addEventListener("click", () => act("guiding.stop", {}, "Stopped guiding."));
  showStar.addEventListener("change", refreshStar);

  ctx.on("catalogue", draw);
  draw();
  return { el: root, show() { poll.start(); }, hide() { poll.stop(); } };
}
