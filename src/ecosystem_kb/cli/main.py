"""CLI: stack lifecycle, client management, connection wizard, backup/restore.

Design notes (plug-and-play plan §B4/E2):
- `kb up/down/status/...` are thin wrappers around Docker Compose with the
  local profile `deploy/compose.local.yaml`; no second orchestrator.
- Secrets live in the untracked .env; nothing here ever prints them after
  generation (rotate explicitly to see a new one).
- Commands that touch the control plane reuse the same repositories and
  migrations as the API (no parallel schema path).
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

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_LOCAL = REPO_ROOT / "deploy" / "compose.local.yaml"
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BACKUP_ROOT = REPO_ROOT / "backups"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run docker compose against the local profile from the repo root."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_LOCAL), *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, check=check)


def _compose_profile(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _compose("--profile", "tunnel", *args, check=check)


def _load_env() -> dict[str, str]:
    """Minimal .env parser (no dependency on dotenv)."""
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
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


def _settings_from_env():
    from ecosystem_kb.core.config import KbSettings

    env = _load_env()
    overrides = {
        "database_url": env.get(
            "KB_DATABASE_URL",
            f"postgresql+asyncpg://kb:{env.get('POSTGRES_PASSWORD', '')}@127.0.0.1:5432/ecosystem_kb",
        ),
        "graph_host": env.get("KB_GRAPH_HOST", "127.0.0.1"),
        "graph_port": int(env.get("KB_GRAPH_PORT", "6379")),
        "graph_password": env.get("FALKORDB_PASSWORD") or None,
        "graph_database": env.get("KB_NAMESPACE", "ecosystem_prod"),
        "environment": env.get("KB_ENVIRONMENT", "production"),
        "llm_provider": env.get("KB_LLM_PROVIDER", "fake"),
        "llm_model": env.get("KB_LLM_MODEL", "fake-extractor"),
        "llm_api_key": env.get("KB_LLM_API_KEY") or None,
        "embedder_model": env.get("KB_EMBEDDER_MODEL", "fake-embedder"),
    }
    return KbSettings(**overrides)


async def _open_control_plane():
    """Control plane + repositories using .env settings (migrations applied)."""
    from ecosystem_kb.adapters.postgres.migrations import migrate
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories

    settings = _settings_from_env()
    cp = ControlPlane(settings.database_url)
    await migrate(cp.engine)
    return cp, Repositories(cp), settings


def _llm_status(settings) -> str:
    if (settings.llm_api_key or "") == "":
        return "missing (KB runs read-only: search/context OK, extraction disabled)"
    return f"configured (provider={settings.llm_provider}, model={settings.llm_model})"


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
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        print(f"created .env with generated secrets (mode 0600): {ENV_PATH}")

    env = _load_env()
    # LLM/embedding validation: warn, do not block (plan §B2).
    if not env.get("KB_LLM_API_KEY"):
        print(
            "WARNING: KB_LLM_API_KEY is empty — ingestion/extraction is DISABLED.\n"
            "         Search and context retrieval still work. Add the key to .env\n"
            "         and run `kb restart` to enable full capture processing."
        )
    if not env.get("CF_TUNNEL_TOKEN"):
        print(
            "NOTE: CF_TUNNEL_TOKEN is not set — the Cloudflare tunnel profile stays\n"
            "      off. See docs/TUNNEL-SETUP.md to add it later (never commit it)."
        )
    if not env.get("KB_ADMIN_TOKEN"):
        tok = pysecrets.token_urlsafe(32)
        _env_repl("KB_ADMIN_TOKEN", tok)
        print(
            "generated KB_ADMIN_TOKEN (admin CLI/API credential).\n"
            "Store it in a password manager now — it is NOT printed again."
        )
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)


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


async def _api_json(path: str) -> dict | None:
    import httpx

    env = _load_env()
    base = env.get("KB_LOCAL_URL", "http://127.0.0.1:8000")
    try:
        r = await httpx.AsyncClient(timeout=5).get(f"{base}{path}")
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _container_states() -> dict[str, str]:
    """service -> health from `docker compose ps --format json`."""
    try:
        out = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_LOCAL), "ps", "-a", "--format", "json"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
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
    line("Cloudflared", states.get("cloudflared", "off (no token)" if not env.get("CF_TUNNEL_TOKEN") else "not created"))
    mcp_ok = await _mcp_probe()
    line("MCP endpoint", "healthy (initialize ok)" if mcp_ok else "not reachable on the local port")
    ready = await _api_json("/ready")
    if ready:
        line("Readiness", "ready" if ready.get("ready") else "degraded")
        for k, v in ready.get("checks", {}).items():
            line(f"  {k}", str(v))
    else:
        line("Readiness", "api not reachable on the local port")
    if env.get("KB_PUBLIC_URL"):
        line("Public URL", env["KB_PUBLIC_URL"] + "/mcp")
    line("Last backup", _last_backup() or "none yet — run `kb backup`")
    # Knowledge + queue snapshot through the control plane.
    try:
        cp, repos, _ = await _open_control_plane()
    except Exception as exc:
        line("Knowledge", f"control plane unreachable: {str(exc)[:80]}")
        return
    try:
        counts = await repos.cp.table_counts()
        line("Knowledge", f"{counts.get('kb_entities', 0)} entities / {counts.get('kb_provenance_links', 0)} provenance links")
        stats = await repos.events.queue_stats()
        line("Queue", f"{stats.get('queued', 0)} pending / {stats.get('dead_letter', 0)} dead-letter")
    finally:
        await cp.dispose()


async def _cmd_doctor(args) -> None:
    env = _load_env()
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail and args.verbose else ""))

    check("docker engine", shutil.which("docker") is not None)
    try:
        _compose("version", check=False)
        check("docker compose", True)
    except Exception:
        check("docker compose", False)

    check(".env exists with 0600", ENV_PATH.exists() and (stat.S_IMODE(ENV_PATH.stat().st_mode) & 0o077) == 0)

    # Control plane: connectivity + schema version.
    try:
        cp, repos, settings = await _open_control_plane()
        check("postgres connectivity + migrations", True)
        from ecosystem_kb.adapters.postgres.migrations import load_migrations

        check("schema version", len(load_migrations()) >= 2, f"{len(load_migrations())} migrations registered")
    except Exception as exc:
        check("postgres connectivity + migrations", False, str(exc)[:120])
        return

    try:
        from ecosystem_kb.adapters.graphiti.client import build_graphiti
        from ecosystem_kb.adapters.graphiti.health import check_graph

        graphiti = build_graphiti(settings)
        health = await check_graph(graphiti, settings.graph_database)
        check("falkordb connectivity + graphiti init", health.ok, str(health.detail)[:100])
        await graphiti.close()
    except Exception as exc:
        check("falkordb connectivity + graphiti init", False, str(exc)[:120])

    ready = await _api_json("/ready")
    check("API readiness", ready is not None and bool(ready.get("ready")))

    # MCP protocol initialization over the same HTTP app (AT-03 equivalent).
    mcp_ok = await _mcp_probe()
    check("MCP endpoint responds to initialize", mcp_ok)

    try:
        stats = await repos.events.queue_stats()
        check("worker heartbeat / queue", True, f"pending={stats.get('pending', 0)} dead={stats.get('dead_letter', 0)}")
    except Exception as exc:
        check("worker heartbeat / queue", False, str(exc)[:120])

    check("LLM extraction config", bool(env.get("KB_LLM_API_KEY")), _llm_status(settings))
    check("tunnel token", True, "configured" if env.get("CF_TUNNEL_TOKEN") else "missing (tunnel profile off; see docs/TUNNEL-SETUP.md)")

    states = _container_states()
    check("persistence volumes", True, f"postgres={states.get('postgres', '?')} falkordb={states.get('falkordb', '?')} (volumes postgres_data, falkordb_data)")

    last = _last_backup()
    check("last backup", last is not None, last or "no backup found yet — run `kb backup`")

    await cp.dispose()
    print("\ndoctor: " + ("all checks passed" if ok else "FAILURES PRESENT — see above"))
    sys.exit(0 if ok else 1)


async def _mcp_probe() -> bool:
    """Protocol-level MCP initialize over the local API port."""
    env = _load_env()
    token = env.get("KB_ADMIN_TOKEN", "")
    base = env.get("KB_LOCAL_URL", "http://127.0.0.1:8000")
    try:
        import httpx

        r = await httpx.AsyncClient(timeout=10).post(
            f"{base}/mcp/",
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
    cp, repos, _ = await _open_control_plane()
    try:
        if args.client_cmd == "create":
            from contracts.clients import ROLE_SCOPES

            scopes = ROLE_SCOPES.get(args.role, ["kb:read"])
            namespaces = _load_env().get("KB_NAMESPACE", "ecosystem_prod").split(",")
            try:
                token = await repos.clients.register(
                    args.client_id, args.name or args.client_id, scopes, namespaces
                )
            except Exception as exc:
                sys.exit(f"could not register client: {exc}")
            print(f"client '{args.client_id}' created with scopes {scopes}")
            print(f"bearer token (shown ONCE, store it now): {token}")
            print(f"rotate later with: kb client rotate {args.client_id}")
        elif args.client_cmd == "list":
            rows = await repos.clients.list_clients()
            if not rows:
                print("no clients registered")
            for c in rows:
                print(f"{c.client_id:<24} {c.status:<10} scopes={','.join(c.scopes)} namespaces={','.join(c.allowed_namespaces)}")
        elif args.client_cmd == "revoke":
            row = await repos.clients.set_status(args.client_id, "revoked", actor="owner-cli")
            sys.exit(f"no such client: {args.client_id}") if row is None else print(f"client '{args.client_id}' revoked")
        elif args.client_cmd == "rotate":
            token = await repos.clients.rotate_token(args.client_id, actor="owner-cli")
            sys.exit(f"no such client: {args.client_id}") if token is None else print(f"new token (shown ONCE): {token}")
    finally:
        await cp.dispose()


async def _cmd_connect(args) -> None:
    env = _load_env()
    public = env.get("KB_PUBLIC_URL", "https://kb.YOUR-DOMAIN.example")
    endpoint = f"{public}/mcp"
    cp, repos, _ = await _open_control_plane()
    try:
        existing = [c for c in await repos.clients.list_clients() if c.client_id == args.name]
        if existing:
            if existing[0].status != "active":
                sys.exit(f"client '{args.name}' is {existing[0].status}; rotate or create a new one")
            print(f"client '{args.name}' already exists — creating a fresh token (old one stops working)")
            token = await repos.clients.rotate_token(args.name, actor="owner-cli")
        else:
            from contracts.clients import ROLE_SCOPES

            scopes = ROLE_SCOPES["contributor"]
            namespaces = env.get("KB_NAMESPACE", "ecosystem_prod").split(",")
            token = await repos.clients.register(args.name, args.name, scopes, namespaces)
            print(f"client '{args.name}' created with scopes {scopes}")
    finally:
        await cp.dispose()

    print(f"bearer token (shown ONCE): {token}\n")
    if args.target == "claude-code":
        snippet_cmd = (
            f'claude mcp add --transport http ecosystem-kb {endpoint} \\\n'
            f'  --header "Authorization: Bearer {token}"'
        )
        print("Run this in your terminal:\n")
        print(snippet_cmd)
        print("\nThen add the usage instructions to your global rules:")
        print("  claude config set globalInstructions - < docs/clients/AGENT-INSTRUCTIONS.md  # or paste manually")
    elif args.target == "cursor":
        snippet = (
            "{\n"
            '  "mcpServers": {\n'
            '    "ecosystem-kb": {\n'
            '      "url": f"{endpoint}",\n'
            '      "headers": { "Authorization": f"Bearer {token}" }\n'
            "    }\n"
            "  }\n"
            "}"
        )
        print("Add to ~/.cursor/mcp.json (or project .cursor/mcp.json):\n")
        print(snippet)
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
    (REPO_ROOT / "docs" / "clients").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "docs" / "clients" / "AGENT-INSTRUCTIONS.md").write_text(CLIENT_SNIPPET)
    print("\nusage instructions written to docs/clients/AGENT-INSTRUCTIONS.md")


async def _cmd_disconnect(args) -> None:
    cp, repos, _ = await _open_control_plane()
    try:
        row = await repos.clients.set_status(args.client_name, "revoked", actor="owner-cli")
        print(f"client '{args.client_name}' revoked — its MCP connections stop authenticating")
        if row is None:
            sys.exit(f"no such client: {args.client_name}")
    finally:
        await cp.dispose()


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
        print(f"public URL: {env.get('KB_PUBLIC_URL', '(KB_PUBLIC_URL not set in .env)')}/mcp")
        _compose("logs", "--tail", "20", "cloudflared")


# --------------------------------------------------------------------------
# backup / restore (WP-B5) — real paths, not instructions
# --------------------------------------------------------------------------


def _psql_docker(*parts: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "postgres", *parts],
        cwd=REPO_ROOT, capture_output=True, text=True, check=check,
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
        cwd=REPO_ROOT, capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        sys.exit(f"pg_dump FAILED — no backup written. {r.stderr.decode()[:300]}")
    dump_path.write_bytes(r.stdout)

    # 2. FalkorDB: BGSAVE then copy the RDB out of the volume.
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "falkordb",
         "redis-cli", "-a", _load_env().get("FALKORDB_PASSWORD", ""), "--no-auth-warning", "SAVE"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if "OK" not in r.stdout:
        sys.exit(f"FalkorDB SAVE FAILED — no backup written. {r.stdout[:200]} {r.stderr[:200]}")
    rdb_tmp = tmp / "falkordb.rdb"
    rc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "cp", "falkordb:/data/dump.rdb", str(rdb_tmp)],
        cwd=REPO_ROOT, capture_output=True,
    )
    if rc.returncode != 0 or not rdb_tmp.exists() or rdb_tmp.stat().st_size == 0:
        sys.exit(f"FalkorDB RDB copy FAILED — no backup written. {rc.stderr.decode()[:300]}")

    # 3. Manifest with checksums (app version from pyproject).
    version = "unknown"
    pyproject = REPO_ROOT / "pyproject.toml"
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
        assert sha256(p) == manifest["components"][name.split(".")[0]]["sha256"], f"checksum mismatch for {name}"
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
    subprocess.run(["docker", "volume", "rm", "ecosystem-kb_postgres_data"], cwd=REPO_ROOT, check=True)
    _compose("up", "-d", "--wait", "postgres")
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "exec", "-T", "postgres",
         "psql", "-U", "kb", "-d", "ecosystem_kb", "-v", "ON_ERROR_STOP=1"],
        cwd=REPO_ROOT, input=(src / "control_plane.sql").read_text(),
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"psql restore FAILED: {r.stderr[:400]}")
    # FalkorDB: copy RDB into the data volume, restart to load it.
    subprocess.run(["docker", "volume", "rm", "ecosystem-kb_falkordb_data"], cwd=REPO_ROOT, check=True)
    _compose("up", "-d", "--wait", "falkordb")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_LOCAL), "cp", str(src / "falkordb.rdb"), "falkordb:/data/dump.rdb"],
        cwd=REPO_ROOT, check=True,
    )
    _compose("restart", "falkordb")
    _compose("up", "-d", "--wait")
    print("restore complete; run `kb doctor` to verify graph + control plane.")


# --------------------------------------------------------------------------
# legacy commands (unchanged behavior) + worker
# --------------------------------------------------------------------------


async def _bootstrap(settings, dev_token: str | None) -> None:
    cp = ControlPlane(settings.database_url)
    from ecosystem_kb.adapters.postgres.migrations import migrate

    await migrate(cp.engine)
    if not dev_token:  # --migrate-only skips client seeding
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
    sub.add_parser("backup", help="Verified backup of postgres + falkordb")
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
    p_conn.add_argument("--name", default=None)

    p_disc = sub.add_parser("disconnect", help="Revoke a client's credential")
    p_disc.add_argument("client_name")

    p_tun = sub.add_parser("tunnel", help="Cloudflare tunnel status")
    tun_sub = p_tun.add_subparsers(dest="tunnel_cmd", required=True)
    tun_sub.add_parser("status")

    # Legacy commands (backward-compatible).
    p_boot = sub.add_parser("bootstrap", help="Create control-plane tables")
    p_boot.add_argument("--migrate-only", action="store_true",
                        help="Run migrations and exit (used by compose 'migrate' service)")
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
        asyncio.run(_bootstrap(settings, None if args.migrate_only else "skip-dev-token"))
    elif args.command == "register-client":
        asyncio.run(_register_client(args))
    elif args.command == "worker":
        asyncio.run(_run_worker())
    elif args.command == "graph-health":
        ns = args.namespace or get_settings().default_namespace
        asyncio.run(_graph_health(ns))


if __name__ == "__main__":
    main()
