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

`mm_get_me`, `mm_search_users`, `mm_list_teams`, `mm_get_team_by_name`, `mm_list_channels_for_team`, `mm_get_channel`, `mm_get_channel_by_name`, `mm_create_direct_channel`, `mm_post_message`, `mm_create_message_draft`, `mm_update_post`, `mm_delete_post`, `mm_get_channel_posts`, `mm_get_thread`, `mm_search_posts`, `mm_add_reaction`, `mm_get_unread_summary`, `mm_get_unread_threads`, `mm_get_unread_direct_messages`, `mm_get_unread_channels`.

### Drafts (save without sending)

`mm_create_message_draft(channel_id, message, root_id?)` saves text in Mattermost's
native synced **Drafts** for the user authenticated by the existing `MM_TOKEN`.
It never publishes a post or notifies recipients. Open **Drafts** in Mattermost,
review the text, and send it yourself.

Use a joined public/private channel id or an existing direct/group-message
channel id (resolve it with the existing channel tools). For a thread reply,
also pass the root post id in that same channel. Omit `root_id` or use `""` for a
channel/DM draft. The result includes `status: "saved"`, `sent: false`, the native
draft, and review guidance. Message text, including formatting, is preserved.

Creation refuses an existing synced draft for the exact `(channel_id, root_id)`
destination, even if it has only attachments. It also rejects empty text,
archived/unjoined channels, and invalid or unrelated thread roots. Disabled
synced drafts (`AllowSyncedDrafts`), permission errors, unsupported APIs and
transport failures return MCP errors; there is no fallback to posting. On an
uncertain save result, inspect **Drafts** before retrying. DM draft safety checks
require at least one joined team because Mattermost lists teamless DM drafts
through a team endpoint.

**Concurrency limit:** Mattermost exposes an upsert, without an atomic
create-only operation. This tool checks synced drafts immediately before saving
and serializes calls on one MCP connection. Other MCP processes or clients can
still race that check; browser-only unsynced drafts cannot be inspected. Avoid
editing the same destination in another client during creation. This limitation
is also stated in the tool description and its result.

API contracts: [draft routes](https://github.com/mattermost/mattermost/blob/master/server/channels/api4/drafts.go),
[draft persistence](https://github.com/mattermost/mattermost/blob/master/server/channels/store/sqlstore/draft_store.go).

### Attachments

`mm_post_message` accepts an optional `file_paths` array of local file paths. For each path the server reads the bytes, uploads the file to the target channel via `POST /api/v4/files`, and attaches the resulting file ids to the post. Paths are resolved on the host where the server process runs (the stdio server is co-located with the agent), so no base64/URL round-trip is needed.

## Smoke

```bash
MM_MCP_ENV_FILE=$HOME/.config/mm.env bash scripts/smoke.sh
```
