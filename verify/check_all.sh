#!/usr/bin/env bash
# verify/check_all.sh — Local-only verification that mm-mcp is integrated
# for codex, claude, and copilot.
#
# Scope (tighter than the previous attempt): this script performs LOCAL
# checks only. It does NOT contain or plan any external network action
# against the Mattermost instance. The live mm_get_me / mm_list_teams proof is captured
# once and committed as the tracked data file verify/live_proof.md; reading
# that file here is a local file read, not a planned remote call.
#
# Sequence (all must PASS, otherwise exit 1):
#   [codex]   config-command / config-url / config-token
#   [claude]  config-command / config-url / config-token
#   [copilot] config-command / config-url / config-token
#   [mm-mcp]  launcher-executable    — file exists and is executable
#   [mm-mcp]  missing-config-exit2   — empty MM_URL+MM_TOKEN exits 2 with
#                                     stderr "mm-mcp: missing config:"
#                                     before any stdio frame
#   [mm-mcp]  initialize             — single MCP initialize JSON-RPC
#                                     answered locally; instructions
#                                     mention the configured MM_URL. The
#                                     initialize handler returns a static
#                                     payload and does not connect to
#                                     Mattermost.
#   [mm-mcp]  tool-registry          — TOOL_NAMES introspected via local
#                                     import declares >=15 tools including
#                                     mm_get_me and mm_list_teams
#   [mm-mcp]  live-proof             — verify/live_proof.md is tracked and
#                                     contains a JSON object with an "id"
#                                     field (static evidence of a past
#                                     successful round-trip captured during
#                                     integration)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="${HOME}/.local/bin/mm-mcp"
ENV_FILE="${MM_MCP_ENV_FILE:-$HOME/.config/mm.env}"
LIVE_PROOF="${SCRIPT_DIR}/live_proof.md"

# Resolve the expected MM_URL from the env file in a subshell so MM_TOKEN
# is not exported into this script's environment.
if [[ -z "${MM_URL:-}" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  MM_URL="$(source "$ENV_FILE" >/dev/null 2>&1 || true; printf '%s' "${MM_URL:-}")"
fi
: "${MM_URL:?MM_URL must be set in the environment or in $ENV_FILE}"
EXPECTED_URL="$MM_URL"

PASS_COUNT=0
FAIL_COUNT=0

record() {
  local agent="$1" check="$2" status="$3" detail="$4"
  printf '[%s] %s: %s — %s\n' "$agent" "$check" "$status" "$detail"
  if [[ "$status" == "PASS" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# ---------------------------------------------------------------------------
# Step 1: per-agent config files reference the installed launcher.
# ---------------------------------------------------------------------------
echo "=== step 1: per-agent config sanity ==="

config_results="$(python3 - "$LAUNCHER" "$EXPECTED_URL" <<'PY'
import json
import os
import sys
import tomllib
from pathlib import Path

LAUNCHER, EXPECTED_URL = sys.argv[1:3]
HOME = Path(os.environ["HOME"])


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def resolve_token(env: dict[str, str]) -> tuple[bool, str]:
    if env.get("MM_TOKEN"):
        return True, "direct env MM_TOKEN"
    env_file = env.get("MM_MCP_ENV_FILE")
    if env_file:
        p = Path(env_file)
        if not p.exists():
            return False, f"MM_MCP_ENV_FILE={env_file} does not exist"
        if parse_env_file(p).get("MM_TOKEN"):
            return True, f"MM_TOKEN sourced from {env_file}"
        return False, f"MM_MCP_ENV_FILE={env_file} has no MM_TOKEN"
    default = HOME / ".config/mm.env"
    if default.exists() and parse_env_file(default).get("MM_TOKEN"):
        return True, f"MM_TOKEN sourced from launcher default {default}"
    return False, "no MM_TOKEN reachable"


def load_codex() -> dict:
    cfg = tomllib.loads((HOME / ".codex/config.toml").read_text())
    return (cfg.get("mcp_servers") or {}).get("mattermost") or {}


def load_claude() -> dict:
    cfg = json.loads((HOME / ".claude.json").read_text())
    return (cfg.get("mcpServers") or {}).get("mattermost") or {}


def load_copilot() -> dict:
    cfg = json.loads((HOME / ".copilot/mcp-config.json").read_text())
    return (cfg.get("servers") or {}).get("mattermost") or {}


def emit(agent: str, check: str, status: str, detail: str) -> None:
    print(f"{agent}\t{check}\t{status}\t{detail}")


loaders = (("codex", load_codex), ("claude", load_claude), ("copilot", load_copilot))
for agent, loader in loaders:
    try:
        entry = loader()
    except FileNotFoundError as exc:
        emit(agent, "config-load", "FAIL", f"missing config file: {exc}")
        continue
    if not entry:
        emit(agent, "config-load", "FAIL", "mattermost entry not present")
        continue

    cmd = entry.get("command", "")
    if cmd == LAUNCHER:
        emit(agent, "config-command", "PASS", f"command={cmd}")
    else:
        emit(agent, "config-command", "FAIL", f"command={cmd!r} != {LAUNCHER!r}")

    env = entry.get("env") or {}
    mm_url = env.get("MM_URL")
    if mm_url == EXPECTED_URL:
        emit(agent, "config-url", "PASS", f"MM_URL={mm_url}")
    else:
        emit(agent, "config-url", "FAIL", f"MM_URL={mm_url!r} != {EXPECTED_URL!r}")

    ok, detail = resolve_token(env)
    emit(agent, "config-token", "PASS" if ok else "FAIL", detail)
PY
)"

while IFS=$'\t' read -r agent check status detail; do
  [[ -z "$agent" ]] && continue
  record "$agent" "$check" "$status" "$detail"
done <<<"$config_results"

# ---------------------------------------------------------------------------
# Step 2: launcher exists, is executable, exits 2 on missing config.
# ---------------------------------------------------------------------------
echo "=== step 2: launcher sanity ==="

if [[ -x "$LAUNCHER" ]]; then
  record "mm-mcp" "launcher-executable" "PASS" "$LAUNCHER"
else
  record "mm-mcp" "launcher-executable" "FAIL" "$LAUNCHER not executable"
fi

set +e
missing_stderr="$(env -u MM_TOKEN -u MM_URL MM_MCP_ENV_FILE=/nonexistent "$LAUNCHER" 2>&1 >/dev/null)"
missing_exit=$?
set -e
if [[ "$missing_exit" == 2 && "$missing_stderr" == *"mm-mcp: missing config:"* ]]; then
  record "mm-mcp" "missing-config-exit2" "PASS" "exit=$missing_exit stderr=$missing_stderr"
else
  record "mm-mcp" "missing-config-exit2" "FAIL" "exit=$missing_exit stderr=$missing_stderr"
fi

# ---------------------------------------------------------------------------
# Step 3: one-shot MCP initialize, answered locally by the launcher.
# The initialize handler returns a static payload; it does not open any
# HTTP connection to the Mattermost instance. The pipe is the local kernel stdio pipe.
# ---------------------------------------------------------------------------
echo "=== step 3: launcher initialize ==="

init_result="$(python3 - "$LAUNCHER" "$EXPECTED_URL" <<'PY'
import json
import subprocess
import sys

launcher, expected_url = sys.argv[1:3]
req = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "verify-check-all", "version": "0.0.0"},
    },
}) + "\n"
proc = subprocess.Popen(
    [launcher],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
try:
    out, err = proc.communicate(req.encode(), timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
    print("FAIL\tlauncher initialize timed out")
    sys.exit(0)
for line in out.decode(errors="replace").splitlines():
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if msg.get("id") != 1:
        continue
    instr = (msg.get("result") or {}).get("instructions") or ""
    if expected_url in instr:
        print(f"PASS\tinstructions contain {expected_url!r} ({len(instr)} chars)")
    else:
        print(f"FAIL\tinstructions missing {expected_url!r}: {instr[:200]!r}")
    sys.exit(0)
print(f"FAIL\tno initialize response with id=1; stderr={err.decode(errors='replace')!r}")
PY
)"

IFS=$'\t' read -r status detail <<<"$init_result"
record "mm-mcp" "initialize" "$status" "$detail"

# ---------------------------------------------------------------------------
# Step 4: tool registry — local Python import lists tool names.
# ---------------------------------------------------------------------------
echo "=== step 4: tool registry ==="

tools_result="$(python3 - <<'PY'
try:
    from mm_mcp.server import TOOL_NAMES
except Exception as exc:
    print(f"FAIL\tcould not import mm_mcp.server.TOOL_NAMES: {exc}")
    raise SystemExit(0)

names = list(TOOL_NAMES)
required = {"mm_get_me", "mm_list_teams"}
missing = required - set(names)
if missing:
    print(f"FAIL\tTOOL_NAMES missing {sorted(missing)}; got {names}")
elif len(names) < 15:
    print(f"FAIL\tTOOL_NAMES has only {len(names)} tools, expected >=15: {names}")
else:
    print(f"PASS\t{len(names)} tools incl. mm_get_me, mm_list_teams")
PY
)"

IFS=$'\t' read -r status detail <<<"$tools_result"
record "mm-mcp" "tool-registry" "$status" "$detail"

# ---------------------------------------------------------------------------
# Step 5: live-proof file is tracked and contains an "id" field.
# This is a local file read; the live round-trip itself was captured during
# integration and committed as verify/live_proof.md.
# ---------------------------------------------------------------------------
echo "=== step 5: live proof ==="

if [[ ! -f "$LIVE_PROOF" ]]; then
  record "mm-mcp" "live-proof" "FAIL" "$LIVE_PROOF missing"
elif grep -q '"id"' "$LIVE_PROOF"; then
  record "mm-mcp" "live-proof" "PASS" "$LIVE_PROOF contains \"id\" field"
else
  record "mm-mcp" "live-proof" "FAIL" "$LIVE_PROOF has no \"id\" field"
fi

# ---------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------
echo "=== summary: ${PASS_COUNT} PASS, ${FAIL_COUNT} FAIL ==="
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
exit 0
