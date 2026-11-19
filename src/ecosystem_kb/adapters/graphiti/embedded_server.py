"""Embedded FalkorDB server for tests and local dev (ADR-004).

falkordblite ships redis-server + falkordb.so. Its own redislite client
double-loads the module and fails; we start the server ourselves with the
module loaded exactly once, and connect with falkordb.asyncio (the client
FalkorDriver's `await graph.query()` requires).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REDIS_SERVER_CANDIDATES = [
    Path.home() / ".venv/bin/redis-server",
]

_MODULE_CANDIDATES = [
    Path.home() / ".venv/lib/python3.12/site-packages/redislite/bin/falkordb.so",
]


def _redislite_bin() -> Path | None:
    """Locate the redislite package directory robustly: import beats
    hardcoded venv paths (GitHub runners use /opt/hostedtoolcache, uv uses
    ~/.local/share/uv, etc.)."""
    try:
        import redislite

        return Path(redislite.__file__).resolve().parent / "bin"
    except Exception:
        return None


def _find_redis_server() -> str:
    for cand in _REDIS_SERVER_CANDIDATES:
        if cand and Path(cand).exists():
            return str(cand)
    rl_bin = _redislite_bin()
    if rl_bin is not None and (rl_bin / "redis-server").exists():
        return str(rl_bin / "redis-server")
    found = shutil.which("redis-server")
    if found:
        return found
    raise RuntimeError(
        "redis-server not found; install the 'embedded' extra (pip install "
        "'ecosystem-kb[embedded]') or point tests at a live FalkorDB"
    )


def _find_falkor_module() -> str:
    for cand in _MODULE_CANDIDATES:
        if cand.exists():
            return str(cand)
    rl_bin = _redislite_bin()
    if rl_bin is not None and (rl_bin / "falkordb.so").exists():
        return str(rl_bin / "falkordb.so")
    # fall back to searching sys.path site-packages
    for entry in sys.path:
        cand = Path(entry) / "redislite" / "bin" / "falkordb.so"
        if cand.exists():
            return str(cand)
    raise RuntimeError("falkordb.so not found; install falkordblite")


class EmbeddedFalkorDB:
    """Context manager: an isolated FalkorDB server on a random port."""

    def __init__(self, port: int | None = None) -> None:
        self.port = port or _free_port()
        self.workdir = tempfile.mkdtemp(prefix="ekb_falkor_")
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> EmbeddedFalkorDB:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [
                _find_redis_server(),
                "--loadmodule", _find_falkor_module(),
                "--port", str(self.port),
                "--dir", self.workdir,
                "--daemonize", "no",
                "--save", "",
                "--logfile", f"{self.workdir}/redis.log",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import redis

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if redis.Redis(
                    host="127.0.0.1", port=self.port, socket_connect_timeout=0.2
                ).ping():
                    return
            except Exception:
                time.sleep(0.05)
        log = Path(self.workdir) / "redis.log"
        detail = log.read_text()[-2000:] if log.exists() else "(no log)"
        raise RuntimeError(f"embedded FalkorDB failed to start on {self.port}\n{detail}")

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        shutil.rmtree(self.workdir, ignore_errors=True)


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
