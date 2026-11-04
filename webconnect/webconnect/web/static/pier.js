// The Pier screen: live state, the control lease, and every operation the Control Surface catalogue offers.
// Nothing here knows about an area (guiding, solving, sky...): the controls are built from the catalogue, so a new
// operation appears without a change to this file. The Master decides what is allowed; this only presents it.

const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...kids);
  return node;
};

export function openPier(api, pier, root) {
  const state = new Map();
  const ui = {
    lease: el("p", { className: "lease" }),
    state: el("table", { className: "state" }),
    ops: el("div", { className: "ops" }),
    log: el("ol", { className: "log", reversed: true }),
  };
  let stream = null;
  let closed = false;

  root.replaceChildren(
    el("h2", {}, pier.name || pier.id),
    ui.lease,
    el("h3", {}, "State"), ui.state,
    el("h3", {}, "Operations"), ui.ops,
    el("h3", {}, "Events"), ui.log);

  const path = (rest) => `/api/v1/pier/${encodeURIComponent(pier.id)}/${rest}`;

  // -- state -------------------------------------------------------------------------------------
  function drawState() {
    const rows = [...state.entries()].sort(([a], [b]) => a.localeCompare(b));
    ui.state.replaceChildren(...(rows.length ? rows : [["", "nothing reported yet"]]).map(
      ([key, value]) => el("tr", {}, el("th", {}, key), el("td", {}, typeof value === "string" ? value : JSON.stringify(value)))));
  }

  async function loadState() {
    const { body } = await api(path("state"));
    state.clear();
    for (const [key, value] of Object.entries(body.values || {})) state.set(key, value);
    drawState();
  }

  // -- lease -------------------------------------------------------------------------------------
  function drawLease(message) {
    ui.lease.replaceChildren(el("span", {}, message || "Control: not held by you."));
    for (const [label, action] of [["Take control", "acquire"], ["Release", "release"]]) {
      ui.lease.append(" ", el("button", { type: "button", onclick: async () => {
        const { body } = await api(path(`lease/${action}`), { method: "POST", body: "{}" });
        drawLease(body.status === "ok" ? (action === "release" ? "Control released." : "You have control.")
                                       : (body.reason || "Could not take control."));
        await loadOps();
      } }, label));
    }
  }

  // -- operations --------------------------------------------------------------------------------
  function field(arg) {
    const common = { name: arg.name, required: arg.required && arg.default === undefined };
    let input;
    if (arg.type === "bool") input = el("input", { ...common, type: "checkbox", checked: arg.default === true });
    else if (arg.type === "enum") {
      input = el("select", common, ...(arg.choices || []).map((c) => el("option", { value: c, selected: c === arg.default }, c)));
    } else if (arg.type === "int" || arg.type === "float") {
      input = el("input", { ...common, type: "number", step: arg.type === "int" ? "1" : "any", min: arg.min ?? "", max: arg.max ?? "",
                            value: arg.default ?? "" });
    } else input = el("input", { ...common, type: "text", value: arg.default ?? "" });
    const unit = arg.unit ? ` (${arg.unit})` : "";
    return el("label", { title: arg.description || "" }, `${arg.name}${unit} `, input);
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

  function opCard(op) {
    const out = el("pre", { className: "result", hidden: true });
    const form = el("form", {}, ...op.args.map(field));
    const run = el("button", { type: "submit", disabled: !op.available, title: op.reason || op.description }, op.name.split(".")[1]);
    form.append(run);
    if (!op.available) form.append(el("small", {}, ` ${op.reason}`));
    let confirm = "";
    let key = crypto.randomUUID();
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const headers = { "Idempotency-Key": key };
      if (confirm) headers["X-Confirm-Token"] = confirm;
      const { body } = await api(path(`ops/${op.name}`), { method: "POST", headers, body: JSON.stringify({ args: collect(form, op) }) });
      out.hidden = false;
      if (body.confirm_token) {                     // a dangerous operation asks again before it runs
        confirm = body.confirm_token;
        run.textContent = "Confirm";
        out.textContent = `${body.reason}. Press again to confirm.`;
        return;
      }
      confirm = ""; key = crypto.randomUUID(); run.textContent = op.name.split(".")[1];
      out.textContent = body.status === "ok" ? (body.value === undefined || body.value === null ? "Done." : JSON.stringify(body.value, null, 1))
                                             : `${body.status}: ${body.reason || ""}`;
    });
    return el("details", { className: "op" }, el("summary", {}, op.name.split(".")[1], el("small", {}, ` ${op.description}`)), form, out);
  }

  async function loadOps() {
    const { body } = await api(path("catalogue"));
    const areas = new Map();
    for (const op of body.catalogue?.operations || []) {
      const area = op.name.split(".")[0];
      if (!areas.has(area)) areas.set(area, []);
      areas.get(area).push(op);
    }
    ui.ops.replaceChildren(...[...areas].map(([area, ops]) =>
      el("details", {}, el("summary", {}, area), ...ops.map(opCard))));
  }

  // -- the live stream ---------------------------------------------------------------------------
  function note(text) {
    ui.log.prepend(el("li", {}, text));
    while (ui.log.children.length > 50) ui.log.lastChild.remove();
  }

  function listen() {
    stream = new EventSource("/api/v1/events");
    const mine = (e) => { const d = JSON.parse(e.data); return d.pier === undefined || d.pier === pier.id ? d : null; };
    stream.addEventListener("state", (e) => {
      const d = mine(e);
      if (d && d.key) { state.set(d.key, d.value); drawState(); }
    });
    stream.addEventListener("lease", (e) => { const d = mine(e); if (d) { note(`lease: ${d.holder || "free"} (${d.reason || ""})`); loadOps(); } });
    stream.addEventListener("event", (e) => { const d = mine(e); if (d) note(`${d.name} ${d.source || ""}`); });
    stream.addEventListener("resync", () => { note("missed some updates; refreshing"); loadState(); });
    stream.addEventListener("hello", () => { loadState(); document.body.classList.remove("offline"); });
    for (const name of ["down", "signed-out"]) stream.addEventListener(name, (e) => {
      document.body.classList.add("offline");        // the picture on screen is stale until the stream is back
      note(`${name}: ${JSON.parse(e.data).reason || ""}`);
      if (name === "signed-out") close();
    });
    stream.onerror = () => document.body.classList.add("offline");
  }

  function close() {
    closed = true;
    stream?.close();
  }

  drawLease();
  drawState();
  loadOps();
  listen();
  return { close, get closed() { return closed; } };
}
