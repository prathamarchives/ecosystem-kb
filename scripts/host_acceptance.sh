#!/usr/bin/env bash
# ecosystem-kb — owner-host acceptance procedure (onboarding repair §6).
#
# Runs the Docker Compose-level checks that cannot run in the CI sandbox
# (no Docker daemon there). The Python-level equivalents (CLI flows, admin
# API, MCP protocol) run in CI via tests/integration/test_host_cli.py and
# tests/contract/test_admin_api.py; this script executes the REAL stack.
#
# Usage: from the deployment checkout (the directory holding deploy/ and
# .env), after `pip install -e .` (or the venv is active):
#   bash scripts/host_acceptance.sh
#
# Exits non-zero on the first failed check. Requires: docker, curl, python.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PASS=0; FAIL=0

say()  { printf '\n== %s ==\n' "$*"; }
ok()   { printf '[PASS] %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '[FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }
try()  { # try <label> <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$label"; else bad "$label"; fi
}

PORT="${KB_BIND_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"

say "0. prerequisites"
[ -f .env ] || { echo "no .env in $(pwd) — run 'kb setup' first"; exit 1; }
try "docker engine present" docker version
try "kb CLI present" kb --help

say "1. kb setup (§6.1)"
kb setup
[ -f .env ] && ok ".env exists" || bad ".env missing"
PERM="$(stat -c %a .env)"
[ "$PERM" = "600" ] && ok ".env mode 600" || bad ".env mode is $PERM (want 600)"

say "2. kb up (§6.2)"
kb up
try "postgres healthy" docker compose -f deploy/compose.local.yaml ps --format json sh -c true

say "3. kb status (§6.3)"
kb status
try "API /health on loopback" curl -fsS "${BASE}/health"

say "4. kb doctor (§6.4) — local-only must be healthy"
if kb doctor; then ok "doctor exit 0 (clean local install)"; else bad "doctor failed on clean install"; fi

say "5. kb client create test-client (§6.5)"
kb client create test-client --role contributor | tee /tmp/kb_client_out.txt
grep -q "bearer token" /tmp/kb_client_out.txt && ok "token shown" || bad "no token shown"
CLIENT_TOKEN="$(grep 'bearer token' /tmp/kb_client_out.txt | sed 's/.*: //')"
[ -n "$CLIENT_TOKEN" ] && ok "token captured" || bad "token empty"

say "6-7. kb connect claude-code + cursor (§6.6-7)"
kb connect claude-code | tee /tmp/kb_cc.txt
grep -q "claude mcp add" /tmp/kb_cc.txt && ok "claude-code snippet" || bad "no claude-code snippet"
kb connect cursor --write --path /tmp/kb_cursor_mcp.json | tee /tmp/kb_cur.txt

say "8. parse generated Cursor JSON (§6.8)"
if python3 - <<PYEOF
import json, sys
cfg = json.load(open("/tmp/kb_cursor_mcp.json"))
srv = cfg["mcpServers"]["ecosystem-kb"]
assert srv["url"].startswith("http"), srv["url"]
assert srv["headers"]["Authorization"].startswith("Bearer "), "missing bearer header"
print("cursor config valid:", srv["url"])
PYEOF
then ok "Cursor JSON valid with real endpoint+token"; else bad "Cursor JSON invalid"; fi

say "9. kb disconnect + revocation (§6.9)"
kb disconnect claude-code || true   # the client connect created
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/mcp/" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer ${CLIENT_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}')"
[ "$CODE" = "401" ] && ok "revoked token rejected (401)" || bad "revoked token got HTTP $CODE (want 401)"

say "10. MCP Inspector reachability (§6.10)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/mcp/" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer ${CLIENT_TOKEN}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"inspector","version":"0"}}}')"
[ "$CODE" = "401" ] && ok "endpoint reachable and auth-gated (401 without valid token)" || bad "got HTTP $CODE"

say "11. tunnel route intact (§6.11 — needs CF_TUNNEL_TOKEN in .env)"
if grep -q '^CF_TUNNEL_TOKEN=' .env; then
  docker compose -f deploy/compose.local.yaml --profile tunnel up -d
  PUBLIC="$(grep '^KB_PUBLIC_URL=' .env | cut -d= -f2)"
  try "public /health via tunnel" curl -fsS "${PUBLIC}/health"
else
  echo "[SKIP] no CF_TUNNEL_TOKEN — local-only mode (add it per docs/TUNNEL-SETUP.md and re-run)"
fi

say "12. database privacy (§6.12)"
PG_BAD=0; FK_BAD=0
# Loopback 5432/6379 must refuse (nothing published), non-loopback must refuse.
if (echo > /dev/tcp/127.0.0.1/5432) 2>/dev/null; then PG_BAD=1; fi
if (echo > /dev/tcp/127.0.0.1/6379) 2>/dev/null; then FK_BAD=1; fi
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "${HOST_IP:-}" ]; then
  if (echo > /dev/tcp/${HOST_IP}/5432) 2>/dev/null; then PG_BAD=1; fi
  if (echo > /dev/tcp/${HOST_IP}/6379) 2>/dev/null; then FK_BAD=1; fi
fi
[ "$PG_BAD" = "0" ] && ok "PostgreSQL unreachable on host ports" || bad "PostgreSQL REACHABLE on a host port"
[ "$FK_BAD" = "0" ] && ok "FalkorDB unreachable on host ports" || bad "FalkorDB REACHABLE on a host port"
API_BIND="$(docker compose -f deploy/compose.local.yaml port api 8000 2>/dev/null || true)"
case "$API_BIND" in
  127.0.0.1:*|"[::1]:*"|"") ok "API bound to loopback only (${API_BIND:-not published})" ;;
  *) bad "API published on non-loopback: $API_BIND" ;;
esac

say "13. restart persistence (§6.13)"
kb client create persist-check --role reader >/dev/null
kb restart
try "API healthy after restart" curl -fsS "${BASE}/health"
if kb client list | grep -q persist-check; then ok "clients survive restart"; else bad "clients LOST after restart"; fi
if kb status | grep -q "Knowledge"; then ok "knowledge status readable"; else bad "knowledge status unreadable"; fi
kb client revoke persist-check >/dev/null 2>&1 || true

say "summary"
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ] || exit 1
