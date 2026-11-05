// The Focuser page: position and temperature, nudging by a chosen number of steps, going to a position, halting.
// Built on focus.* operations (the focus module's own manual focus, never a generic focuser move). Held motion
// (focus.hold_*) needs a keep-alive and is not offered here yet.

import { el } from "./dom.js";
import { describe, messenger, num, poller, readout } from "./common.js";

const STEPS = [1, 10, 50, 100, 500, 1000];

export function focuserPage(ctx) {
  const position = readout("Position"), temperature = readout("Temperature");
  const notice = el("div", { className: "notice warn", hidden: true });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);

  let step = 100;
  const chips = STEPS.map((n) => el("button", { type: "button", className: n === step ? "chip on" : "chip",
    onclick: () => { step = n; chips.forEach((c, i) => { c.className = STEPS[i] === step ? "chip on" : "chip"; }); } }, String(n)));
  const less = el("button", { type: "button", className: "big" }, "− In");
  const more = el("button", { type: "button", className: "big" }, "Out +");
  const halt = el("button", { type: "button", className: "danger" }, "HALT");
  const target = el("input", { type: "number", inputMode: "numeric", min: "0", step: "1", placeholder: "position" });
  const go = el("button", { type: "button", className: "primary" }, "Move to");

  const root = el("div", {}, el("h2", {}, "Focuser"), notice,
    el("div", { className: "card" }, el("div", { className: "readout" }, position.box, temperature.box)),
    el("h3", {}, "Nudge"),
    el("div", { className: "card" },
      el("div", { className: "label-line" }, "Steps per press"), el("div", { className: "chips" }, ...chips),
      el("div", { className: "row" }, less, more, halt)),
    el("h3", {}, "Go to position"),
    el("div", { className: "card" },
      el("div", { className: "row" }, el("label", {}, "Position", target), el("div", { className: "fixed" }, go)), messageNode));

  function draw(value) {
    const present = ctx.catalogue.has("focus.status");
    notice.hidden = ctx.can("focus.status") || !present;
    notice.textContent = `No focuser: ${ctx.why("focus.status")}.`;
    position.set(value?.position == null ? null : String(value.position));
    temperature.set(value?.temperature == null ? null : num(value.temperature, 1, " °C"));
    for (const b of [less, more]) b.disabled = !ctx.can("focus.move_by");
    go.disabled = target.disabled = !ctx.can("focus.move_to");
    halt.disabled = !ctx.can("focus.halt");
  }

  let last = null;
  const poll = poller(ctx, async () => {
    if (ctx.can("focus.status")) {
      const out = await ctx.run("focus.status");
      if (out.status === "ok") last = out.value;
    }
    draw(last);
  }, 2000);

  async function act(name, args, done) {
    say("Working…");
    const out = await ctx.run(name, args);
    say(describe(out, done), out.status !== "ok");
    await poll.now();
  }
  less.addEventListener("click", () => act("focus.move_by", { steps: -step }, `Moved −${step}.`));
  more.addEventListener("click", () => act("focus.move_by", { steps: step }, `Moved +${step}.`));
  halt.addEventListener("click", () => act("focus.halt", {}, "Halted."));
  go.addEventListener("click", () => {
    const p = parseInt(target.value, 10);
    if (!(p >= 0)) return say("Give a position of 0 or more.", true);
    act("focus.move_to", { position: p }, `Moving to ${p}.`);
  });

  ctx.on("catalogue", () => draw(last));
  draw(null);
  return { el: root, show() { poll.start(); }, hide() { poll.stop(); } };
}
