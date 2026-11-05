// Two pages that know nothing about any one device: the raw state and event log, and every operation the Control
// Surface catalogue offers, with forms built from each operation's argument schema. A new operation appears here with
// no change to this file, which is also how an area without its own page yet (camera, guiding, solve...) is reachable.

import { el } from "./dom.js";

export function statusPage(ctx) {
  const table = el("table", { className: "state" });
  const log = el("ol", { className: "log", reversed: true });
  const root = el("div", {}, el("h2", {}, "Status"), el("div", { className: "card" }, table),
    el("h3", {}, "Events"), el("div", { className: "card" }, log));

  function draw() {
    const rows = [...ctx.state.entries()].sort(([a], [b]) => a.localeCompare(b));
    table.replaceChildren(...(rows.length ? rows : [["", "Nothing reported yet."]]).map(
      ([key, value]) => el("tr", {}, el("th", {}, key), el("td", {}, typeof value === "string" ? value : JSON.stringify(value)))));
  }
  ctx.on("state", draw);
  ctx.on("event", (text) => {
    log.prepend(el("li", {}, text));
    while (log.children.length > 50) log.lastChild.remove();
  });
  draw();
  return { el: root };
}

function field(arg) {
  const common = { name: arg.name, required: arg.required && arg.default === undefined };
  let input;
  if (arg.type === "bool") {
    input = el("input", { ...common, type: "checkbox", checked: arg.default === true });
    return el("label", { className: "check", title: arg.description || "" }, input, arg.name);
  }
  if (arg.type === "enum") {
    input = el("select", common, ...(arg.choices || []).map((c) => el("option", { value: c, selected: c === arg.default }, c)));
  } else if (arg.type === "int" || arg.type === "float") {
    input = el("input", { ...common, type: "number", inputMode: "decimal", step: arg.type === "int" ? "1" : "any",
                          min: arg.min ?? "", max: arg.max ?? "", value: arg.default ?? "" });
  } else input = el("input", { ...common, type: "text", value: arg.default ?? "" });
  const unit = arg.unit ? ` (${arg.unit})` : "";
  return el("label", { title: arg.description || "" }, `${arg.name}${unit}`, input);
}

function collect(form, op) {
  const args = {};
  for (const arg of op.args) {
    const input = form.elements[arg.name];
    if (arg.type === "bool") args[arg.name] = input.checked;
    else if (input.value === "") continue;
    else args[arg.name] = arg.type === "int" ? parseInt(input.value, 10) : arg.type === "float" ? parseFloat(input.value) : input.value;
  }
  return args;
}

function opCard(ctx, op) {
  const out = el("pre", { className: "result", hidden: true });
  const short = op.name.split(".")[1];
  const run = el("button", { type: "submit", className: "primary", disabled: !op.available, title: op.reason || op.description }, short);
  const form = el("form", {}, ...op.args.map(field), run);
  if (!op.available) form.append(el("small", {}, op.reason));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    run.disabled = true;
    const body = await ctx.run(op.name, collect(form, op));
    run.disabled = !op.available;
    out.hidden = false;
    out.textContent = body.status === "ok" ? (body.value == null ? "Done." : JSON.stringify(body.value, null, 1))
                                           : `${body.status}: ${body.reason || ""}`;
  });
  return el("details", { className: "op" }, el("summary", {}, short, el("small", {}, ` ${op.description || ""}`)), form, out);
}

export function operationsPage(ctx) {
  const list = el("div", { className: "ops" });
  const root = el("div", {}, el("h2", {}, "All operations"), list);
  function draw() {
    const areas = new Map();
    for (const op of ctx.catalogue.values()) {
      const area = op.name.split(".")[0];
      if (!areas.has(area)) areas.set(area, []);
      areas.get(area).push(op);
    }
    list.replaceChildren(...[...areas].map(([area, ops]) =>
      el("details", {}, el("summary", {}, area.charAt(0).toUpperCase() + area.slice(1)), ...ops.map((op) => opCard(ctx, op)))));
  }
  ctx.on("catalogue", draw);
  draw();
  return { el: root };
}
