// The History page: recorded sessions and, for one, how focus (HFR), star count and guiding error held up over its
// frames. Built on history.* operations (read-only, no lease needed).

import { el } from "./dom.js";
import { legend, lineChart, num, readout } from "./common.js";

const MAX_ROWS = 60;

export function historyPage(ctx) {
  const notice = el("div", { className: "notice warn", hidden: true });
  const list = el("div", { className: "card" });
  const title = el("h3", {}, "Session");
  const avgHfr = readout("Average HFR"), avgStars = readout("Average stars"), frameCount = readout("Frames");
  const hfrChart = el("div", { className: "chart-box" }), starChart = el("div", { className: "chart-box" });
  const guideChart = el("div", { className: "chart-box" });
  const table = el("table", { className: "state frames" });
  const detail = el("div", { hidden: true },
    title,
    el("div", { className: "card" }, el("div", { className: "readout" }, frameCount.box, avgHfr.box, avgStars.box)),
    el("h3", {}, "HFR"), el("div", { className: "card" }, hfrChart),
    el("h3", {}, "Stars detected"), el("div", { className: "card" }, starChart),
    el("h3", {}, "Guiding error (RMS)"), el("div", { className: "card" }, guideChart),
    el("h3", {}, "Frames"), el("div", { className: "card" }, table));
  const root = el("div", {}, el("h2", {}, "History"), notice, list, detail);

  const time = (stamp) => (stamp ? String(stamp).replace("T", " ").slice(0, 19) : "");

  async function open(name) {
    title.textContent = name;
    detail.hidden = false;
    for (const box of [hfrChart, starChart, guideChart]) box.replaceChildren(el("div", { className: "dim" }, "Loading…"));
    const out = await ctx.run("history.report", { session: name });
    if (out.status !== "ok") { hfrChart.replaceChildren(el("div", { className: "message error" }, out.reason || out.status)); return; }
    const frames = out.value?.frames || [];
    frameCount.set(String(out.value?.total ?? frames.length));
    avgHfr.set(num(out.value?.avg_hfr, 2));
    avgStars.set(num(out.value?.avg_star_count, 0));
    const hfr = [{ label: "HFR", tone: "a", points: frames.map((f) => f.hfr) }];
    const stars = [{ label: "Stars", tone: "b", points: frames.map((f) => f.star_count) }];
    const guide = [{ label: "RA", tone: "a", points: frames.map((f) => f.guide_rms_ra) }, { label: "Dec", tone: "b", points: frames.map((f) => f.guide_rms_dec) }];
    hfrChart.replaceChildren(lineChart(hfr, { zero: false }), legend(hfr));
    starChart.replaceChildren(lineChart(stars, { zero: false }), legend(stars));
    guideChart.replaceChildren(lineChart(guide), legend(guide));
    const shown = frames.slice(-MAX_ROWS).reverse();
    table.replaceChildren(el("tr", {}, ...["Time", "HFR", "Stars", "RA", "Dec"].map((h) => el("th", {}, h))),
      ...shown.map((f) => el("tr", {}, el("td", {}, time(f.timestamp)), el("td", {}, num(f.hfr, 2)), el("td", {}, String(f.star_count)),
        el("td", {}, num(f.guide_rms_ra, 2)), el("td", {}, num(f.guide_rms_dec, 2)))));
    detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function load() {
    const known = ctx.catalogue.has("history.sessions");
    notice.hidden = !known || ctx.can("history.sessions");
    notice.textContent = `History is not available: ${ctx.why("history.sessions")}.`;
    if (!ctx.can("history.sessions")) return;
    const out = await ctx.run("history.sessions");
    const sessions = out.status === "ok" ? out.value?.sessions || [] : [];
    list.replaceChildren(...(sessions.length ? sessions.slice().reverse().map((s) =>
      el("button", { type: "button", className: "session-row", onclick: () => open(s.name) },
        el("span", {}, s.name), el("small", { className: "dim" }, `${s.frame_count} frame(s)`))) :
      [el("div", { className: "dim" }, "No sessions recorded yet.")]));
  }

  ctx.on("catalogue", load);
  return { el: root, show() { load(); } };
}
