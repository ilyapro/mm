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
`~/.config/mcp-servers.json`, and optional propagation to downstream agent
configs via a user-provided `MM_MCP_SYNC_HOOK` executable):

```bash
bash scripts/install.sh
bash scripts/check-integration.sh
```

`install.sh` is idempotent. `check-integration.sh` exits 0 only if the
launcher responds to an MCP `initialize` JSON-RPC frame with instructions
that contain the configured `MM_URL` and the mattermost entry has been
propagated to the codex, claude, and copilot configs.

## Environment

Required:

- `MM_URL`, for example `https://mattermost.example.com`
- `MM_TOKEN`, a Mattermost personal access token

Optional:

- `MM_MCP_ENV_FILE`, a shell-style env file parsed by the Python entry point only when required config is missing.
- `MM_MCP_SYNC_HOOK`, an executable that `install.sh` runs after updating the registry to propagate `~/.config/mcp-servers.json` to downstream agent configs; set it in the environment or in `~/.config/mm.env`.

## Tools

`mm_get_me`, `mm_search_users`, `mm_list_teams`, `mm_get_team_by_name`, `mm_list_channels_for_team`, `mm_get_channel`, `mm_get_channel_by_name`, `mm_create_direct_channel`, `mm_post_message`, `mm_update_post`, `mm_delete_post`, `mm_get_channel_posts`, `mm_get_thread`, `mm_search_posts`, `mm_add_reaction`.

### Attachments

`mm_post_message` accepts an optional `file_paths` array of local file paths. For each path the server reads the bytes, uploads the file to the target channel via `POST /api/v4/files`, and attaches the resulting file ids to the post. Paths are resolved on the host where the server process runs (the stdio server is co-located with the agent), so no base64/URL round-trip is needed.

## Smoke

```bash
MM_MCP_ENV_FILE=$HOME/.config/mm.env bash scripts/smoke.sh
```
