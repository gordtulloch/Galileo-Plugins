// Pairing, sign-in and the Pier list; opening a Pier hands over to pier.js, which builds its screen from the catalogue.
import { openPier } from "./pier.js";
const $ = (id) => document.getElementById(id);
let csrf = "";
let current = null;

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const resp = await fetch(path, { credentials: "same-origin", ...options, headers });
  return { http: resp.status, body: await resp.json().catch(() => ({})) };
}

async function showPiers() {
  const { body } = await api("/api/v1/piers");
  const list = $("pier-list");
  list.replaceChildren();
  for (const pier of body.piers || []) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = pier.name || pier.id;
    button.addEventListener("click", () => {
      current?.close();
      current = openPier(api, pier, $("pier-view"));
    });
    li.append(button);
    list.append(li);
  }
  $("piers").hidden = false;
  $("pairing").hidden = true;
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

if (!(await signIn())) $("pairing").hidden = false;
