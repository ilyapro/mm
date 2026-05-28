# mm-mcp

Python stdio MCP server for Mattermost over the Mattermost REST API.

## Install

```bash
pip install .
```

Run with:

```bash
mm-mcp
```

or:

```bash
python -m mm_mcp
```

## System install + agent registration

For a full system install (launcher in `~/.local/bin`, registration in
`~/.config/mcp-servers.json`, and distribution to every agent via
`~/.local/bin/sync-hook`):

```bash
bash scripts/install.sh
bash scripts/check-integration.sh
```

`install.sh` is idempotent. `check-integration.sh` exits 0 only if the
launcher responds to an MCP `initialize` JSON-RPC frame with instructions
that contain `https://mattermost.example.com` and the mattermost entry has been
propagated to the codex, claude, and copilot configs.

## Environment

Required:

- `MM_URL`, for example `https://mattermost.example.com`
- `MM_TOKEN`, a Mattermost personal access token

Optional:

- `MM_MCP_ENV_FILE`, a shell-style env file parsed by the Python entry point only when required config is missing.

## Tools

`mm_get_me`, `mm_search_users`, `mm_list_teams`, `mm_get_team_by_name`, `mm_list_channels_for_team`, `mm_get_channel`, `mm_get_channel_by_name`, `mm_create_direct_channel`, `mm_post_message`, `mm_update_post`, `mm_delete_post`, `mm_get_channel_posts`, `mm_get_thread`, `mm_search_posts`, `mm_add_reaction`.

## Smoke

```bash
MM_MCP_ENV_FILE=$HOME/.config/mm.env bash scripts/smoke.sh
```
