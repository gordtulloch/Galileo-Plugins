// The Sessions page: what is running and how far it has got, pause / resume / stop, starting a saved session
// template (optionally on a target), and the scheduler's queue. Built on session.* and scheduler.* operations.

import { el } from "./dom.js";
import { badges, describe, messenger, poller } from "./common.js";
import { formatDec, formatRa, parseSexagesimal } from "./mount.js";

export function sessionsPage(ctx) {
  const notice = el("div", { className: "notice warn", hidden: true });
  const flags = el("div", { className: "badges" });
  const title = el("h3", {}, "Nothing running");
  const bar = el("progress", { max: 1, value: 0 });
  const detail = el("div", { className: "message" });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);
  const pause = el("button", { type: "button" }, "Pause");
  const resume = el("button", { type: "button" }, "Resume");
  const stop = el("button", { type: "button", className: "danger" }, "Stop");

  const templates = el("select", {});
  const target = el("input", { type: "text", autocomplete: "off", placeholder: "e.g. M 31" });
  const ra = el("input", { type: "text", autocomplete: "off", placeholder: "hh:mm:ss or hours" });
  const dec = el("input", { type: "text", autocomplete: "off", placeholder: "±dd:mm:ss or degrees" });
  const slot = el("input", { type: "text", autocomplete: "off", placeholder: "primary" });
  const run = el("button", { type: "button", className: "primary" }, "Start session");
  const queue = el("div", { className: "card" });

  const root = el("div", {}, el("h2", {}, "Sessions"), notice,
    el("div", { className: "card" }, flags, title, bar, detail, el("div", { className: "row" }, pause, resume, stop)),
    el("h3", {}, "Start a session"),
    el("div", { className: "card" },
      el("div", { className: "row" }, el("label", {}, "Saved session", templates)),
      el("details", {}, el("summary", {}, "Target (for a template with a placeholder target)"),
        el("div", { className: "row" }, el("label", {}, "Name", target)),
        el("div", { className: "row" }, el("label", {}, "RA (hours)", ra), el("label", {}, "Dec (degrees)", dec)),
        el("div", { className: "row" }, el("label", {}, "Camera slot", slot))),
      el("div", { className: "row" }, run), messageNode),
    el("h3", {}, "Scheduler queue"), queue);

  let last = null;
  function draw() {
    const known = ctx.catalogue.has("session.status");
    notice.hidden = !known || ctx.can("session.status");
    notice.textContent = `Sessions are not available: ${ctx.why("session.status")}.`;
    const s = last || { state: "idle" };
    const running = s.state && s.state !== "idle";
    title.textContent = running ? `${s.name || "Session"} — ${s.state}` : "Nothing running";
    const fraction = s.frames_total ? s.frames_done / s.frames_total : 0;
    bar.hidden = !running;
    bar.value = fraction;
    detail.textContent = running ? [s.block && `${s.block} (${s.block_index + 1} of ${s.block_total || "?"})`,
      s.frames_total ? `${s.frames_done} of ${s.frames_total} frames` : "", s.message].filter(Boolean).join(" · ") : "";
    badges(flags, [[running ? s.state : "Idle", running ? (s.state === "paused" ? "warn" : "on") : ""]]);
    pause.disabled = !ctx.can("session.pause") || s.state === "paused";
    resume.disabled = !ctx.can("session.resume") || s.state !== "paused";
    stop.disabled = !ctx.can("session.stop");
    run.disabled = !ctx.can("session.run") || !templates.value;
    for (const i of [templates, target, ra, dec, slot]) i.disabled = !ctx.can("session.run");
  }

  async function loadTemplates() {
    if (!ctx.catalogue.has("session.templates")) return;
    const out = await ctx.run("session.templates");
    const names = Array.isArray(out.value?.templates) ? out.value.templates : [];
    const keep = templates.value;
    templates.replaceChildren(...(names.length ? names : [""]).map((n) => el("option", { value: n }, n || "No saved sessions")));
    if (names.includes(keep)) templates.value = keep;
    draw();
  }

  function drawQueue(value) {
    const jobs = value?.jobs || [];
    if (!jobs.length) { queue.replaceChildren(el("div", { className: "dim" }, "No jobs queued.")); return; }
    queue.replaceChildren(...jobs.map((j) => {
      const coords = j.target_ra != null && j.target_dec != null ? `${formatRa(j.target_ra / 15)} ${formatDec(j.target_dec)}` : "";
      const done = j.total_required ? `${j.frames_captured}/${j.total_required} frames` : "";
      return el("div", { className: "job" },
        el("div", {}, el("strong", {}, j.name), j.name === value.next ? el("span", { className: "badge on" }, "next") : ""),
        el("small", { className: "dim" }, [j.run_state, done, coords, j.kind].filter(Boolean).join(" · ")));
    }));
  }

  const poll = poller(ctx, async () => {
    if (ctx.can("session.status")) {
      const out = await ctx.run("session.status");
      if (out.status === "ok") last = out.value;
    }
    if (ctx.can("scheduler.jobs")) {
      const out = await ctx.run("scheduler.jobs");
      if (out.status === "ok") drawQueue(out.value);
    }
    draw();
  }, 2500);

  async function act(opName, args, done) {
    say("Working…");
    const out = await ctx.run(opName, args);
    say(describe(out, done), out.status !== "ok");
    await poll.now();
  }
  pause.addEventListener("click", () => act("session.pause", {}, "Paused."));
  resume.addEventListener("click", () => act("session.resume", {}, "Resumed."));
  stop.addEventListener("click", () => { if (window.confirm("Stop the running session?")) act("session.stop", {}, "Stopped."); });
  run.addEventListener("click", () => {
    const args = { template: templates.value };
    if (slot.value.trim()) args.camera_slot = slot.value.trim();
    if (target.value.trim()) {
      const hours = parseSexagesimal(ra.value), degrees = parseSexagesimal(dec.value);
      if (!(hours >= 0 && hours < 24) || !(degrees >= -90 && degrees <= 90)) return say("A target needs a name, an RA (0–24 h) and a Dec (±90°).", true);
      Object.assign(args, { target_name: target.value.trim(), ra_deg: hours * 15, dec_deg: degrees });
    }
    if (window.confirm(`Start "${args.template}"?`)) act("session.run", args, "Session started.");
  });
  templates.addEventListener("change", draw);

  ctx.on("catalogue", () => { draw(); loadTemplates(); });
  draw();
  return { el: root, show() { poll.start(); loadTemplates(); }, hide() { poll.stop(); } };
}
