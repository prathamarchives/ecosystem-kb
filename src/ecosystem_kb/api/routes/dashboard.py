"""Dashboard routes (runbook Step 9): one origin, one static page.

FastAPI serves the whole owner UI. No second hostname, no UI container.
Untrusted values (client names, facts, errors) are rendered with
textContent only — never interpolated into HTML strings.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ecosystem_kb.core.exceptions import AuthenticationError

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

<script>
"use strict";
// Every untrusted value is rendered with textContent/DOM construction.
// innerHTML is never used for data. CSP is set by the server.

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

// ---- login ----
function showLogin() {
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
      await fetch(cfg.supabase_url + "/auth/v1/otp", {
        method: "POST",
        headers: { "Content-Type": "application/json", "apikey": cfg.supabase_anon_key },
        body: JSON.stringify({ email: email.value, create_user: false,
          email_redirect_to: cfg.callback_url }),
      }).then(async r => { if (!r.ok) throw new Error("Supabase rejected the request"); });
      err.textContent = "";
      toast("Sign-in link sent — check your email.");
    } catch (e) { err.textContent = e.message; }
  });
  card.appendChild(email); card.appendChild(err); card.appendChild(el("p", {}, [btn]));
  main.appendChild(card);
}

function showApp() {
  document.getElementById("logout").classList.remove("hidden");
  document.getElementById("endpoint").textContent = window.KB_MCP_URL;
  document.getElementById("connect-json").textContent =
    JSON.stringify({ mcpServers: { "ecosystem-kb": { url: window.KB_MCP_URL } } }, null, 2);
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
      meta.appendChild(el("span", { class: "tag " + (status === "accepted" ? "ok" : status === "superseded" ? "warn" : "warn"), text: status }));
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
    if (!me.authenticated) { showLogin(); return; }
    document.getElementById("whoami").textContent = "owner";
    showApp();
  } catch (e) { showLogin(); }
})();
</script>
</body>
</html>"""


def _page(extra_js: str = "", body_class: str = "") -> HTMLResponse:
    script = f"window.KB_MCP_URL = {extra_js};" if extra_js else ""
    page = _PAGE.replace("<script>\n\"use strict\";", f"<script>\n\"use strict\";\n{script}")
    response = HTMLResponse(page)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self' https://*.supabase.co; img-src 'self' data:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _require_owner(request: Request):
    """Owner session or raise. Used by every dashboard data endpoint."""
    token = request.cookies.get("kb_session")
    if not token:
        raise AuthenticationError("owner login required")
    row = await request.app.state.oauth_sessions.resolve(token)
    if row is None:
        raise AuthenticationError("owner login required")
    return row


@router.get("/", include_in_schema=False)
async def dashboard(request: Request):
    settings = request.app.state.settings
    resource = settings.canonical_mcp_resource or ""
    return _page(extra_js=f'"{resource}"')


def login_page(request: Request, next_request_id: str | None = None) -> HTMLResponse:
    """Login view with a pending OAuth authorization to resume after login."""
    resume = (
        f"window.KB_PENDING_AUTH = {next_request_id!r};"
        if next_request_id
        else "window.KB_PENDING_AUTH = null;"
    )
    response = _page(extra_js=resume)
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
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; script-src 'none'; frame-ancestors 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@router.get("/auth/callback", include_in_schema=False)
async def auth_callback():
    """The Supabase PKCE callback lands here; the SPA script on / handles
    the code exchange client-side, so this is a redirect to / with the
    query preserved."""
    return HTMLResponse(
        '<!doctype html><meta http-equiv="refresh" content="0;url=/">'
        "<p>Completing sign-in…</p>"
    )
