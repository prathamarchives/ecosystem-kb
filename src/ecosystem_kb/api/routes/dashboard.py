"""Dashboard routes (runbook Step 9): one origin, one static page.

FastAPI serves the whole owner UI. No second hostname, no UI container.
Security model (review P0-1/P0-2):
  - CSP with a per-response cryptographically random nonce on the single
    inline script; 'unsafe-inline' is never used; Cache-Control: no-store
    because the HTML carries a per-response nonce + owner/login state.
  - Values embedded into JavaScript are serialized with json.dumps,
    never Python repr.
  - Untrusted values (client names, facts, errors) are rendered with
    textContent/DOM construction only.
  - Owner login is the Supabase PKCE browser flow: Web Crypto verifier,
    localStorage persistence, /auth/v1/otp?redirect_to=..., callback code
    exchange at /auth/v1/token?grant_type=pkce, then the same-origin
    /auth/session exchange for the first-party kb_session cookie.
"""

from __future__ import annotations

import html
import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ecosystem-kb</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --text:#e6e9ef; --dim:#8b93a3; --accent:#4f8cff; --ok:#3fb950; --warn:#d29922; --bad:#f85149; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text); }
header { display:flex; gap:16px; align-items:center; padding:14px 22px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
header h1 { font-size:16px; margin:0; font-weight:600; }
header nav { display:flex; gap:4px; flex-wrap:wrap; }
header nav button { background:none; border:0; color:var(--dim); padding:7px 12px; border-radius:7px; cursor:pointer; font:inherit; }
header nav button.on { color:var(--text); background:var(--panel); }
#whoami { margin-left:auto; color:var(--dim); font-size:13px; }
main { max-width:1080px; margin:0 auto; padding:22px; }
section { display:none; }
section.on { display:block; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px 18px; margin-bottom:14px; }
.card h2 { margin:0 0 10px; font-size:14px; color:var(--dim); font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
#endpoint { font-size:17px; color:var(--accent); word-break:break-all; }
button.primary { background:var(--accent); color:#fff; border:0; padding:9px 16px; border-radius:8px; cursor:pointer; font:inherit; }
button.small { background:var(--panel); color:var(--text); border:1px solid var(--line); padding:5px 10px; border-radius:7px; cursor:pointer; font:inherit; font-size:13px; }
button.danger { color:var(--bad); }
input, textarea, select { background:#10131a; color:var(--text); border:1px solid var(--line); border-radius:7px; padding:8px 10px; font:inherit; width:100%; }
textarea { min-height:90px; resize:vertical; }
label { display:block; font-size:13px; color:var(--dim); margin:10px 0 4px; }
.row { display:flex; gap:10px; flex-wrap:wrap; }
.row > * { flex:1 1 200px; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th { text-align:left; color:var(--dim); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--line); }
td { padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; background:#232936; color:var(--dim); margin-right:4px; }
.tag.ok { color:var(--ok); } .tag.warn { color:var(--warn); } .tag.bad { color:var(--bad); }
#toast { position:fixed; bottom:18px; right:18px; background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:10px 16px; display:none; max-width:420px; }
.muted { color:var(--dim); }
pre.snippet { background:#10131a; border:1px solid var(--line); border-radius:8px; padding:12px; overflow-x:auto; font-size:13px; }
#login-card { max-width:420px; margin:12vh auto; }
.err { color:var(--bad); min-height:20px; font-size:14px; }
.hidden { display:none !important; }
</style>
</head>
<body>
<header>
  <h1>ecosystem-kb</h1>
  <nav id="tabs">
    <button data-tab="knowledge" class="on">Knowledge</button>
    <button data-tab="clients">Clients</button>
    <button data-tab="connect">Connect</button>
    <button data-tab="status">Status</button>
  </nav>
  <span id="whoami"></span>
  <button id="logout" class="small hidden">Log out</button>
</header>
<main>

<section id="knowledge" class="on">
  <div class="card">
    <h2>Search</h2>
    <div class="row">
      <input id="q" placeholder="Search the knowledge graph…" autocomplete="off">
      <button class="primary" id="search-btn" style="flex:0 0 auto">Search</button>
    </div>
    <div id="search-results"></div>
  </div>
  <div class="card">
    <h2>Capture</h2>
    <div class="row">
      <div><label>Kind</label>
        <select id="cap-kind">
          <option>finding</option><option>decision</option><option>correction</option>
          <option>note</option><option>claim</option><option>experiment</option><option>system</option>
        </select></div>
      <div><label>Status</label>
        <select id="cap-status"><option>proposed</option><option>accepted</option><option>superseded</option><option>rejected</option><option>disputed</option></select></div>
    </div>
    <label>Title</label><input id="cap-title" placeholder="Short title">
    <label>Content</label><textarea id="cap-content" placeholder="What did you learn?"></textarea>
    <div class="row">
      <div><label>Project</label><input id="cap-project"></div>
      <div><label>Repository</label><input id="cap-repo"></div>
    </div>
    <label>Source URL (optional)</label><input id="cap-uri" placeholder="https://…">
    <label>Typed extraction JSON (optional, advanced)</label>
    <textarea id="cap-extraction" class="mono" placeholder='{"entities":[…],"edges":[…]}'></textarea>
    <div class="err" id="cap-err"></div>
    <p style="margin-top:10px"><button class="primary" id="capture-btn">Capture</button>
    <span class="muted" id="cap-note"></span></p>
  </div>
</section>

<section id="clients">
  <div class="card"><h2>Registered clients</h2>
    <div id="clients-table"></div>
  </div>
</section>

<section id="connect">
  <div class="card">
    <h2>MCP endpoint</h2>
    <p id="endpoint" class="mono"></p>
    <p class="muted">Add this URL to an MCP-compatible client. The client will open
    the browser for sign-in and permission approval. <strong>No API key is required.</strong></p>
    <pre class="snippet" id="connect-json"></pre>
    <p class="muted">Tested clients: any client implementing MCP OAuth discovery
    (e.g. OpenCode, Claude Code, Cursor remote MCP). A client that only accepts a
    static bearer token is not OAuth-compatible and cannot use this flow.</p>
  </div>
</section>

<section id="status">
  <div class="card"><h2>Service status</h2><div id="status-body"><span class="muted">Loading…</span></div></div>
</section>

</main>
<div id="toast"></div>

<script nonce="__NONCE__">
"use strict";
// Every untrusted value is rendered with textContent/DOM construction.
// innerHTML is never used for data. CSP allows only this nonce-tagged
// script plus same-origin resources.

// ---- PKCE helpers (Web Crypto; no plain fallback — review P0-2) ----
function b64url(bytes) {
  let s = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
  return btoa(s).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
}

async function createPkce() {
  if (!window.crypto || !window.crypto.subtle || !window.crypto.getRandomValues) {
    throw new Error("Web Crypto is unavailable in this browser; cannot sign in securely.");
  }
  const raw = new Uint8Array(32);
  window.crypto.getRandomValues(raw);
  const verifier = b64url(raw); // 43 chars for 32 bytes
  const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const challenge = b64url(digest);
  return { verifier, challenge };
}

// ---- configuration injected by the server (json.dumps, never repr) ----
var KB_CONFIG = __KB_CONFIG__;

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children || []) if (child) node.appendChild(child);
  return node;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

async function api(path, opts) {
  const response = await fetch(path, Object.assign({ credentials: "same-origin" }, opts));
  if (response.status === 401 && !path.startsWith("/auth")) { showLogin(); throw new Error("login required"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || ("HTTP " + response.status));
  return data;
}

// ---- login: Supabase magic-link PKCE (review P0-2) ----
function showLogin(pendingRequestId) {
  const main = document.querySelector("main");
  main.textContent = "";
  const card = el("div", { class: "card", id: "login-card" });
  card.appendChild(el("h2", { text: "Owner login" }));
  card.appendChild(el("p", { class: "muted", text: "Enter the owner email; a sign-in link will be sent." }));
  const email = el("input", { type: "email", id: "login-email", placeholder: "owner@example.com" });
  const err = el("div", { class: "err" });
  const btn = el("button", { class: "primary", text: "Send sign-in link" });
  btn.addEventListener("click", async () => {
    err.textContent = "";
    try {
      const cfg = await api("/auth/config");
      const pkce = await createPkce();
      // Persist BEFORE sending: the email link may open in a new tab.
      window.localStorage.setItem("kb_pkce_verifier", pkce.verifier);
      if (pendingRequestId) window.localStorage.setItem("kb_pending_request", pendingRequestId);
      else window.localStorage.removeItem("kb_pending_request");

      const callback = new URL(cfg.callback_url);
      if (pendingRequestId) callback.searchParams.set("request_id", pendingRequestId);
      const otpUrl = new URL(cfg.supabase_url + "/auth/v1/otp");
      otpUrl.searchParams.set("redirect_to", callback.toString());

      const r = await fetch(otpUrl.toString(), {
        method: "POST",
        headers: { "Content-Type": "application/json", "apikey": cfg.supabase_anon_key },
        body: JSON.stringify({
          email: email.value,
          create_user: false,
          code_challenge: pkce.challenge,
          code_challenge_method: "s256",
        }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(detail.msg || detail.error_description || detail.message || ("Supabase returned HTTP " + r.status));
      }
      toast("Sign-in link sent — check your email.");
    } catch (e) { err.textContent = e.message; }
  });
  card.appendChild(email); card.appendChild(err); card.appendChild(el("p", {}, [btn]));
  main.appendChild(card);
}

function showApp() {
  document.getElementById("logout").classList.remove("hidden");
  document.getElementById("endpoint").textContent = KB_CONFIG.mcp_url;
  document.getElementById("connect-json").textContent =
    JSON.stringify({ mcpServers: { "ecosystem-kb": { url: KB_CONFIG.mcp_url } } }, null, 2);
  loadClients(); loadStatus();
}

// ---- tabs ----
for (const btn of document.querySelectorAll("#tabs button")) {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach(b => b.classList.remove("on"));
    document.querySelectorAll("main section").forEach(s => s.classList.remove("on"));
    btn.classList.add("on");
    document.getElementById(btn.dataset.tab).classList.add("on");
  });
}
document.getElementById("logout").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" }); location.reload();
});

// ---- knowledge: search ----
async function doSearch() {
  const out = document.getElementById("search-results");
  out.textContent = "";
  try {
    const data = await api("/v1/dashboard/search?query=" + encodeURIComponent(document.getElementById("q").value));
    if (!data.facts || !data.facts.length) { out.appendChild(el("p", { class: "muted", text: "No results." })); return; }
    for (const fact of data.facts) {
      const card = el("div", { class: "card" });
      card.appendChild(el("div", { text: fact.fact || "" }));
      const meta = el("div", { class: "muted", style: "font-size:13px;margin-top:6px" });
      const status = fact.epistemic_status || "unknown";
      meta.appendChild(el("span", { class: "tag " + (status === "accepted" ? "ok" : "warn"), text: status }));
      if (fact.source_reference) meta.appendChild(el("span", { class: "tag", text: fact.source_reference }));
      if (fact.valid_at) meta.appendChild(el("span", { class: "tag", text: "from " + fact.valid_at }));
      card.appendChild(meta);
      out.appendChild(card);
    }
  } catch (e) { out.appendChild(el("p", { class: "err", text: e.message })); }
}
document.getElementById("search-btn").addEventListener("click", doSearch);
document.getElementById("q").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });

// ---- knowledge: capture ----
document.getElementById("capture-btn").addEventListener("click", async () => {
  const errBox = document.getElementById("cap-err"), note = document.getElementById("cap-note");
  errBox.textContent = ""; note.textContent = "";
  const payload = {
    kind: document.getElementById("cap-kind").value,
    title: document.getElementById("cap-title").value,
    content: document.getElementById("cap-content").value,
    project: document.getElementById("cap-project").value || null,
    repository: document.getElementById("cap-repo").value || null,
    source_uri: document.getElementById("cap-uri").value || null,
    status: document.getElementById("cap-status").value,
  };
  const extraction = document.getElementById("cap-extraction").value.trim();
  if (extraction) {
    try { payload.extraction = JSON.parse(extraction); }
    catch (e) { errBox.textContent = "Extraction is not valid JSON: " + e.message; return; }
  }
  try {
    const receipt = await api("/v1/capture", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    note.textContent = receipt.epistemic_note || "captured";
    if (receipt.extraction_status) note.textContent += " — " + receipt.extraction_status;
    toast("Captured: " + (receipt.event_id || "ok"));
  } catch (e) { errBox.textContent = e.message; }
});

// ---- clients ----
async function loadClients() {
  const out = document.getElementById("clients-table");
  out.textContent = "";
  try {
    const data = await api("/v1/clients");
    if (!data.clients || !data.clients.length) { out.appendChild(el("p", { class: "muted", text: "No clients registered yet." })); return; }
    const table = el("table");
    const head = el("tr");
    for (const h of ["Client", "Mode", "Scopes", "Active tokens", "Consent", "Last used", ""]) head.appendChild(el("th", { text: h }));
    table.appendChild(head);
    for (const c of data.clients) {
      const row = el("tr");
      const nameCell = el("td");
      nameCell.appendChild(el("div", { text: c.client_name || c.client_id }));
      if (c.client_id) nameCell.appendChild(el("div", { class: "muted mono", style: "font-size:12px", text: c.client_id }));
      row.appendChild(nameCell);
      row.appendChild(el("td", { text: c.registration_mode || "-" }));
      row.appendChild(el("td", { text: (c.scopes || []).join(", ") || "-" }));
      const tokens = el("td");
      tokens.appendChild(el("span", { class: "tag", text: (c.active_access_tokens || 0) + " access" }));
      tokens.appendChild(el("span", { class: "tag", text: (c.active_refresh_tokens || 0) + " refresh" }));
      row.appendChild(tokens);
      row.appendChild(el("td", {}, [el("span", { class: "tag " + (c.consent_granted ? "ok" : ""), text: c.consent_granted ? "granted" : "none" })]));
      row.appendChild(el("td", { text: c.last_used_at ? c.last_used_at.slice(0, 19).replace("T", " ") : "never" }));
      const actions = el("td");
      const revoke = el("button", { class: "small danger", text: "Revoke" });
      revoke.addEventListener("click", async () => {
        if (!confirm("Revoke this client? All its tokens stop working immediately.")) return;
        try { await api("/v1/clients/" + encodeURIComponent(c.client_id) + "/revoke", { method: "POST" }); toast("Client revoked."); loadClients(); }
        catch (e) { toast(e.message); }
      });
      actions.appendChild(revoke);
      row.appendChild(actions);
      table.appendChild(row);
    }
    out.appendChild(table);
  } catch (e) { out.appendChild(el("p", { class: "err", text: e.message })); }
}

// ---- status ----
async function loadStatus() {
  const out = document.getElementById("status-body");
  out.textContent = "";
  try {
    const data = await api("/v1/status");
    const table = el("table");
    for (const [name, value] of Object.entries(data.checks || {})) {
      const row = el("tr");
      row.appendChild(el("td", { text: name }));
      const val = el("td", {}, [el("span", { class: "tag " + (String(value).startsWith("ok") ? "ok" : "warn"), text: String(value) })]);
      row.appendChild(val);
      table.appendChild(row);
    }
    if (data.version) table.appendChild(el("tr", {}, [el("td", { text: "version" }), el("td", { text: data.version })]));
    out.appendChild(table);
  } catch (e) { out.appendChild(el("p", { class: "err", text: e.message })); }
}

// ---- boot ----
(async function boot() {
  try {
    const me = await api("/auth/me");
    if (!me.authenticated) { showLogin(KB_CONFIG.pending_request); return; }
    document.getElementById("whoami").textContent = "owner";
    showApp();
  } catch (e) { showLogin(KB_CONFIG.pending_request); }
})();
</script>
</body>
</html>"""


def _page(config: dict) -> HTMLResponse:
    """Render the dashboard shell.

    config: JSON-serializable values the client script needs (mcp_url,
    pending_request). Serialized with json.dumps — never repr. A fresh
    cryptographically random nonce is generated per response and applied
    to both the script element and the CSP header.
    """
    nonce = secrets.token_urlsafe(18)
    page = _PAGE.replace("__NONCE__", nonce)
    page = page.replace("__KB_CONFIG__", json.dumps(config))
    response = HTMLResponse(page)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self' https://*.supabase.co; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store"  # per-response nonce + login state
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/", include_in_schema=False)
async def dashboard(request: Request):
    settings = request.app.state.settings
    return _page({"mcp_url": settings.canonical_mcp_resource or "", "pending_request": None})


def login_page(request: Request, next_request_id: str | None = None) -> HTMLResponse:
    """Login view; carries the pending OAuth request id (if any) for the
    PKCE callback to resume."""
    return _page({"mcp_url": "", "pending_request": next_request_id})


_CALLBACK_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Completing sign-in…</title>
<style>
body { margin:0; font:15px/1.5 system-ui,sans-serif; background:#0f1115; color:#e6e9ef;
  display:flex; align-items:center; justify-content:center; min-height:100vh; }
.card { background:#171a21; border:1px solid #2a2f3a; border-radius:12px; padding:28px; max-width:460px; width:92%; }
h1 { font-size:18px; margin:0 0 8px; }
.muted { color:#8b93a3; }
.err { color:#f85149; margin-top:10px; min-height:20px; }
a { color:#4f8cff; }
</style>
</head>
<body>
<div class="card">
<h1>Completing sign-in…</h1>
<p id="status" class="muted">Exchanging the sign-in code.</p>
<p class="err" id="err"></p>
<p><a href="/" id="retry" style="display:none">Start a fresh sign-in</a></p>
</div>
<script nonce="__NONCE__">
"use strict";
// Supabase magic-link PKCE completion (review P0-2):
//   1. read code + request_id from the query
//   2. verify request_id matches the localStorage value (anti-substitution)
//   3. exchange the code at /auth/v1/token?grant_type=pkce
//   4. POST the access token to same-origin /auth/session (HttpOnly cookie)
//   5. clear storage, replace history, resume the pending OAuth request

function fail(msg) {
  document.getElementById("status").textContent = "Sign-in failed.";
  const err = document.getElementById("err");
  err.textContent = msg || "Unknown error.";
  document.getElementById("retry").style.display = "inline";
}

async function run() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const request_id = params.get("request_id");
  const storedVerifier = window.localStorage.getItem("kb_pkce_verifier");
  const storedRequest = window.localStorage.getItem("kb_pending_request");

  if (!code) { fail("No sign-in code in the URL."); return; }
  // The request_id must match what was stored when the link was requested:
  // never resume a different pending authorization.
  if (request_id && storedRequest && request_id !== storedRequest) {
    fail("Sign-in session mismatch — start again from the application.");
    return;
  }
  const verifier = storedVerifier;
  window.localStorage.removeItem("kb_pkce_verifier");
  // Remove the one-time code from history immediately.
  window.history.replaceState({}, "", "/auth/callback");

  if (!verifier) { fail("Sign-in verifier missing — request a new link."); return; }

  try {
    const cfg = await fetch("/auth/config", { credentials: "same-origin" }).then(r => r.json());
    const tokenUrl = cfg.supabase_url + "/auth/v1/token?grant_type=pkce";
    const r = await fetch(tokenUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "apikey": cfg.supabase_anon_key },
      body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}));
      throw new Error(detail.msg || detail.error_description || detail.message || ("token exchange HTTP " + r.status));
    }
    const session = await r.json();
    if (!session.access_token) throw new Error("Supabase returned no access token.");

    const me = await fetch("/auth/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: session.access_token }),
    });
    if (!me.ok) {
      const detail = await me.json().catch(() => ({}));
      throw new Error(detail.message || ("session exchange HTTP " + me.status));
    }

    const pending = request_id || null;
    window.localStorage.removeItem("kb_pending_request");
    document.getElementById("status").textContent = "Signed in. Continuing…";
    window.location.replace(pending ? "/oauth/authorize/resume?request_id=" + encodeURIComponent(pending) : "/");
  } catch (e) {
    fail(e.message);
  }
}

run();
</script>
</body>
</html>"""


def callback_page(request: Request) -> HTMLResponse:
    """Executable callback HTML: performs the PKCE code exchange, the
    same-origin session exchange, then resumes the pending authorization."""
    nonce = secrets.token_urlsafe(18)
    page = _CALLBACK_PAGE.replace("__NONCE__", nonce)
    response = HTMLResponse(page)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "connect-src 'self' https://*.supabase.co; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def consent_page(
    request: Request, *, client_name: str, client_id: str, scopes: list[str], request_id: str
) -> HTMLResponse:
    """The OAuth consent screen (runbook §5.6): actual client name, the
    requested access, Approve / Deny. Form posts to /oauth/authorize/decide."""
    safe_scopes = [s for s in scopes if s in ("kb:read", "kb:write", "offline_access")]
    scope_lines = "".join(
        f"<li>{html.escape(s)} — "
        + (
            "read and search knowledge"
            if s == "kb:read"
            else "capture new knowledge"
            if s == "kb:write"
            else "stay connected without repeated sign-ins"
        )
        + "</li>"
        for s in safe_scopes
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize client</title>
<style>
body {{ margin:0; font:15px/1.5 system-ui,sans-serif; background:#0f1115; color:#e6e9ef;
  display:flex; align-items:center; justify-content:center; min-height:100vh; }}
.card {{ background:#171a21; border:1px solid #2a2f3a; border-radius:12px; padding:28px;
  max-width:460px; width:92%; }}
h1 {{ font-size:18px; margin:0 0 6px; }}
.muted {{ color:#8b93a3; font-size:14px; }}
.client {{ font-size:20px; font-weight:600; color:#4f8cff; margin:10px 0 2px; word-break:break-all; }}
ul {{ padding-left:20px; }} li {{ margin:4px 0; }}
form {{ display:flex; gap:10px; margin-top:20px; }}
button {{ flex:1; padding:11px; border-radius:8px; border:0; cursor:pointer; font:inherit; font-size:15px; }}
.approve {{ background:#4f8cff; color:#fff; }}
.deny {{ background:#232936; color:#e6e9ef; }}
</style></head><body>
<div class="card">
<h1>Authorize this client?</h1>
<p class="muted">The following application wants to connect to your ecosystem-kb.</p>
<div class="client">{html.escape(client_name)}</div>
<p class="muted mono" style="font-size:12px">{html.escape(client_id)}</p>
<p class="muted">It requests:</p>
<ul>{scope_lines}</ul>
<form method="post" action="/oauth/authorize/decide">
<input type="hidden" name="request_id" value="{html.escape(request_id)}">
<button class="approve" type="submit" name="decision" value="approve">Approve</button>
<button class="deny" type="submit" name="decision" value="deny">Deny</button>
</form>
</div></body></html>"""
    response = HTMLResponse(page)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'unsafe-inline'; script-src 'none'; frame-ancestors 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@router.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request):
    """The Supabase PKCE callback lands here. The page itself performs the
    code exchange + session exchange (client-side, with the anon key only)
    and then resumes any pending OAuth authorization."""
    return callback_page(request)
