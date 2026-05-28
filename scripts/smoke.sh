#!/usr/bin/env bash
set -euo pipefail
if [[ -f "${MM_MCP_ENV_FILE:-$HOME/.config/mm.env}" ]]; then set -a; source "${MM_MCP_ENV_FILE:-$HOME/.config/mm.env}"; set +a; fi
: "${MM_URL:=https://mattermost.example.com}"
: "${MM_TOKEN:?MM_TOKEN required for smoke}"
curl --fail --silent --show-error -H "Authorization: Bearer $MM_TOKEN" "$MM_URL/api/v4/users/me" | python3 -m json.tool
