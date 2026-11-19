"""CLI: stack lifecycle, client management, connection wizard, backup/restore.

Architecture (ADR-019 — onboarding repair): the host CLI NEVER connects to
PostgreSQL or FalkorDB directly. The local Compose deployment keeps both on
a private Docker network with no published ports; the only host-reachable
surface is the API on 127.0.0.1:<KB_BIND_PORT> (loopback, not LAN).
Privileged client-management operations go through /v1/admin/* with the
KB_ADMIN_TOKEN credential (kb:admin scope, audited). Compose-control
commands (up/down/logs/backup/restore) shell out to `docker compose`,
which talks to the Docker socket, not the databases.

Secrets live in the untracked .env; nothing here ever prints them after
generation (rotate explicitly to see a new one).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets as pysecrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from ecosystem_kb.adapters.graphiti.client import build_graphiti
from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
from ecosystem_kb.core.config import get_settings

# Deployment state belongs to the DIRECTORY THE OWNER RUNS `kb` FROM (the
# deployment checkout), never to the installed package's source tree.
# Repair finding: deriving these from __file__ silently wrote .env into
# site-packages on non-editable installs — or into the source tree when
# running from elsewhere — so neither the stack nor the CLI saw the same
# configuration.
DEPLOY_ROOT = Path.cwd()
COMPOSE_LOCAL = DEPLOY_ROOT / "deploy" / "compose.local.yaml"
ENV_PATH = DEPLOY_ROOT / ".env"
ENV_EXAMPLE = DEPLOY_ROOT / ".env.example"
BACKUP_ROOT = DEPLOY_ROOT / "backups"
REPO_ROOT = Path(__file__).resolve().parents[3]  # installed source tree

ADMIN_CLIENT_ID = "kb-admin"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run docker compose against the local profile from the repo root."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_LOCAL), *args]
    return subprocess.run(cmd, cwd=DEPLOY_ROOT, check=check)


def _compose_profile(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _compose("--profile", "tunnel", *args, check=check)


def _load_env() -> dict[str, str]:
    """Deployment .env values, with real environment variables taking
    precedence (CI and tests override without rewriting files)."""
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith(("KB_", "CF_"))})
    return values


def _env_repl(k: str, v: str) -> None:
    """Insert or replace a key in .env, preserving comments and order."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    out, seen = [], False
    for line in lines:
        if line.strip().startswith(f"{k}="):
            out.append(f"{k}={v}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n")


def _api_base() -> str:
    """Local API base URL — loopback by default (ADR-019; repair §2)."""
    env = _load_env()
    host = env.get("KB_BIND_HOST", "127.0.0.1")
    port = env.get("KB_BIND_PORT", "8000")
    return f"http://{host}:{port}"


class AdminApiError(RuntimeError):
    """Admin API returned an error payload the CLI should surface."""


async def _admin_request(method: str, path: str, json_body: dict | None = None) -> dict:
    """Authenticated call to /v1/admin/* on the local loopback API."""
    import httpx

    env = _load_env()
    token = env.get("KB_ADMIN_TOKEN", "")
    if not token:
        raise AdminApiError(
            "KB_ADMIN_TOKEN is missing from .env — run `kb setup`, then `kb up` "
            "(the API seeds the admin client from the token at startup)"
        )
    try:
        r = await httpx.AsyncClient(timeout=15).request(
            method,
            f"{_api_base()}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
        )
    except httpx.ConnectError as exc:
        raise AdminApiError(
            f"cannot reach the local API at {_api_base()} — is the stack up? (`kb up`)"
        ) from exc
    if r.status_code == 401:
        raise AdminApiError(
            "admin credential rejected — if KB_ADMIN_TOKEN was regenerated in .env, "
            "re-run with the token the API knows, or `kb up` again to re-seed"
        )
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code >= 400:
        detail = body.get("message") or body.get("detail") or r.text[:200]
        raise AdminApiError(f"admin API error ({r.status_code}): {detail}")
    return body


def _api_json(path: str) -> dict | None:
    """Sync unauthenticated GET (health/ready) for status display."""
    import httpx

    try:
        r = httpx.get(f"{_api_base()}{path}", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# kb setup
# --------------------------------------------------------------------------


def _cmd_setup(args) -> None:
    if ENV_PATH.exists() and not args.force:
        print(f".env already exists at {ENV_PATH} — left untouched (use --force to reset)")
    else:
        if not ENV_EXAMPLE.exists():
            sys.exit("missing .env.example — run from a clean checkout of the repo")
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        _env_repl("POSTGRES_PASSWORD", pysecrets.token_urlsafe(24))
        _env_repl("FALKORDB_PASSWORD", pysecrets.token_urlsafe(24))
        _env_repl("KB_ADMIN_TOKEN", pysecrets.token_urlsafe(32))
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        print(f"created .env with generated secrets incl. KB_ADMIN_TOKEN (mode 0600): {ENV_PATH}")
        print("store the values in a password manager now — they are NOT printed again")

    env = _load_env()
    # LLM/embedding validation: warn, do not block (repair §4).
    if not env.get("KB_LLM_API_KEY"):
        print(
            "NOTE: KB_LLM_API_KEY is empty — the KB will run READ-ONLY\n"
            "      (search/context work; extraction of new captures is disabled).\n"
            "      Add the key to .env and `kb restart` to enable full processing."
        )
    if not env.get("CF_TUNNEL_TOKEN"):
        print(
            "NOTE: CF_TUNNEL_TOKEN is not set — local-only mode. The Cloudflare\n"
            "      tunnel profile stays off; add it later (docs/TUNNEL-SETUP.md)."
        )


# --------------------------------------------------------------------------
# stack lifecycle
# --------------------------------------------------------------------------


def _cmd_up(args) -> None:
    env = _load_env()
    profiles = ["--profile", "tunnel"] if env.get("CF_TUNNEL_TOKEN") else []
    print("starting ecosystem-kb stack (postgres, falkordb, migrate, api, worker)…")
    _compose(*profiles, "up", "-d", "--wait")
    print("stack is up. Next: `kb status`, then `kb client create` + `kb connect`.")


def _cmd_down(args) -> None:
    _compose_profile("down")
    print("stack stopped. Volumes preserved — `kb up` brings everything back.")


def _cmd_restart(args) -> None:
    _cmd_down(args)
    _cmd_up(args)


def _cmd_logs(args) -> None:
    _compose("logs", "--tail", str(args.tail), *(args.service or []))


def _cmd_nuke(args) -> None:
    """Destructive: stop the stack AND delete volumes. Explicit confirmation."""
    if not args.yes:
        print(
            "This deletes ALL knowledge (postgres + falkordb volumes).\n"
            "Run `kb backup` first if you care about the data.\n"
            "Re-run with --yes to confirm."
        )
        return
    _compose_profile("down", "-v")
    print("stack removed including volumes.")


# --------------------------------------------------------------------------
# kb status / doctor
# --------------------------------------------------------------------------


def _container_states() -> dict[str, str]:
    """service -> health from `docker compose ps --format json`."""
    try:
        out = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_LOCAL), "ps", "-a", "--format", "json"],
            cwd=DEPLOY_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return {}
    import json as _json

    states = {}
    for line in out.splitlines():
        try:
            svc = _json.loads(line)
            states[svc.get("Service", "?")] = svc.get("Health") or svc.get("State", "?")
        except Exception:
            continue
    return states


async def _cmd_status(args) -> None:
    env = _load_env()
    states = _container_states()

    def line(label: str, value: str) -> None:
        print(f"{label:<16}{value}")

    line("KB API", states.get("api", "not created"))
    line("PostgreSQL", states.get("postgres", "not created"))
    line("FalkorDB", states.get("falkordb", "not created"))
    line("Worker", states.get("worker", "not created"))
    line("Cloudflared", states.get("cloudflared", "off (local-only mode)" if not env.get("CF_TUNNEL_TOKEN") else "not created"))
    mcp_ok = await _mcp_probe()
    line("MCP endpoint", "healthy (initialize ok)" if mcp_ok else "not reachable on the local port")
    ready = _api_json("/ready")
    if ready:
        line("Readiness", "ready" if ready.get("ready") else "degraded")
        for k, v in ready.get("checks", {}).items():
            line(f"  {k}", str(v))
    else:
        line("Readiness", f"api not reachable on {_api_base()}")
    if env.get("KB_PUBLIC_BASE_URL") or env.get("KB_PUBLIC_URL"):
        public = env.get("KB_PUBLIC_BASE_URL") or env.get("KB_PUBLIC_URL")
        line("Public URL", public.rstrip("/") + "/mcp")
    line("Last backup", _last_backup() or "none yet — run `kb backup`")
    # Knowledge + queue snapshot through the admin API (no direct DB access).
    try:
        stats = await _admin_request("GET", "/v1/admin/stats")
        k = stats["knowledge"]
        q = stats["queue"]
        line("Knowledge", f"{k['entities']} entities / {k['provenance_links']} provenance links")
        line("Queue", f"{q.get('queued', 0)} pending / {q.get('dead_letter', 0)} dead-letter")
    except AdminApiError as exc:
        line("Knowledge", f"unavailable: {str(exc)[:90]}")


async def _cmd_doctor(args) -> None:
    """Deep diagnostics. Mandatory service failures exit 1; optional
    configuration gaps print as WARN (repair §4):
    - local-only mode (no CF_TUNNEL_TOKEN) is healthy by design;
    - no backup yet is a warning until the first backup exists;
    - missing LLM credentials mean read-only/extraction-disabled operation.
    --strict turns provisioned-deployment gaps into failures."""
    env = _load_env()
    failures: list[str] = []
    warnings: list[str] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail and args.verbose else ""))
        if not passed:
            failures.append(name)

    def warn(name: str, detail: str) -> None:
        print(f"[WARN] {name}" + (f" — {detail}" if detail and args.verbose else ""))
        warnings.append(name)

    # -- .env is mandatory for any deployment mode.
    check(".env exists with 0600", ENV_PATH.exists() and (stat.S_IMODE(ENV_PATH.stat().st_mode) & 0o077) == 0)

    # -- Docker/compose only matter for the local Compose deployment.
    if shutil.which("docker") is None:
        warn("docker engine", "not found — required for `kb up` on this machine")
    else:
        try:
            _compose("version", check=False)
            check("docker compose", True)
        except Exception:
            check("docker compose", False)

    # -- API is the single mandatory service surface.
    ready = _api_json("/ready")
    check("API readiness", ready is not None and bool(ready.get("ready")))
    if ready and not ready.get("ready"):
        for k, v in ready.get("checks", {}).items():
            print(f"       {k}: {str(v)[:120]}")

    mcp_ok = await _mcp_probe()
    check("MCP endpoint responds to initialize", mcp_ok)

    # -- Admin credential works through the API (no direct DB path exists).
    admin_ok = False
    try:
        stats = await _admin_request("GET", "/v1/admin/stats")
        admin_ok = True
        q = stats["queue"]
        print(f"[PASS] admin API + control plane — pending={q.get('queued', 0)} dead={q.get('dead_letter', 0)}")
    except AdminApiError as exc:
        check("admin API + control plane", False, str(exc)[:120])

    # -- optional configuration (repair §4): warnings unless --strict.
    if env.get("CF_TUNNEL_TOKEN"):
        states = _container_states()
        t_state = states.get("cloudflared", "not running")
        if t_state == "running" or t_state == "healthy":
            print("[PASS] cloudflared tunnel — running")
        else:
            (check if args.strict else warn)("cloudflared tunnel", f"token set but container {t_state}")
    else:
        warn("cloudflared tunnel", "not configured — LOCAL-ONLY mode (healthy; see docs/TUNNEL-SETUP.md)")

    if not env.get("KB_LLM_API_KEY"):
        warn("LLM extraction config", "missing — READ-ONLY mode: search/context OK, extraction of captures disabled")
    else:
        print(f"[PASS] LLM extraction config — provider={env.get('KB_LLM_PROVIDER')}, model={env.get('KB_LLM_MODEL')}")

    last = _last_backup()
    if last is None:
        warn("last backup", "no backup yet — run `kb backup`")
    else:
        print(f"[PASS] last backup — {last}")

    states = _container_states()
    print(f"[{'PASS' if admin_ok else 'WARN'}] persistence volumes — postgres={states.get('postgres', '?')} falkordb={states.get('falkordb', '?')}")

    print(
        f"\ndoctor: {len(failures)} failure(s), {len(warnings)} warning(s)"
        + (" — FAILURES PRESENT" if failures else " — healthy" + (" (local-only)" if not env.get("CF_TUNNEL_TOKEN") else ""))
    )
    if args.strict and warnings:
        print("strict mode: warnings are treated as failures for fully provisioned deployments")
    sys.exit(1 if failures or (args.strict and warnings) else 0)


async def _mcp_probe() -> bool:
    """Protocol-level MCP initialize over the local loopback API port."""
    env = _load_env()
    token = env.get("KB_ADMIN_TOKEN", "")
    try:
        import httpx

        r = await httpx.AsyncClient(timeout=10).post(
            f"{_api_base()}/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "kb-doctor", "version": "0.0.0"},
            }},
        )
        return r.status_code == 200
    except Exception:
        return False


def _last_backup() -> str | None:
    if not BACKUP_ROOT.exists():
        return None
    manifests = sorted(BACKUP_ROOT.glob("*/manifest.json"))
    if not manifests:
        return None
    import json as _json

    m = _json.loads(manifests[-1].read_text())
    return f"{m.get('created_at', '?')} ({manifests[-1].parent.name})"


# --------------------------------------------------------------------------
# client registry + connect wizard (WP-E)
# --------------------------------------------------------------------------

CLIENT_SNIPPET = """# ecosystem-kb — agent usage instructions
Before substantial work on any task:
1. Call `kb_context` with the task description and, when known, the project
   and repository. Treat source-backed CURRENT decisions and requirements it
   returns as constraints, not suggestions.
2. If KB context conflicts with the current repository state or a newer
   verified source, surface the conflict in your answer; do not blindly
   follow stale memory.
3. Capture durable outcomes through `kb_capture`: decisions (with status),
   important corrections, resolved problems, findings worth keeping.
4. Do NOT capture transient chatter, secrets, speculative claims presented
   as facts, or entire transcripts.
5. When you captured something durable, include the KB receipt id in your
   final answer.
Retrieved items carry epistemic status (proposed/accepted/superseded/
disputed) — respect it when reasoning about what is established.
"""


async def _cmd_client(args) -> None:
    try:
        if args.client_cmd == "create":
            body = await _admin_request("POST", "/v1/admin/clients", {
                "client_id": args.client_id,
                "client_name": args.name,
                "role": args.role,
            })
            print(f"client '{body['client_id']}' created with scopes {body['scopes']}")
            print(f"bearer token (shown ONCE, store it now): {body['token']}")
            print(f"rotate later with: kb client rotate {args.client_id}")
        elif args.client_cmd == "list":
            body = await _admin_request("GET", "/v1/admin/clients")
            rows = body["clients"]
            if not rows:
                print("no clients registered")
            for c in rows:
                print(f"{c['client_id']:<24} {c['status']:<10} scopes={','.join(c['scopes'])} namespaces={','.join(c['allowed_namespaces'])}")
        elif args.client_cmd == "revoke":
            body = await _admin_request("POST", f"/v1/admin/clients/{args.client_id}/revoke")
            print(f"client '{body['client_id']}' revoked ({body['status']})")
        elif args.client_cmd == "rotate":
            body = await _admin_request("POST", f"/v1/admin/clients/{args.client_id}/rotate")
            print(f"new token for '{body['client_id']}' (shown ONCE): {body['token']}")
    except AdminApiError as exc:
        sys.exit(str(exc))


def _default_client_name(target: str) -> str:
    """Repair §3: default the client identifier from the selected target."""
    return {"claude-code": "claude-code", "cursor": "cursor"}.get(target, "mcp-client")


def _cursor_config(endpoint: str, token: str) -> dict:
    """Cursor mcp.json fragment for this KB only — merged, never replacing
    unrelated servers (repair §3)."""
    return {
        "mcpServers": {
            "ecosystem-kb": {
                "url": endpoint,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }


def _merge_mcp_json(path: Path, fragment: dict) -> dict:
    """Read existing mcp.json (if any), merge our server entry, keep the rest."""
    import json

    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            sys.exit(f"{path} is not valid JSON — fix or remove it first (nothing was written)")
    else:
        existing = {}
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers.update(fragment["mcpServers"])
    merged = dict(existing)
    merged["mcpServers"] = servers
    return merged


async def _cmd_connect(args) -> None:
    env = _load_env()
    name = args.name or _default_client_name(args.target)
    # Canonical endpoint is /mcp with NO trailing slash (runbook §1.2.5):
    # the app now serves /mcp directly (no redirect to /mcp/), so the old
    # slash workaround is gone. KB_PUBLIC_URL is the one-release alias for
    # KB_PUBLIC_BASE_URL.
    public = env.get("KB_PUBLIC_BASE_URL") or env.get("KB_PUBLIC_URL")
    endpoint = f"{public.rstrip('/')}/mcp" if public else f"{_api_base()}/mcp"

    existing = None
    try:
        listed = await _admin_request("GET", "/v1/admin/clients")
        existing = next((c for c in listed["clients"] if c["client_id"] == name), None)
    except AdminApiError as exc:
        sys.exit(str(exc))

    if existing is not None:
        if existing["status"] != "active":
            sys.exit(
                f"client '{name}' is {existing['status']} — create a differently-named client\n"
                f"or reactivate it via the admin API before connecting."
            )
        if not args.rotate:
            sys.exit(
                f"client '{name}' already exists and is active.\n"
                f"Re-running connect would rotate its credential and invalidate every\n"
                f"configuration that still uses the old token.\n"
                f"If you LOST the token, re-run with --rotate to issue a fresh one."
            )
        body = await _admin_request("POST", f"/v1/admin/clients/{name}/rotate")
        token = body["token"]
        print(f"rotated credential for '{name}' — the previous token stops working immediately")
    else:
        body = await _admin_request("POST", "/v1/admin/clients", {
            "client_id": name,
            "role": "contributor",
        })
        token = body["token"]
        print(f"client '{name}' created with scopes {body['scopes']}")

    print(f"bearer token (shown ONCE): {token}\n")

    if args.target == "claude-code":
        snippet_cmd = (
            f"claude mcp add --transport http ecosystem-kb {endpoint} \\\n"
            f'  --header "Authorization: Bearer {token}"'
        )
        print("Run this in your terminal:\n")
        print(snippet_cmd)
        print("\nThen add the usage instructions to your global rules:")
        print("  paste docs/clients/AGENT-INSTRUCTIONS.md into your rules file")
    elif args.target == "cursor":
        # Repair §3: VALID JSON with real values, parsed back before display,
        # optionally written into an mcp.json that preserves other servers.
        import json

        fragment = _cursor_config(endpoint, token)
        config = fragment  # display-only by default
        target_path: Path | None = None
        if args.write:
            target_path = Path(args.path).expanduser() if args.path else Path.home() / ".cursor" / "mcp.json"
            config = _merge_mcp_json(target_path, fragment)
        rendered = json.dumps(config, indent=2)
        json.loads(rendered)  # validate before displaying (repair §3)
        if target_path is not None:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(rendered + "\n")
            print(f"merged ecosystem-kb server into {target_path} (existing servers preserved)")
        else:
            print("Add to ~/.cursor/mcp.json (or project .cursor/mcp.json):\n")
        print(rendered)
        if not target_path:
            print("\ntip: `kb connect cursor --write` merges this into ~/.cursor/mcp.json automatically")
    else:  # generic
        snippet = (
            "MCP endpoint (Streamable HTTP):\n"
            f"  {endpoint}\n"
            "Headers:\n"
            f"  Authorization: Bearer {token}\n"
            "Tools: kb_context, kb_capture, kb_search, kb_get_entity, kb_get_provenance,\n"
            "       kb_get_context, kb_propose_update, kb_get_maintenance_report, kb_submit_event"
        )
        print(snippet)

    instructions = DEPLOY_ROOT / "docs" / "clients" / "AGENT-INSTRUCTIONS.md"
    if not instructions.exists():
        (DEPLOY_ROOT / "docs" / "clients").mkdir(parents=True, exist_ok=True)
        instructions.write_text(CLIENT_SNIPPET)
        print("\nusage instructions written to docs/clients/AGENT-INSTRUCTIONS.md")


async def _cmd_disconnect(args) -> None:
    name = args.client_name
    try:
        await _admin_request("POST", f"/v1/admin/clients/{name}/revoke")
    except AdminApiError as exc:
        sys.exit(str(exc))
    print(f"client '{name}' revoked — its MCP connections stop authenticating immediately")


# --------------------------------------------------------------------------
# tunnel (WP-C)
# --------------------------------------------------------------------------


def _cmd_tunnel(args) -> None:
    env = _load_env()
    if args.tunnel_cmd == "status":
        if not env.get("CF_TUNNEL_TOKEN"):
            print("tunnel: NOT configured — add CF_TUNNEL_TOKEN to .env (docs/TUNNEL-SETUP.md)")
            return
        states = _container_states()
        print(f"tunnel: token configured; cloudflared container: {states.get('cloudflared', 'not running')}")
        public = (
            env.get("KB_PUBLIC_BASE_URL") or env.get("KB_PUBLIC_URL") or "(KB_PUBLIC_BASE_URL not set in .env)"
        )
        print(f"public URL: {public.rstrip('/')}/mcp")
        _compose("logs", "--tail", "20", "cloudflared")


# --------------------------------------------------------------------------
# backup / restore (WP-B5) — real paths, not instructions
# --------------------------------------------------------------------------


def _psql_docker(*parts: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "postgres", *parts],
        cwd=DEPLOY_ROOT, capture_output=True, text=True, check=check,
    )


def _cmd_backup(args) -> None:
    import datetime
    import hashlib
    import json

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output) if args.output else BACKUP_ROOT / f"kb-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir.with_suffix(".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # 1. PostgreSQL dump via the running container (strict failure).
    dump_path = tmp / "control_plane.sql"
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "postgres",
         "pg_dump", "-U", "kb", "-d", "ecosystem_kb"],
        cwd=DEPLOY_ROOT, capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        sys.exit(f"pg_dump FAILED — no backup written. {r.stderr.decode()[:300]}")
    dump_path.write_bytes(r.stdout)

    # 2. FalkorDB: BGSAVE then copy the RDB out of the volume.
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "falkordb",
         "redis-cli", "-a", _load_env().get("FALKORDB_PASSWORD", ""), "--no-auth-warning", "SAVE"],
        cwd=DEPLOY_ROOT, capture_output=True, text=True,
    )
    if "OK" not in r.stdout:
        sys.exit(f"FalkorDB SAVE FAILED — no backup written. {r.stdout[:200]} {r.stderr[:200]}")
    rdb_tmp = tmp / "falkordb.rdb"
    rc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "cp", "falkordb:/var/lib/falkordb/data/dump.rdb", str(rdb_tmp)],
        cwd=DEPLOY_ROOT, capture_output=True,
    )
    if rc.returncode != 0 or not rdb_tmp.exists() or rdb_tmp.stat().st_size == 0:
        sys.exit(f"FalkorDB RDB copy FAILED — no backup written. {rc.stderr.decode()[:300]}")

    # 3. Manifest with checksums (app version from pyproject).
    version = "unknown"
    pyproject = DEPLOY_ROOT / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                version = line.split('"')[1]
                break

    def sha256(p: Path) -> str:
        return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

    artifacts = {"control_plane.sql": dump_path, "falkordb.rdb": rdb_tmp}
    manifest = {
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "app_version": version,
        "components": {
            "postgres": {"file": "control_plane.sql", "sha256": sha256(dump_path), "bytes": dump_path.stat().st_size},
            "falkordb": {"file": "falkordb.rdb", "sha256": sha256(rdb_tmp), "bytes": rdb_tmp.stat().st_size},
        },
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # 4. Verify checksums, then atomically publish (plan §B5).
    for name, p in artifacts.items():
        key = "postgres" if name.startswith("control_plane") else "falkordb"
        assert sha256(p) == manifest["components"][key]["sha256"], f"checksum mismatch for {name}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp.rename(out_dir)
    print(f"backup verified and published: {out_dir}")
    print(
        "WARNING: this copy is on the same disk as the service — copy it elsewhere\n"
        "(external drive or object storage) to survive disk loss."
    )


def _cmd_restore(args) -> None:
    import json

    src = Path(args.path)
    manifest_path = src / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"no manifest.json under {src} — not a kb backup directory")
    manifest = json.loads(manifest_path.read_text())
    import hashlib

    for comp, meta in manifest["components"].items():
        p = src / meta["file"]
        if not p.exists():
            sys.exit(f"missing artifact for {comp}: {p}")
        digest = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        if digest != meta["sha256"]:
            sys.exit(f"checksum mismatch for {comp} — backup corrupt?")

    if not args.yes:
        print(
            f"Restoring backup {src} (created {manifest['created_at']}) OVERWRITES the live\n"
            "postgres + falkordb volumes. Recommended path is restoring into fresh\n"
            "volumes instead (plan §B5). Re-run with --yes to proceed on live volumes."
        )
        return
    print("checksums verified; restoring into live volumes…")
    _compose_profile("down")
    # Postgres: recreate volume, boot postgres only, restore dump.
    subprocess.run(["docker", "volume", "rm", "ecosystem-kb_postgres_data"], cwd=DEPLOY_ROOT, check=True)
    _compose("up", "-d", "--wait", "postgres")
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "postgres",
         "psql", "-U", "kb", "-d", "ecosystem_kb", "-v", "ON_ERROR_STOP=1"],
        cwd=DEPLOY_ROOT, input=(src / "control_plane.sql").read_text(),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"psql restore FAILED: {r.stderr[:400]}")
    # FalkorDB: copy RDB into the data volume, restart to load it.
    subprocess.run(["docker", "volume", "rm", "ecosystem-kb_falkordb_data"], cwd=DEPLOY_ROOT, check=True)
    _compose("up", "-d", "--wait", "falkordb")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "cp", str(src / "falkordb.rdb"), "falkordb:/var/lib/falkordb/data/dump.rdb"],
        cwd=DEPLOY_ROOT, check=True,
    )
    _compose("restart", "falkordb")
    _compose("up", "-d", "--wait")
    print("restore complete; run `kb doctor` to verify graph + control plane.")


# --------------------------------------------------------------------------
# legacy commands (unchanged behavior) + worker
# --------------------------------------------------------------------------


async def _bootstrap(settings, dev_token: str | None, seed_admin: bool = False) -> None:
    cp = ControlPlane(settings.database_url)
    from ecosystem_kb.adapters.postgres.migrations import migrate

    await migrate(cp.engine)
    if seed_admin:
        # Repair §1: the host never touches the DB; the API container seeds
        # the admin client from KB_ADMIN_TOKEN at startup. This path exists
        # for direct (non-Compose) deployments where the CLI process IS on
        # the same network as PostgreSQL.
        env_token = _load_env().get("KB_ADMIN_TOKEN") or None
        repos = Repositories(cp)
        token = await repos.clients.ensure_admin_client(token=env_token)
        if token:
            print(f"admin client '{ADMIN_CLIENT_ID}' seeded (credential: KB_ADMIN_TOKEN from .env)")
        else:
            print(f"admin client '{ADMIN_CLIENT_ID}' already present — left unchanged")
    elif not dev_token:  # --migrate-only skips client seeding
        print("control plane ready:", settings.database_url)
        print("namespaces:", settings.namespace_list)
    await cp.dispose()


async def _register_client(args) -> None:
    from contracts.clients import ROLE_SCOPES

    settings = get_settings()
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    repos = Repositories(cp)
    scopes = ROLE_SCOPES.get(args.role, ["kb:read"])
    token = await repos.clients.register(
        args.client_id, args.name or args.client_id, scopes, args.namespaces.split(",")
    )
    print(f"client '{args.client_id}' registered with scopes {scopes}")
    print(f"bearer token (store safely, shown once): {token}")
    await cp.dispose()


async def _run_worker() -> None:
    settings = get_settings()
    settings.validate_runtime_safety()
    cp = ControlPlane(settings.database_url)
    from ecosystem_kb.adapters.postgres.migrations import migrate

    await migrate(cp.engine)
    graphiti = build_graphiti(settings)
    await graphiti.build_indices_and_constraints()

    from ecosystem_kb.services.event_service import KbServices
    from ecosystem_kb.services.maintenance_service import MaintenanceService
    from ecosystem_kb.workers.ingestion import IngestionWorker

    services = KbServices(Repositories(cp), graphiti, settings)
    _maintenance = MaintenanceService(Repositories(cp), graphiti)
    worker = IngestionWorker(services, settings)
    print(f"ingestion worker {worker.worker_id} running (ctrl-c to stop)")
    try:
        await worker.run()
    finally:
        await cp.dispose()
        await graphiti.close()


async def _graph_health(namespace: str) -> None:
    settings = get_settings()
    graphiti = build_graphiti(settings)
    health = await check_graph(graphiti, namespace)
    print(
        f"healthy={health.ok} namespace={health.namespace} "
        f"entities={health.entity_count} edges={health.edge_count} "
        f"episodes={health.episode_count} detail={health.detail}"
    )
    await graphiti.close()


# --------------------------------------------------------------------------
# argument wiring
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kb", description="ecosystem-kb CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="First-run: create .env with generated secrets")
    p_setup.add_argument("--force", action="store_true", help="regenerate .env even if one exists")
    sub.add_parser("up", help="Start the local stack (Docker Compose)")
    sub.add_parser("down", help="Stop the stack (volumes preserved)")
    sub.add_parser("restart", help="Stop then start the stack")
    p_logs = sub.add_parser("logs", help="Tail service logs")
    p_logs.add_argument("service", nargs="*")
    p_logs.add_argument("--tail", type=int, default=100)
    sub.add_parser("status", help="Stack + knowledge + queue overview")
    p_doc = sub.add_parser("doctor", help="Deep health diagnostics")
    p_doc.add_argument("--verbose", action="store_true")
    p_doc.add_argument("--strict", action="store_true",
                       help="treat optional-provisioning warnings (tunnel, backup, LLM) as failures")
    p_bak = sub.add_parser("backup", help="Verified backup of postgres + falkordb")
    p_bak.add_argument("--output", help="backup output directory")
    p_res = sub.add_parser("restore", help="Restore a backup (checksums verified first)")
    p_res.add_argument("path")
    p_res.add_argument("--yes", action="store_true", help="confirm overwrite of live volumes")
    p_nuke = sub.add_parser("nuke", help="DANGER: remove stack AND volumes")
    p_nuke.add_argument("--yes", action="store_true")

    p_cli = sub.add_parser("client", help="MCP client registry")
    cli_sub = p_cli.add_subparsers(dest="client_cmd", required=True)
    p_create = cli_sub.add_parser("create")
    p_create.add_argument("client_id")
    p_create.add_argument("--name", default=None)
    p_create.add_argument("--role", default="contributor",
                          choices=["reader", "contributor", "reviewer", "maintainer", "administrator"])
    cli_sub.add_parser("list")
    p_revoke = cli_sub.add_parser("revoke")
    p_revoke.add_argument("client_id")
    p_rotate = cli_sub.add_parser("rotate")
    p_rotate.add_argument("client_id")

    p_conn = sub.add_parser("connect", help="Create a client + print its MCP config")
    p_conn.add_argument("target", choices=["claude-code", "cursor", "generic"])
    p_conn.add_argument("--name", default=None,
                        help="client identifier (defaults from the target: claude-code/cursor/mcp-client)")
    p_conn.add_argument("--rotate", action="store_true",
                        help="EXPLICITLY rotate the credential if the client already exists")
    p_conn.add_argument("--write", action="store_true",
                        help="(cursor) merge the config into mcp.json instead of only printing it")
    p_conn.add_argument("--path", default=None,
                        help="(cursor with --write) mcp.json path (default ~/.cursor/mcp.json)")

    p_disc = sub.add_parser("disconnect", help="Revoke a client's credential")
    p_disc.add_argument("client_name")

    p_tun = sub.add_parser("tunnel", help="Cloudflare tunnel status")
    tun_sub = p_tun.add_subparsers(dest="tunnel_cmd", required=True)
    tun_sub.add_parser("status")

    # Legacy commands (backward-compatible).
    p_boot = sub.add_parser("bootstrap", help="Create control-plane tables")
    p_boot.add_argument("--migrate-only", action="store_true",
                        help="Run migrations and exit (used by compose 'migrate' service)")
    p_boot.add_argument("--seed-admin", action="store_true",
                        help="Also ensure the kb-admin client exists (direct deployments; "
                             "the Compose API container does this automatically at startup)")
    sub.add_parser("worker", help="Run the ingestion worker")
    p_client = sub.add_parser("register-client", help="Register an integrating client")
    p_client.add_argument("client_id")
    p_client.add_argument("--name", default=None)
    p_client.add_argument("--role", default="reader",
                          choices=["reader", "contributor", "reviewer", "maintainer", "administrator"])
    p_client.add_argument("--namespaces", default="ecosystem-dev")
    p_health = sub.add_parser("graph-health", help="Check graph store health")
    p_health.add_argument("--namespace", default=None)

    args = parser.parse_args(argv)

    if args.command == "setup":
        _cmd_setup(args)
    elif args.command == "up":
        _cmd_up(args)
    elif args.command == "down":
        _cmd_down(args)
    elif args.command == "restart":
        _cmd_restart(args)
    elif args.command == "logs":
        _cmd_logs(args)
    elif args.command == "nuke":
        _cmd_nuke(args)
    elif args.command == "backup":
        _cmd_backup(args)
    elif args.command == "restore":
        _cmd_restore(args)
    elif args.command == "tunnel":
        _cmd_tunnel(args)
    elif args.command == "status":
        asyncio.run(_cmd_status(args))
    elif args.command == "doctor":
        asyncio.run(_cmd_doctor(args))
    elif args.command == "client":
        asyncio.run(_cmd_client(args))
    elif args.command == "connect":
        asyncio.run(_cmd_connect(args))
    elif args.command == "disconnect":
        asyncio.run(_cmd_disconnect(args))
    elif args.command == "bootstrap":
        settings = get_settings()
        asyncio.run(_bootstrap(settings, None if args.migrate_only else "skip-dev-token",
                               seed_admin=args.seed_admin))
    elif args.command == "register-client":
        asyncio.run(_register_client(args))
    elif args.command == "worker":
        asyncio.run(_run_worker())
    elif args.command == "graph-health":
        ns = args.namespace or get_settings().default_namespace
        asyncio.run(_graph_health(ns))


if __name__ == "__main__":
    main()
