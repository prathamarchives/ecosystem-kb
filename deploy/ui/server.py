"""Tiny static server for the ecosystem-kb UI, with SPA fallback and config.js."""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path == "/config.js":
            body = json.dumps(
                {
                    "url": os.environ.get("SUPABASE_URL", ""),
                    "anon": os.environ.get("SUPABASE_ANON_KEY", ""),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.path.split("?", 1)[0]
        target = ROOT / path.lstrip("/")
        if not target.exists() or target.is_dir():
            self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 3000), Handler).serve_forever()
