#!/usr/bin/env bash
# Idempotent installer for the mm-mcp stdio MCP server.
#
# Steps:
#   1. pip install --user the package from this worktree.
#   2. Move the pip-generated console script to ~/.local/bin/mm-mcp-console
#      and write a launcher at ~/.local/bin/mm-mcp that sources
#      ~/.config/mm.env, defaults MM_URL=https://mattermost.example.com, and execs
#      the console script.
#   3. Backup ~/.config/mcp-servers.json once and patch in a "mattermost"
#      entry pointing at the launcher.
#   4. Run ~/.local/bin/sync-hook so every downstream agent
#      config (codex, claude, copilot, ...) picks up the new server.
#
# Re-running this script is safe; existing files are kept in sync.

set -euo pipefail

HOME_DIR="${HOME:?HOME required}"
LOCAL_BIN="$HOME_DIR/.local/bin"
LAUNCHER="$LOCAL_BIN/mm-mcp"
CONSOLE="$LOCAL_BIN/mm-mcp-console"
REGISTRY="$HOME_DIR/.config/mcp-servers.json"
ENV_FILE="$HOME_DIR/.config/mm.env"
SYNC_MCP="$LOCAL_BIN/sync-hook"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg_dir="$(dirname "$script_dir")"

mkdir -p "$LOCAL_BIN" "$HOME_DIR/.config"

pip_args=(install --user --quiet --upgrade "$pkg_dir")
if python3 -m pip help install 2>/dev/null | grep -q -- '--break-system-packages'; then
  pip_args+=(--break-system-packages)
fi
python3 -m pip "${pip_args[@]}"

if [[ -f "$LAUNCHER" ]] && head -n1 "$LAUNCHER" | grep -q '^#!/.*python'; then
  mv "$LAUNCHER" "$CONSOLE"
elif [[ ! -f "$CONSOLE" ]]; then
  printf '%s\n' \
    '#!/usr/bin/env python3' \
    'import sys' \
    'from mm_mcp.server import main' \
    "if __name__ == '__main__':" \
    "    sys.exit(main())" >"$CONSOLE"
fi
chmod +x "$CONSOLE"

cat >"$LAUNCHER" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  printf 'usage: mm-mcp\n\nstdio MCP server for Mattermost at %s\n' "${MM_URL:-https://mattermost.example.com}"
  exit 0
fi

env_file="${MM_MCP_ENV_FILE:-$HOME/.config/mm.env}"
if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

export MM_URL="${MM_URL:-https://mattermost.example.com}"
exec "$HOME/.local/bin/mm-mcp-console" "$@"
LAUNCH
chmod +x "$LAUNCHER"

if [[ -f "$REGISTRY" ]]; then
  cp -p "$REGISTRY" "$REGISTRY.bak-$(date +%s)"
fi

python3 - "$REGISTRY" "$LAUNCHER" "$ENV_FILE" <<'PY'
import json, os, sys
from pathlib import Path

registry, launcher, env_file = (Path(p) for p in sys.argv[1:4])
data = {}
if registry.exists():
    data = json.loads(registry.read_text() or "{}")
data["mattermost"] = {
    "command": str(launcher),
    "args": [],
    "env": {
        "MM_URL": "https://mattermost.example.com",
        "MM_MCP_ENV_FILE": str(env_file),
    },
}
registry.parent.mkdir(parents=True, exist_ok=True)
tmp = registry.with_suffix(".tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
os.chmod(tmp, 0o600)
tmp.rename(registry)
PY

if [[ -x "$SYNC_MCP" ]]; then
  "$SYNC_MCP"
else
  echo "warning: $SYNC_MCP not executable; downstream agents not refreshed" >&2
fi
