# mm-mcp live proof

Static evidence that the installed `mm-mcp` launcher round-trips real
Mattermost REST calls. Captured once during integration from the running
`~/.local/bin/mm-mcp` instance and committed as data.

The capture is filtered to a fixed allowlist of identity fields, and the
identity values themselves are redacted before commit: this repository is
public, so user ids, usernames, emails, real names, and team ids/names are
replaced with `<redacted>` placeholders. Sensitive fields the Mattermost
REST API also returns — `auth_data`, `notify_props`, `last_password_update`,
`timezone`, `props`, `locale`, `nickname`, `position`, picture/welcome
timestamps, etc. — are dropped entirely. The MM_TOKEN is never recorded.

## Source

- Launcher: `~/.local/bin/mm-mcp`
- MM_URL: the private Mattermost instance from `~/.config/mm.env`
- Tool calls (idempotent GETs against the Mattermost REST API):
  - `mm_get_me` → `GET /api/v4/users/me`
  - `mm_list_teams` → `GET /api/v4/users/me/teams`

## mm_get_me

```json
{
  "id": "<redacted 26-char user id>",
  "username": "<redacted>",
  "email": "<redacted>",
  "first_name": "<redacted>",
  "last_name": "<redacted>",
  "roles": "system_user system_user_access_token"
}
```

## mm_list_teams

```json
[
  {"id": "<redacted>", "name": "<redacted>", "display_name": "<redacted>"},
  {"id": "<redacted>", "name": "<redacted>", "display_name": "<redacted>"}
]
```

## unread tools (v0.3.0)

Captured live via `mcp.client.stdio` + `ClientSession` against the installed
`~/.local/bin/mm-mcp` launcher, exercising the four read-only unread
tools end-to-end over the Mattermost REST API:

- `mm_get_unread_summary`  → `GET /users/me/teams/unread` (+ team names)
- `mm_get_unread_threads`  → `GET /users/me/teams/{team_id}/threads?unread=true`
- `mm_get_unread_direct_messages` → channel memberships, type `D`/`G`
- `mm_get_unread_channels` → channel memberships, type `O`/`P`

Counts are a volatile snapshot. Counterpart usernames/full names, channel
display names, and team identities returned by the live calls are excluded;
only aggregate counts and structural booleans are recorded (the tools resolve
those names — proven by `counterpart_username_resolved: true` — but they are
colleagues' data, not committed).

```json
{
  "server_version": "0.3.0",
  "tool_count": 19,
  "unread_tools": [
    "mm_get_unread_summary",
    "mm_get_unread_threads",
    "mm_get_unread_direct_messages",
    "mm_get_unread_channels"
  ],
  "mm_get_unread_summary": {
    "teams": [
      {"team_id": "<redacted>", "team_display_name": "<redacted>", "unread_messages": 758162, "mentions": 77, "unread_threads": 0, "thread_mentions": 0},
      {"team_id": "<redacted>", "team_display_name": "<redacted>", "unread_messages": 860, "mentions": 0, "unread_threads": 0, "thread_mentions": 0}
    ],
    "totals": {"unread_messages": 759022, "mentions": 77, "unread_threads": 0, "thread_mentions": 0}
  },
  "mm_get_unread_direct_messages": {"totals": {"conversations": 4, "unread_messages": 6, "mentions": 3}, "counterpart_username_resolved": true, "sample_kinds": ["direct"]},
  "mm_get_unread_channels_mentions_only": {"totals": {"channels": 49, "unread_messages": 183194, "mentions": 77}, "sample_types": ["private", "public"], "top_mentions": 8},
  "mm_get_unread_threads": {"totals": {"unread_threads": 0, "unread_mentions": 0}, "returned": 0}
}
```
