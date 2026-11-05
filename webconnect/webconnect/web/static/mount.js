// The Mount page: where the telescope is, whether it is tracking, parked or slewing, and the controls an observer
// reaches for at the telescope (STOP, track, park, slew, sync). Built from the Control Surface's mount.* operations;
// it presents, the Master decides (lease, limits, horizon, confirmation).

import { el } from "./dom.js";

const POLL_MS = 2000;

// -- sky coordinates: what the person types, and what is shown -----------------------------------------------
const pad = (n, width = 2) => String(Math.trunc(n)).padStart(width, "0");

export function formatRa(hours) {
  if (hours == null || Number.isNaN(hours)) return "—";
  const total = ((hours % 24) + 24) % 24 * 3600;
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return `${pad(h)}h ${pad(m)}m ${s.toFixed(1).padStart(4, "0")}s`;
}

export function formatDec(deg) {
  if (deg == null || Number.isNaN(deg)) return "—";
  const sign = deg < 0 ? "−" : "+";
  const total = Math.abs(deg) * 3600;
  const d = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return `${sign}${pad(d)}° ${pad(m)}′ ${Math.round(s).toString().padStart(2, "0")}″`;
}

/** "12:34:56", "12 34 56", "12h34m56s" or a decimal number -> a number in the unit of the first field (hours or degrees). */
export function parseSexagesimal(text) {
  const t = String(text).trim().replace(/[hdms°′″'"]/gi, " ").replace(/:/g, " ").replace(/−/g, "-");
  if (!t) return NaN;
  const parts = t.split(/\s+/).map(Number);
  if (parts.length > 3 || parts.some(Number.isNaN)) return NaN;
  const negative = /^-/.test(t);
  const [a, b = 0, c = 0] = parts.map(Math.abs);
  if (b >= 60 || c >= 60) return NaN;
  const value = a + b / 60 + c / 3600;
  return negative ? -value : value;
}

const readout = (name) => {
  const value = el("div", { className: "value" }, "—");
  return { box: el("div", {}, el("div", { className: "name" }, name), value), set: (text) => { value.textContent = text; } };
};

export function mountPage(ctx) {
  const ra = readout("Right ascension"), dec = readout("Declination"), alt = readout("Altitude"), az = readout("Azimuth");
  const lst = readout("Sidereal time"), side = readout("Pier side");
  const badges = el("div", { className: "badges" });
  const notice = el("div", { className: "notice warn", hidden: true });
  const message = el("div", { className: "message", role: "status" });

  const track = el("input", { type: "checkbox" });
  const stop = el("button", { type: "button", className: "danger" }, "STOP");
  const park = el("button", { type: "button" }, "Park");
  const raInput = el("input", { type: "text", inputMode: "text", placeholder: "hh:mm:ss or hours", autocomplete: "off" });
  const decInput = el("input", { type: "text", inputMode: "text", placeholder: "±dd:mm:ss or degrees", autocomplete: "off" });
  const slew = el("button", { type: "button", className: "primary" }, "Slew to");
  const sync = el("button", { type: "button" }, "Sync");

  const root = el("div", {},
    el("h2", {}, "Mount"), notice,
    el("div", { className: "card" },
      el("div", { className: "readout" }, ra.box, dec.box, alt.box, az.box, lst.box, side.box), badges),
    el("h3", {}, "Control"),
    el("div", { className: "card" },
      el("div", { className: "row" }, stop,
        el("label", { className: "check fixed" }, track, "Tracking"), park)),
    el("h3", {}, "Go to coordinates"),
    el("div", { className: "card" },
      el("div", { className: "row" },
        el("label", {}, "RA (hours)", raInput), el("label", {}, "Dec (degrees)", decInput)),
      el("div", { className: "row" }, slew, sync),
      el("small", { className: "dim" }, "Same coordinates as the readout above, as the mount reports them. Sync tells the mount it is pointing here."),
      message));

  function say(text, error = false) {
    message.textContent = text;
    message.className = error ? "message error" : "message";
  }

  // -- reading the mount ---------------------------------------------------------------------------------------
  let status = null;
  function draw() {
    const present = ctx.catalogue.has("mount.status");
    const usable = ctx.can("mount.status");
    if (!present) {
      notice.hidden = false;
      notice.textContent = "Loading what this Pier offers…";
    } else if (!usable) {
      notice.hidden = false;
      notice.textContent = `The mount is not available: ${ctx.why("mount.status")}.`;
      status = null;
    } else if (!ctx.can("mount.slew")) {
      notice.hidden = false;
      notice.textContent = ctx.why("mount.slew") ? `You can watch but not operate: ${ctx.why("mount.slew")}.` : "";
      notice.hidden = !notice.textContent;
    } else notice.hidden = true;

    const s = status || {};
    ra.set(formatRa(s.right_ascension));
    dec.set(formatDec(s.declination));
    alt.set(s.altitude == null ? "—" : `${s.altitude.toFixed(1)}°`);
    az.set(s.azimuth == null ? "—" : `${s.azimuth.toFixed(1)}°`);
    lst.set(formatRa(s.sidereal_time));
    side.set(s.side_of_pier || "—");

    const flags = [["Tracking", s.tracking, s.tracking_rate ? ` (${s.tracking_rate})` : ""], ["Slewing", s.slewing, ""], ["Parked", s.at_park, ""]];
    badges.replaceChildren(...flags.map(([name, on, extra]) =>
      el("span", { className: `badge ${on ? (name === "Slewing" ? "warn" : "on") : ""}` }, `${name}${on ? extra : ""}`)));

    track.checked = s.tracking === true;
    park.textContent = s.at_park ? "Unpark" : "Park";
    track.disabled = !ctx.can("mount.track");
    stop.disabled = !ctx.can("mount.abort_slew");
    park.disabled = !ctx.can(s.at_park ? "mount.unpark" : "mount.park");
    for (const b of [slew, sync]) b.disabled = !ctx.can(b === slew ? "mount.slew" : "mount.sync");
    for (const i of [raInput, decInput]) i.disabled = !ctx.can("mount.slew");
  }

  let timer = null;
  let polling = false;
  async function poll() {
    if (polling || !ctx.can("mount.status")) return draw();
    polling = true;
    try {
      const out = await ctx.run("mount.status");
      if (out.status === "ok" && out.value && typeof out.value === "object") status = out.value;
    } finally { polling = false; }
    draw();
  }

  // -- acting --------------------------------------------------------------------------------------------------
  async function act(name, args, done) {
    say("Working…");
    const out = await ctx.run(name, args);
    if (out.status === "ok") say(done);
    else say(out.reason ? `${out.status}: ${out.reason}` : out.status, out.status !== "cancelled");
    await poll();
  }

  function coordinates() {
    const hours = parseSexagesimal(raInput.value), degrees = parseSexagesimal(decInput.value);
    if (!(hours >= 0 && hours < 24)) { say("RA must be between 0 and 24 hours.", true); return null; }
    if (!(degrees >= -90 && degrees <= 90)) { say("Dec must be between −90 and +90 degrees.", true); return null; }
    return { ra_deg: hours * 15, dec_deg: degrees };
  }

  stop.addEventListener("click", () => act("mount.abort_slew", {}, "Stopped."));
  track.addEventListener("change", () => act("mount.track", { enabled: track.checked }, track.checked ? "Tracking on." : "Tracking off."));
  park.addEventListener("click", () => (status?.at_park ? act("mount.unpark", {}, "Unparking.") : act("mount.park", {}, "Parking.")));
  slew.addEventListener("click", () => { const c = coordinates(); if (c) act("mount.slew", c, "Slewing."); });
  sync.addEventListener("click", () => {
    const c = coordinates();
    if (c && window.confirm("Tell the mount it is pointing at these coordinates? This changes its pointing model.")) act("mount.sync", c, "Synced.");
  });

  ctx.on("catalogue", draw);
  ctx.on("live", (live) => { if (live) poll(); });
  draw();
  return {
    el: root,
    show() { poll(); timer = setInterval(() => { if (ctx.live) poll(); }, POLL_MS); },
    hide() { clearInterval(timer); timer = null; },
  };
}
