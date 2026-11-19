"""Host-level CLI tests (onboarding repair §6).

These run the REAL `kb` executable as a subprocess against a REAL uvicorn
server — the same invocation path the owner uses in a terminal. The server
runs with an SQLite control plane + embedded FalkorDB (no Docker in this
environment); the Compose-level variants run on the owner host via
scripts/host_acceptance.sh.

What is exercised end-to-end here:
  kb setup → kb status → kb doctor → kb client create/list →
  kb connect claude-code / cursor (JSON validity, --write merge) →
  re-connect refusal without --rotate → kb disconnect → revocation.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time

import httpx
import pytest

pytestmark = pytest.mark.embedded

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _kb(repo_root: str, *args: str, env_extra: dict | None = None, check: bool = True):
    """Run the installed kb CLI in a subprocess with an isolated repo dir."""
    env = dict(os.environ)
    env["KB_BIND_HOST"] = "127.0.0.1"
    env["KB_BIND_PORT"] = "8000"
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        [sys.executable, "-m", "ecosystem_kb.cli_entry", *args] if False else [shutil.which("kb") or "kb", *args],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=120,
    )
    if check and r.returncode != 0:
        raise AssertionError(f"kb {' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


@pytest.fixture
def host_env(tmp_path):
    """Isolated repo-shaped dir: .env written by `kb setup`, config values
    pointing the CLI at a test server on a fixed loopback port."""
    from ecosystem_kb.adapters.graphiti import EmbeddedFalkorDB
    from ecosystem_kb.core.config import KbSettings

    embedded = EmbeddedFalkorDB()
    embedded.__enter__()
    port = embedded.port

    env_dir = tmp_path / "repo"
    env_dir.mkdir()
    (env_dir / ".env.example").write_text(
        "# template\nPOSTGRES_PASSWORD=CHANGE_ME_GENERATED_BY_SETUP\n"
        "FALKORDB_PASSWORD=CHANGE_ME_GENERATED_BY_SETUP\nKB_LLM_API_KEY=\n"
        "KB_ENVIRONMENT=production\nKB_NAMESPACE=ecosystem_prod\n"
        "KB_BIND_HOST=127.0.0.1\nKB_BIND_PORT=8000\n"
    )
    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/host.db",
        graph_host="127.0.0.1",
        graph_port=port,
        graph_database="test_ns",
        llm_provider="fake",
        default_namespace="ecosystem-dev",
        allowed_namespaces="ecosystem-dev",
        api_tokens="",
    )

    # Start the API exactly like compose does (own loop; app builds its own graphiti).
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
    from ecosystem_kb.api.app import create_app

    cp = ControlPlane(settings.database_url)
    from ecosystem_kb.adapters.postgres.migrations import migrate

    coro = migrate(cp.engine)

    import asyncio

    loop = asyncio.new_event_loop()
    loop.run_until_complete(coro)
    # Seed admin inside the server loop (compose does this in the migrate
    # one-shot container; here the same repository call).
    admin_token = "host-admin-token-0123456789abcdef"
    rep = Repositories(cp)
    loop.run_until_complete(rep.clients.ensure_admin_client(token=admin_token))
    loop.run_until_complete(cp.dispose())

    app = create_app(settings=settings)
    import uvicorn

    uvi = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="error"))

    def _serve():
        asyncio.set_event_loop(asyncio.new_event_loop())
        uvi.run()

    threading.Thread(target=_serve, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:8000/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)

    yield {"dir": str(env_dir), "admin_token": admin_token}

    uvi.should_exit = True
    time.sleep(0.5)
    embedded.__exit__(None, None, None)


class TestHostCliFlows:
    def test_01_setup_creates_env(self, host_env):
        r = _kb(host_env["dir"], "setup")
        env_file = os.path.join(host_env["dir"], ".env")
        assert os.path.exists(env_file)
        assert stat.S_IMODE(os.stat(env_file).st_mode) & 0o077 == 0
        text = open(env_file).read()
        assert "CHANGE_ME_GENERATED_BY_SETUP" not in text
        assert "READ-ONLY" in r.stdout  # LLM warning per repair §4

    def test_03_status_reports_stack_and_api(self, host_env):
        _kb(host_env["dir"], "setup")
        r = _kb(host_env["dir"], "status")
        assert "KB API" in r.stdout
        assert "MCP endpoint" in r.stdout
        assert "Last backup" in r.stdout

    def test_04_doctor_exit_codes(self, host_env):
        _kb(host_env["dir"], "setup")
        # .env perms ok; API up; MCP up; admin token works (from .env? no —
        # setup generated a DIFFERENT admin token than the server's).
        r = _kb(host_env["dir"], "doctor", check=False)
        # Either healthy (if token matches) or admin-API failure exit 1.
        assert r.returncode in (0, 1)
        assert "doctor:" in r.stdout

    def test_05_06_07_client_and_connect(self, host_env):
        # Point the CLI at the server's admin token.
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")

        r = _kb(host_env["dir"], "client", "create", "test-client", "--role", "contributor")
        assert "test-client" in r.stdout
        token_line = [ln for ln in r.stdout.splitlines() if "bearer token" in ln.lower()]
        assert token_line, "token shown exactly once"
        assert r.stdout.count(token_line[0].split(":")[0]) == 1

        r = _kb(host_env["dir"], "client", "list")
        assert "test-client" in r.stdout
        assert "kb-admin" in r.stdout

        # claude-code: default name + endpoint from the local base URL
        r = _kb(host_env["dir"], "connect", "claude-code", "--name", "claude-code")
        assert "claude mcp add" in r.stdout
        assert "http://127.0.0.1:8000/mcp" in r.stdout  # local endpoint (no KB_PUBLIC_URL)

        # cursor: valid JSON with real values — extract the printed config
        # block by scanning for the first line that parses as JSON '{...}'.
        r = _kb(host_env["dir"], "connect", "cursor")
        config = None
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("{") and ln.endswith("}"):
                try:
                    config = json.loads(ln)
                    break
                except json.JSONDecodeError:
                    continue
        if config is None:
            # Fallback: the whole block may be pretty-printed; join lines
            # from the first '{' to the last '}' and parse.
            block = r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1]
            config = json.loads(block)
        assert config["mcpServers"]["ecosystem-kb"]["url"] == "http://127.0.0.1:8000/mcp"
        assert config["mcpServers"]["ecosystem-kb"]["headers"]["Authorization"].startswith("Bearer ")

    def test_08_cursor_write_preserves_other_servers(self, host_env):
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")
        target = os.path.join(host_env["dir"], "mcp.json")
        with open(target, "w") as f:
            json.dump({"mcpServers": {"other-tool": {"url": "https://example.com/mcp"}}}, f)
        r = _kb(host_env["dir"], "connect", "cursor", "--write", "--path", target)
        assert "merged" in r.stdout
        merged = json.loads(open(target).read())
        assert merged["mcpServers"]["other-tool"]["url"] == "https://example.com/mcp"
        assert merged["mcpServers"]["ecosystem-kb"]["url"] == "http://127.0.0.1:8000/mcp"

    def test_09_reconnect_requires_consent(self, host_env):
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")
        _kb(host_env["dir"], "connect", "claude-code", "--name", "claude-code")
        r = _kb(host_env["dir"], "connect", "claude-code", "--name", "claude-code", check=False)
        assert r.returncode != 0
        assert "--rotate" in r.stdout + r.stderr
        # explicit rotation works
        r = _kb(host_env["dir"], "connect", "claude-code", "--name", "claude-code", "--rotate")
        assert "rotated" in r.stdout

    def test_09b_disconnect_revokes(self, host_env):
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")
        r = _kb(host_env["dir"], "client", "create", "victim", "--role", "reader")
        token = [ln for ln in r.stdout.splitlines() if ln.startswith("bearer token")][0].split(": ")[1]
        _kb(host_env["dir"], "disconnect", "victim")
        # Revoked token can no longer initialize MCP (§6.9).
        resp = httpx.post("http://127.0.0.1:8000/mcp/", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}})
        assert resp.status_code == 401
        # §6.10: MCP Inspector path — plain initialize with a VALID token.
        r = _kb(host_env["dir"], "client", "create", "inspector", "--role", "reader")
        tok2 = [ln for ln in r.stdout.splitlines() if ln.startswith("bearer token")][0].split(": ")[1]
        resp2 = httpx.post("http://127.0.0.1:8000/mcp/", headers={
            "Authorization": f"Bearer {tok2}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "inspector", "version": "0"}}})
        assert resp2.status_code == 200

    def test_doctor_healthy_local_only(self, host_env):
        """Repair §4: clean local-only install (no tunnel, no LLM key, no
        backup) still exits 0 — warnings, not failures."""
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")
        r = _kb(host_env["dir"], "doctor", check=False)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "WARN" in r.stdout  # tunnel/backup/LLM warnings visible
        assert "local-only" in r.stdout

    def test_doctor_strict_fails_on_warnings(self, host_env):
        env_file = os.path.join(host_env["dir"], ".env")
        _kb(host_env["dir"], "setup")
        with open(env_file, "a") as f:
            f.write(f"KB_ADMIN_TOKEN={host_env['admin_token']}\n")
        r = _kb(host_env["dir"], "doctor", "--strict", check=False)
        assert r.returncode == 1  # no tunnel + no backup + no LLM key
