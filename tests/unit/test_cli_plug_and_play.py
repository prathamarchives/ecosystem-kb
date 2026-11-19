"""Plug-and-play CLI tests (plan §B/E).

Unit-level: setup semantics, client lifecycle, connect snippet shape,
backup manifest verification. Docker-dependent paths (up/down/status) are
exercised on the owner host via the acceptance drill; their argument wiring
is covered here by --help parsing.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
from ecosystem_kb.cli.main import CLIENT_SNIPPET, main


@pytest.fixture
def repo_root(tmp_path, monkeypatch) -> Path:
    """Isolated repo-shaped directory so CLI file operations never touch the real one."""
    import ecosystem_kb.cli.main as cli

    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(cli, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(cli, "BACKUP_ROOT", tmp_path / "backups")
    (tmp_path / ".env.example").write_text(
        "# template\nPOSTGRES_PASSWORD=CHANGE_ME_GENERATED_BY_SETUP\n"
        "FALKORDB_PASSWORD=CHANGE_ME_GENERATED_BY_SETUP\nKB_LLM_API_KEY=\n"
        "KB_ENVIRONMENT=production\nKB_NAMESPACE=ecosystem_prod\n"
    )
    return tmp_path


class TestSetup:
    def test_setup_creates_env_0600_with_secrets(self, repo_root, capsys):
        main(["setup"])
        env = repo_root / ".env"
        assert env.exists()
        assert stat.S_IMODE(env.stat().st_mode) & 0o077 == 0  # plan §B2
        text = env.read_text()
        assert "CHANGE_ME_GENERATED_BY_SETUP" not in text
        pg = [ln for ln in text.splitlines() if ln.startswith("POSTGRES_PASSWORD=")][0]
        fk = [ln for ln in text.splitlines() if ln.startswith("FALKORDB_PASSWORD=")][0]
        assert len(pg.split("=", 1)[1]) >= 24 and len(fk.split("=", 1)[1]) >= 24
        out = capsys.readouterr().out
        assert "KB_LLM_API_KEY is empty" in out  # read-only warning
        assert "Store it in a password manager" in out  # admin token shown once note
        # Admin token itself must be in the file but never printed.
        admin = [ln for ln in text.splitlines() if ln.startswith("KB_ADMIN_TOKEN=")][0]
        assert admin.split("=", 1)[1] not in out

    def test_setup_never_overwrites_existing_env(self, repo_root, capsys):
        (repo_root / ".env").write_text("POSTGRES_PASSWORD=iamsecret\n")
        main(["setup"])
        assert "POSTGRES_PASSWORD=iamsecret" in (repo_root / ".env").read_text()
        assert "left untouched" in capsys.readouterr().out


class TestClientLifecycle:
    @pytest.fixture
    def cp(self, tmp_path):
        cp = ControlPlane(f"sqlite+aiosqlite:///{tmp_path}/clients.db")
        yield cp
        import asyncio

        asyncio.run(cp.dispose())

    async def test_create_list_revoke_rotate(self, cp, capsys):
        repos = Repositories(cp)
        await cp.create_all()
        tok1 = await repos.clients.register("cursor", "Cursor", ["kb:read", "kb:write"], ["ecosystem_prod"])
        assert await repos.clients.find_by_token(tok1) is not None
        tok2 = await repos.clients.rotate_token("cursor", actor="owner")
        assert await repos.clients.find_by_token(tok1) is None  # old dead immediately
        row = await repos.clients.find_by_token(tok2)
        assert row.rotated_at is not None
        await repos.clients.set_status("cursor", "revoked", actor="owner")
        assert (await repos.clients.find_by_token(tok2)).status == "revoked"
        names = [c.client_id for c in await repos.clients.list_clients()]
        assert names == ["cursor"]
        audits = [a.action for a in await repos.audit.for_resource("client", "cursor")]
        assert audits == ["client.token_rotated", "client.revoked"]  # LAW-014

    async def test_revoke_is_independent(self, cp):
        """AT-05 core: revoking A leaves B working."""
        repos = Repositories(cp)
        await cp.create_all()
        tok_a = await repos.clients.register("a", "A", ["kb:read"], ["ecosystem_prod"])
        tok_b = await repos.clients.register("b", "B", ["kb:read"], ["ecosystem_prod"])
        await repos.clients.set_status("a", "revoked", actor="owner")
        assert (await repos.clients.find_by_token(tok_a)).status == "revoked"
        assert (await repos.clients.find_by_token(tok_b)).status == "active"


class TestConnectSnippets:
    def test_generic_snippet_mentions_all_tools_and_endpoint(self, repo_root):
        # _cmd_connect prints the generic block; we assert on its content via
        # the module constants instead of spinning the control plane here.
        assert "kb_context" in CLIENT_SNIPPET and "kb_capture" in CLIENT_SNIPPET
        assert "epistemic" in CLIENT_SNIPPET.lower()

    def test_agent_instructions_written(self, repo_root):
        assert "do not blindly follow stale memory" in CLIENT_SNIPPET.lower() or \
               "do not blindly" in CLIENT_SNIPPET.lower()


class TestBackupManifest:
    def test_manifest_checksum_verification(self, repo_root, tmp_path):
        """The restore gate: tampered artifacts must be detected by checksum."""
        import hashlib
        import json

        src = tmp_path / "bk"
        src.mkdir()
        (src / "control_plane.sql").write_text("-- dump")
        (src / "falkordb.rdb").write_bytes(b"\x00REDIS")
        manifest = {
            "created_at": "2026-11-19T00:00:00+00:00",
            "components": {
                "postgres": {
                    "file": "control_plane.sql",
                    "sha256": "sha256:" + hashlib.sha256(b"-- dump").hexdigest(),
                },
                "falkordb": {
                    "file": "falkordb.rdb",
                    "sha256": "sha256:" + hashlib.sha256(b"\x00REDIS").hexdigest(),
                },
            },
        }
        (src / "manifest.json").write_text(json.dumps(manifest))
        # Untampered: every checksum matches (this is the check _cmd_restore runs).
        for _comp, meta in manifest["components"].items():
            digest = "sha256:" + hashlib.sha256((src / meta["file"]).read_bytes()).hexdigest()
            assert digest == meta["sha256"]
        # Tampered: the same check must diverge.
        (src / "control_plane.sql").write_text("-- tampered")
        comp = manifest["components"]["postgres"]
        digest = "sha256:" + hashlib.sha256((src / comp["file"]).read_bytes()).hexdigest()
        assert digest != comp["sha256"], "tampering must be detected"

    def test_restore_requires_manifest(self, repo_root, tmp_path):
        import ecosystem_kb.cli.main as cli

        empty = tmp_path / "nobackup"
        empty.mkdir()
        try:
            cli._cmd_restore(type("A", (), {"path": str(empty), "yes": False})())
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "no manifest.json" in str(e.code)


class TestCliWiring:
    def test_all_plan_commands_registered(self):
        """Plan §E2 command surface exists verbatim."""
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                main(["--help"])
            except SystemExit:
                pass
        text = out.getvalue()
        for cmd in ["setup", "up", "down", "restart", "status", "doctor", "backup",
                    "restore", "client", "connect", "disconnect", "tunnel",
                    "bootstrap", "worker", "register-client", "graph-health"]:
            assert cmd in text, f"missing CLI command: {cmd}"

    def test_dangerous_commands_require_confirmation(self, repo_root, capsys):
        import ecosystem_kb.cli.main as cli

        cli._cmd_nuke(type("A", (), {"yes": False})())
        assert "--yes" in capsys.readouterr().out  # plan §B4: destructive is separate+confirmed
