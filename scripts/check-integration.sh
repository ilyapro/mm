#!/usr/bin/env bash
# Smoke-check the integration produced by scripts/install.sh.
#
# Verifies (in order):
#   - ~/.local/bin/mm-mcp exists, is executable, and --help returns 0.
#   - An MCP initialize JSON-RPC frame piped to the launcher returns a
#     response whose result.instructions contains the configured MM_URL.
#   - ~/.config/mcp-servers.json has a mattermost entry pointing at the
#     launcher.
#   - The sync-hook distributor produced mattermost entries in the codex
#     (~/.codex/config.toml), claude (~/.claude.json), and copilot
#     (~/.copilot/mcp-config.json) configs.
#
# Exit 0 only if every check passes.

set -euo pipefail

LAUNCHER="$HOME/.local/bin/mm-mcp"
ENV_FILE="${MM_MCP_ENV_FILE:-$HOME/.config/mm.env}"

# Resolve MM_URL from the env file in a subshell so MM_TOKEN is not
# exported into this script's environment.
if [[ -z "${MM_URL:-}" && -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  MM_URL="$(source "$ENV_FILE" >/dev/null 2>&1 || true; printf '%s' "${MM_URL:-}")"
fi
: "${MM_URL:?MM_URL must be set in the environment or in $ENV_FILE}"

test -x "$LAUNCHER"
"$LAUNCHER" --help >/dev/null

python3 - "$LAUNCHER" "$MM_URL" <<'PY'
import json, subprocess, sys
launcher, expected_url = sys.argv[1:3]
req = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mm-mcp-check", "version": "0.0.0"},
    },
}) + "\n"
proc = subprocess.Popen(
    [launcher],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
out, err = proc.communicate(req.encode(), timeout=10)
for line in out.decode(errors="replace").splitlines():
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if msg.get("id") != 1:
        continue
    instr = (msg.get("result") or {}).get("instructions") or ""
    if expected_url not in instr:
        sys.exit(f"instructions missing {expected_url}")
    break
else:
    sys.stderr.write(err.decode(errors="replace"))
    sys.exit("no initialize response with id=1")
PY

python3 - "$MM_URL" <<'PY'
import json, sys, tomllib
from pathlib import Path
expected_url = sys.argv[1]
home = Path.home()
registry = json.loads((home / ".config/mcp-servers.json").read_text())
mm = registry.get("mattermost") or {}
assert mm.get("command", "").endswith("/.local/bin/mm-mcp"), mm
assert (mm.get("env") or {}).get("MM_URL") == expected_url, mm
codex = tomllib.loads((home / ".codex/config.toml").read_text())
assert codex.get("mcp_servers", {}).get("mattermost"), "codex missing mattermost"
claude = json.loads((home / ".claude.json").read_text())
assert claude.get("mcpServers", {}).get("mattermost"), "claude missing mattermost"
cop = json.loads((home / ".copilot/mcp-config.json").read_text())
cop_mm = cop.get("servers", {}).get("mattermost") or {}
assert cop_mm, "copilot missing mattermost"
assert cop_mm.get("tools") == ["*"], "copilot mattermost tools != ['*']"
print("OK")
PY
