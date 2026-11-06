// The Camera page: sensor temperature and cooling, taking an exposure with progress, and the filter wheel.
// Built on camera.* and filter.* operations. There is no image operation in the catalogue yet, so no preview here.

import { el } from "./dom.js";
import { imageViewer } from "./imageview.js";
import { badges, describe, messenger, num, operationTracker, poller, readout } from "./common.js";

export function cameraPage(ctx) {
  const temp = readout("Sensor"), power = readout("Cooler power");
  const flags = el("div", { className: "badges" });
  const notice = el("div", { className: "notice warn", hidden: true });
  const messageNode = el("div", { className: "message", role: "status" });
  const sayHere = messenger(messageNode);

  const seconds = el("input", { type: "number", inputMode: "decimal", min: "0.001", max: "3600", step: "any", value: "10" });
  const frameType = el("select", {}, ...["Light", "Dark", "Flat", "Bias"].map((t) => el("option", { value: t }, t)));
  const gain = el("input", { type: "number", inputMode: "numeric", min: "0", max: "10000", step: "1", value: "0" });
  const offset = el("input", { type: "number", inputMode: "numeric", min: "0", max: "10000", step: "1", value: "0" });
  const binning = el("select", {}, ...[1, 2, 3, 4].map((b) => el("option", { value: b }, `${b}×${b}`)));
  const expose = el("button", { type: "button", className: "primary" }, "Expose");
  const abort = el("button", { type: "button", className: "danger" }, "Abort");
  const bar = el("progress", { max: 1, hidden: true });
  const progressText = el("div", { className: "message" });
  const target = el("input", { type: "number", inputMode: "decimal", min: "-60", max: "40", step: "any", placeholder: "°C" });
  const cool = el("button", { type: "button" }, "Set temperature");
  const filters = el("select", {});
  const pick = el("button", { type: "button" }, "Select filter");
  const filterCard = el("div", { className: "card", hidden: true }, el("div", { className: "row" },
    el("label", {}, "Filter", filters), el("div", { className: "fixed" }, pick)));

  const picture = imageViewer(ctx, { full: true });
  const root = el("div", {}, el("h2", {}, "Camera"), notice,
    el("div", { className: "card" }, el("div", { className: "readout" }, temp.box, power.box), flags,
      el("div", { className: "row" }, el("label", {}, "Target temperature (°C)", target), el("div", { className: "fixed" }, cool))),
    el("h3", {}, "Exposure"),
    el("div", { className: "card" },
      el("div", { className: "row" }, el("label", {}, "Seconds", seconds), el("label", {}, "Frame type", frameType)),
      el("div", { className: "row" }, el("label", {}, "Gain", gain), el("label", {}, "Offset", offset), el("label", {}, "Binning", binning)),
      el("div", { className: "row" }, expose, abort), bar, progressText, messageNode),
    el("h3", {}, "Filter wheel"), filterCard, picture.el);
  operationTracker(ctx, "camera.", bar, progressText);

  function draw(value) {
    const present = ctx.catalogue.has("camera.status");
    notice.hidden = ctx.can("camera.status") || !present;
    notice.textContent = `No camera: ${ctx.why("camera.status")}.`;
    temp.set(value ? num(value.temperature, 1, " °C") : null);
    power.set(value ? num(value.cooler_power, 0, " %") : null);
    badges(flags, [["Cooling", value && value.cooler_power > 0 ? "on" : ""]]);
    for (const b of [expose, cool]) b.disabled = !ctx.can(b === expose ? "camera.expose" : "camera.set_temperature");
    abort.disabled = !ctx.can("camera.abort");
    for (const i of [seconds, gain, offset, binning, frameType]) i.disabled = !ctx.can("camera.expose");
    target.disabled = cool.disabled;
    filterCard.hidden = !ctx.catalogue.has("filter.names") || !ctx.can("filter.names");
    pick.disabled = !ctx.can("filter.select");
  }

  async function loadFilters() {
    if (!ctx.can("filter.names")) return;
    const out = await ctx.run("filter.names");
    const names = Array.isArray(out.value) ? out.value : Array.isArray(out.value?.names) ? out.value.names : [];
    const keep = filters.value;
    filters.replaceChildren(...names.map((n) => el("option", { value: n }, n)));
    if (names.includes(keep)) filters.value = keep;
  }

  let last = null;
  const poll = poller(ctx, async () => {
    if (ctx.can("camera.status")) {
      const out = await ctx.run("camera.status");
      last = out.status === "ok" ? out.value : last;
    }
    draw(last);
  }, 3000);

  expose.addEventListener("click", async () => {
    const s = parseFloat(seconds.value);
    if (!(s > 0)) return sayHere("Give an exposure time in seconds.", true);
    sayHere("Starting…");
    const out = await ctx.run("camera.expose", {
      seconds: s, gain: parseInt(gain.value, 10) || 0, offset: parseInt(offset.value, 10) || 0,
      binning: parseInt(binning.value, 10), frame_type: frameType.value });
    sayHere(describe(out), out.status !== "ok");
  });
  abort.addEventListener("click", async () => { const out = await ctx.run("camera.abort"); sayHere(describe(out, "Aborted."), out.status !== "ok"); });
  cool.addEventListener("click", async () => {
    const c = parseFloat(target.value);
    if (Number.isNaN(c)) return sayHere("Give a target temperature in °C.", true);
    const out = await ctx.run("camera.set_temperature", { celsius: c });
    sayHere(describe(out, `Cooling to ${c} °C.`), out.status !== "ok");
  });
  pick.addEventListener("click", async () => {
    if (!filters.value) return;
    const out = await ctx.run("filter.select", { name: filters.value });
    sayHere(describe(out, `Filter: ${filters.value}.`), out.status !== "ok");
  });

  ctx.on("catalogue", () => { draw(last); loadFilters(); });
  draw(null);
  return { el: root, show() { poll.start(); loadFilters(); picture.show(); }, hide() { poll.stop(); picture.hide(); } };
}
