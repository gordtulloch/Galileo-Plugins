// The picture viewer, embedded in the page that needs it (Camera, Focus, Solve): the Pier's current exposure or a
// Library frame as a server-rendered picture, with clipping, detail and binning, star statistics, and pan and zoom
// by tiles. ``full`` (the Camera page) adds a choice of frame, a histogram and blink / side-by-side comparison of two
// frames. The browser decodes no FITS and runs no image processing: it shows JPEGs the Master rendered
// (WCON-600, 610, 630, 640).

import { el } from "./dom.js";
import { histogramChart, messenger, num } from "./common.js";

const DETAIL = { Fast: { size: 512, quality: 70 }, Balanced: { size: 1024, quality: 85 }, Full: { size: 2048, quality: 92 } };
const TILE = 256;
const BLINK_MS = 700;

export function imageViewer(ctx, { full = false } = {}) {
  const params = { stretch: 50, bin: 1, detail: "Balanced" };
  let source = "current", chosen = false, other = "", version = 0, mode = "off", blinkTimer = null, level = 0, meta = null, shown = false;

  const notice = el("div", { className: "notice warn", hidden: true });
  const messageNode = el("div", { className: "message", role: "status" });
  const say = messenger(messageNode);

  const picker = el("select", {});
  const pickerB = el("select", {});
  const refresh = el("button", { type: "button" }, "Refresh");
  const clip = el("input", { type: "range", min: "0", max: "100", step: "1", value: String(params.stretch) });
  const clipValue = el("span", { className: "dim" }, String(params.stretch));
  const detail = el("select", {}, ...Object.keys(DETAIL).map((k) => el("option", { value: k, selected: k === params.detail }, k)));
  const bin = el("select", {}, ...[1, 2, 3, 4].map((b) => el("option", { value: b }, `${b}×${b}`)));
  const compare = el("select", {}, el("option", { value: "off" }, "Off"), el("option", { value: "blink" }, "Blink"),
    el("option", { value: "side" }, "Side by side"));
  const labelB = el("label", { hidden: true }, "Compare with", pickerB);
  const zoomBtn = el("button", { type: "button" }, "Zoom and pan");
  const zoomRow = el("div", { className: "row" }, zoomBtn);

  const imgA = el("img", { className: "frame a", alt: "The frame" });
  const imgB = el("img", { className: "frame b", alt: "The frame to compare", hidden: true });
  const stage = el("div", { className: "stage" }, imgA, imgB);
  const badge = el("div", { className: "badges" });

  const tiles = el("table", { className: "tiles" });
  const zoomIn = el("button", { type: "button" }, "Zoom in +");
  const zoomOut = el("button", { type: "button" }, "− Zoom out");
  const back = el("button", { type: "button", className: "primary" }, "Back to picture");
  const zoomLabel = el("span", { className: "dim" });
  const zoomBox = el("div", { hidden: true },
    el("div", { className: "card" }, el("div", { className: "row" }, zoomOut, zoomIn, back), zoomLabel),
    el("div", { className: "tile-scroll" }, tiles));

  const histogram = el("div", { className: "chart-box" });
  const stats = el("div", { className: "readout" });
  const note = el("div", { className: "notice warn", hidden: true, role: "status" });
  const viewer = el("div", {}, note, stage, badge);

  const settings = [
    el("div", { className: "row" }, el("label", {}, el("span", {}, "Clipping ", clipValue), clip)),
    el("div", { className: "row" }, el("label", {}, "Detail", detail), el("label", {}, "Binning", bin)), messageNode];
  const root = el("div", { className: full ? "viewer" : "viewer compact" }, el("h3", {}, "Picture"), notice, viewer, zoomRow, zoomBox,
    full
      ? el("div", { className: "card" },
        el("div", { className: "row" }, el("label", {}, "Frame", picker), el("div", { className: "fixed" }, refresh)),
        ...settings, el("div", { className: "row" }, el("label", {}, "Compare", compare), labelB))
      : el("details", { className: "card" }, el("summary", {}, "Picture settings"), ...settings),
    ...(full ? [el("h3", {}, "Histogram"), el("div", { className: "card" }, histogram)] : []),
    el("h3", {}, "Stars"), el("div", { className: "card" }, stats));

  // -- addresses -----------------------------------------------------------------------------------------------
  const base = (route) => `/api/v1/pier/${encodeURIComponent(ctx.pier.id)}/${route}`;
  function query(ref, extra = {}) {
    const d = DETAIL[params.detail];
    return new URLSearchParams({ ref, stretch: params.stretch, bin: params.bin, size: d.size, quality: d.quality,
      v: ref === "current" ? version : "1", ...extra }).toString();
  }
  const previewUrl = (ref) => `${base("preview")}?${query(ref)}`;

  // -- the picture ---------------------------------------------------------------------------------------------
  // A picture that cannot be made comes back as the Master's plain reason, not an image: say it in place of the
  // browser's broken-image icon.
  async function explain(url) {
    let reason = "The picture could not be loaded.";
    try {
      const resp = await fetch(url, { credentials: "same-origin" });
      const body = await resp.json().catch(() => ({}));
      reason = body.reason || `The picture could not be made (${resp.status}).`;
    } catch { /* keep the generic reason */ }
    note.textContent = reason === "no such frame"
      ? "There is no current exposure yet. Take one, or choose a Library frame above." : reason;
    note.hidden = false;
  }
  for (const [img, url] of [[imgA, () => previewUrl(source)], [imgB, () => previewUrl(other)]]) {
    img.addEventListener("error", () => { if (shown) { img.classList.add("broken"); explain(url()); } });
    img.addEventListener("load", () => { img.classList.remove("broken"); if (img === imgA) note.hidden = true; say(""); });
  }

  function draw() {
    shown = true;
    imgA.src = previewUrl(source);
    const comparing = mode !== "off" && other;
    imgB.hidden = !comparing;
    if (comparing) imgB.src = previewUrl(other);
    stage.className = `stage ${comparing ? mode : ""}`;
    clearInterval(blinkTimer);
    blinkTimer = null;
    imgB.classList.remove("off");
    if (comparing && mode === "blink") blinkTimer = setInterval(() => imgB.classList.toggle("off"), BLINK_MS);
    loadMeta();
  }

  // Portrait and landscape frames are laid out differently (see app.css): a tall frame is capped in height and
  // centred, and two tall frames compared side by side sit next to each other while wide ones stack.
  function shape(w, h) {
    root.classList.toggle("portrait", h > w);
    root.classList.toggle("landscape", h <= w);
  }

  async function loadMeta() {
    const d = DETAIL[params.detail];
    const resp = await fetch(`${base("preview/meta")}?${query(source)}`, { credentials: "same-origin" });
    const out = await resp.json().catch(() => ({}));
    if (out.status !== "ok") {
      meta = null;
      root.classList.remove("portrait", "landscape");
      histogram.replaceChildren();
      stats.replaceChildren(el("div", { className: "dim" }, out.reason || "No frame yet."));
      badge.replaceChildren();
      return;
    }
    meta = out;
    shape(out.width, out.height);
    histogram.replaceChildren(histogramChart(out.histogram));
    const s = out.stats || {};
    stats.replaceChildren(...[["Stars", num(s.star_count, 0)], ["HFR", num(s.hfr, 2, " px")], ["FWHM", num(s.fwhm, 2, " px")],
      ["Frame", `${out.width} × ${out.height}`]].map(([n, v]) => el("div", {},
      el("div", { className: "name" }, n), el("div", { className: "value" }, v))));
    badge.replaceChildren(...(out.degraded ? [el("span", { className: "badge warn" }, "Reduced quality while a session runs")] : []),
      el("span", { className: "badge" }, `${d.size}px · JPEG ${d.quality}`));
  }

  // -- the list of frames --------------------------------------------------------------------------------------
  async function loadFrames() {
    const resp = await fetch(base("frames"), { credentials: "same-origin" });
    const out = await resp.json().catch(() => ({}));
    const frames = out.frames || [];
    notice.hidden = out.status === "ok";
    notice.textContent = out.reason ? `Pictures are not available: ${out.reason}` : "";
    const label = (f) => [f.object || f.name, f.type, f.filter, f.exposure && `${f.exposure}s`, f.date].filter(Boolean).join(" · ");
    for (const [select, withCurrent] of [[picker, true], [pickerB, false]]) {
      const keep = select.value;
      select.replaceChildren(...(withCurrent ? [el("option", { value: "current" }, "Current exposure")] : [el("option", { value: "" }, "Choose a frame")]),
        ...frames.filter((f) => f.local !== false).map((f) => el("option", { value: `library:${f.id}` }, label(f))));
      if ([...select.options].some((o) => o.value === keep)) select.value = keep;
    }
    // No current exposure yet (a capture from this computer's own Imaging screen is not announced): open on the
    // newest Library frame rather than an empty picture, until the person chooses something themselves.
    const firstLibrary = frames.find((f) => f.local !== false);
    if (!chosen && source === "current" && out.status === "ok" && !out.current?.available && firstLibrary) {
      source = `library:${firstLibrary.id}`;
      picker.value = source;
    }
    const latest = out.current?.version || 0;
    if (latest !== version) {
      version = latest;
      if (!chosen && source !== "current" && out.current?.available) { source = "current"; picker.value = "current"; }  // a new exposure
      if (source === "current" && shown) draw();
    }
    if (out.status === "ok" && !out.current?.available && source === "current" && !frames.length) {
      say("Nothing to show yet. Take an exposure, or choose a Library frame.");
    }
  }

  // -- pan and zoom by tiles -----------------------------------------------------------------------------------
  function drawTiles() {
    if (!meta) return;
    const b = params.bin;
    const w = Math.max(1, Math.floor(Math.floor(meta.width / b) / (1 << level)));
    const h = Math.max(1, Math.floor(Math.floor(meta.height / b) / (1 << level)));
    const cols = Math.ceil(w / TILE), rows = Math.ceil(h / TILE);
    tiles.replaceChildren(...Array.from({ length: rows }, (_, y) => el("tr", {}, ...Array.from({ length: cols }, (_, x) =>
      el("td", {}, el("img", { alt: "", loading: "lazy", width: Math.min(TILE, w - x * TILE), height: Math.min(TILE, h - y * TILE),
        src: `${base("tile")}?${query(source, { level, x, y })}` }))))));
    zoomLabel.textContent = `${w} × ${h} px shown · ${level === 0 ? "full resolution" : `1 in ${1 << level}`}`;
    zoomIn.disabled = level === 0;
    zoomOut.disabled = (1 << (level + 1)) > Math.max(meta.width, meta.height);
  }
  function openZoom() {
    if (!meta) return say("There is no picture to zoom into yet.", true);
    level = Math.max(0, Math.min(8, Math.ceil(Math.log2(Math.max(1, Math.floor(meta.width / params.bin) / 700)))));
    viewer.hidden = zoomRow.hidden = true;
    zoomBox.hidden = false;
    drawTiles();
  }
  function closeZoom() { zoomBox.hidden = true; viewer.hidden = zoomRow.hidden = false; }

  // -- controls ------------------------------------------------------------------------------------------------
  let wait = null;
  const later = (fn) => { clearTimeout(wait); wait = setTimeout(fn, 250); };
  picker.addEventListener("change", () => { source = picker.value; chosen = true; draw(); });
  pickerB.addEventListener("change", () => { other = pickerB.value; draw(); });
  refresh.addEventListener("click", async () => { await loadFrames(); draw(); });
  clip.addEventListener("input", () => { params.stretch = parseInt(clip.value, 10); clipValue.textContent = clip.value; later(draw); });
  detail.addEventListener("change", () => { params.detail = detail.value; draw(); });
  bin.addEventListener("change", () => { params.bin = parseInt(bin.value, 10); draw(); });
  compare.addEventListener("change", () => { mode = compare.value; labelB.hidden = mode === "off"; draw(); });
  zoomBtn.addEventListener("click", openZoom);
  back.addEventListener("click", closeZoom);
  zoomIn.addEventListener("click", () => { level = Math.max(0, level - 1); drawTiles(); });
  zoomOut.addEventListener("click", () => { level += 1; drawTiles(); });

  // a frame announced on the stream: look again, which refreshes the picture if the current exposure is on show
  ctx.on("event", (text) => { if (shown && /FrameCaptured/.test(text)) loadFrames(); });

  return {
    el: root,
    show() { loadFrames().then(draw); },
    hide() { clearInterval(blinkTimer); blinkTimer = null; shown = false; },
  };
}
