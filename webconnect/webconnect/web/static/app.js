// Pairing, sign-in and the Pier list; opening a Pier hands over to pier.js, which builds its screen from the catalogue.
import { openPier } from "./pier.js";
const $ = (id) => document.getElementById(id);
let csrf = "";
let current = null;

// Theme: dark and light as on the desktop, and night (red only) for the dark-adapted eye. A browser-side preference.
const THEMES = ["dark", "light", "night"];
function applyTheme(name) {
  document.documentElement.dataset.theme = name;
  localStorage.setItem("wc-theme", name);
  $("theme-btn").title = `Theme: ${name} (tap to change)`;
}
applyTheme(THEMES.includes(localStorage.getItem("wc-theme")) ? localStorage.getItem("wc-theme") : "dark");
$("theme-btn").addEventListener("click", () => applyTheme(THEMES[(THEMES.indexOf(document.documentElement.dataset.theme) + 1) % THEMES.length]));

function open(pier) {
  current?.close();
  current = openPier(api, pier, $("pier-view"), $("tabs"));
  $("piers").hidden = true;
  localStorage.setItem("wc-pier", pier.id);
}

$("pier-name").addEventListener("click", () => {          // back to the Pier list
  current?.close();
  current = null;
  $("pier-view").replaceChildren();
  $("pier-name").textContent = "";
  localStorage.removeItem("wc-pier");
  $("piers").hidden = false;
});

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const resp = await fetch(path, { credentials: "same-origin", ...options, headers });
  return { http: resp.status, body: await resp.json().catch(() => ({})) };
}

async function showPiers() {
  const { http, body } = await api("/api/v1/piers");
  const list = $("pier-list");
  list.replaceChildren();
  if (!(body.piers || []).length) {
    const li = document.createElement("li");
    li.textContent = body.reason || (http === 200 ? "Galileo has no Piers to show yet." : `Could not load the Piers (${http}).`);
    list.append(li);
  }
  for (const pier of body.piers || []) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = pier.name || pier.id;
    button.addEventListener("click", () => open(pier));
    li.append(button);
    list.append(li);
  }
  $("pairing").hidden = true;
  const list_ = body.piers || [];
  const remembered = list_.find((p) => p.id === localStorage.getItem("wc-pier"));
  const only = list_.length === 1 ? list_[0] : remembered;     // one Pier, or the last one used: go straight in
  if (only) open(only);
  else $("piers").hidden = false;
}

async function signIn() {
  const { http, body } = await api("/signin", { method: "POST" });
  if (http !== 200) return false;
  csrf = body.csrf;
  $("who").textContent = `${body.name} (${body.role})`;
  await showPiers();
  return true;
}

async function pollPairing() {
  const { body } = await api("/pair/status");
  if (body.status === "approved") { await signIn(); return; }
  if (body.status === "pending") setTimeout(pollPairing, 2000);
  else $("pair-code").textContent = "The request was not approved. Try again.";
}

$("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const { body } = await api("/pair/request", { method: "POST", body: JSON.stringify({ name: $("device-name").value }) });
  $("pair-code").textContent = body.code ? `Code: ${body.code}` : (body.reason || "Could not ask.");
  if (body.code) pollPairing();
});

// A reload keeps the session cookie but loses the CSRF token; ask for it back before falling back to a sign-in.
async function resume() {
  const { http, body } = await api("/api/v1/me");
  if (http !== 200 || !body.csrf) return false;
  csrf = body.csrf;
  $("who").textContent = `${body.name} (${body.role})`;
  await showPiers();
  return true;
}

if (!(await resume()) && !(await signIn())) $("pairing").hidden = false;
