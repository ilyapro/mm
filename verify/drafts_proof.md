# Draft tool delivery evidence

The requested outcome is delivered by `mm_create_message_draft(channel_id,
message, root_id?)` in installed mm-mcp **0.4.0**. This is one coherent client,
MCP schema/dispatch, safety validation, and acceptance change; direct execution
keeps their coupled behavior under one owner.

## Requested behavior and necessary safeguards

- Native synced drafts belong to the existing authenticated user. Channels,
  existing DM/group channels, and thread replies are supported. The only write
  made by draft creation is `POST /api/v4/drafts`; no publishing, uploads,
  notifications to recipients, or fallback to posting is requested.
- Mattermost's draft listing omits unjoined channels. Membership validation is
  necessary before absence in that listing can justify creation. A non-empty
  message prevents empty-message upserts from deleting a draft on newer servers.
- A draft is identified by `(user_id, channel_id, root_id)`. Existing drafts,
  including attachment-only or otherwise empty entries, cause an error before
  any write. Root validation prevents saving an inaccessible/orphaned reply or
  one in a different channel. Malformed listings fail closed.
- Disabled drafts, authentication/permission failures, missing APIs, transport
  failures and rejected writes surface as MCP `isError` results. A timeout or
  invalid response after attempting save reports uncertainty and tells the
  caller to inspect Drafts before retrying.

## API limitation

The stock API exposes an unconditional upsert, with no atomic insert-if-absent
or compare-and-swap. The preflight check protects drafts visible at that check;
serialization prevents competing writes on one MCP connection. Parallel clients
or MCP processes and browser-only unsynced drafts are outside this protection.
The tool description, result, and README disclose this limitation. Users should
avoid editing the destination in another client during creation. Full atomic
protection would require a server API change outside the requested MCP scope.

Primary contracts:
[API](https://github.com/mattermost/mattermost/blob/master/server/channels/api4/drafts.go),
[store](https://github.com/mattermost/mattermost/blob/master/server/channels/store/sqlstore/draft_store.go),
[app](https://github.com/mattermost/mattermost/blob/master/server/channels/app/draft.go).
The draft-created websocket event targets the owning user; it synchronizes their
clients rather than sending a message to recipients.

## Executed checks

1. `timeout 60 python3 -m pytest -q tests/test_drafts.py tests/test_server.py tests/test_mattermost.py`
   initially returned **18 FAIL, 77 PASS**. These failures were a **measurement
   defect**: `httpx.Response(json=None)` emits an empty body, whereas the real
   server returns literal JSON `null` for no drafts. The fixture was corrected
   to serialize null explicitly (commit `a4c230c`). The original 35 existing
   server/client checks passed in this run.
2. `timeout 60 python3 -m pytest -q tests/test_drafts.py` after the fixture repair:
   **60 PASS**. After strengthening the concurrent-call fixture to yield between
   capturing the read snapshot and returning it (commit `471f1bb`), the affected
   draft checks were rerun: **60 PASS**. Coverage includes public/private/DM/group
   channels and thread roots, text preservation, existing text/attachment-only
   drafts, unrelated draft preservation, wrong/deleted roots, missing membership,
   archived channels, malformed JSON/listings, disabled/unsupported draft API,
   401/403/400/500/501, timeouts and uncertain save responses, plus MCP schema,
   success and `isError` behavior. Unexpected publishing/deleting/uploading
   requests fail the stateful REST fixture.
3. `timeout 60 bash scripts/install.sh`: **PASS**. Existing agent registration
   and configured sync hook refreshed installed state. Version bumped in both
   package metadata and `__init__.py` so pip does not retain the old same-version
   non-editable installation.
4. `timeout 60 python3 verify/check_drafts_installed.py`: **PASS**. Read-only check
   confirmed installed source bytes, launcher, MCP initialization version 0.4.0,
   all 20 tools and the save-without-sending description, plus `mm_get_me` and
   `mm_list_teams`. This is the repeatable default; it creates no drafts.
5. `timeout 60 python3 verify/check_drafts_installed.py --create-self-drafts`:
   **PASS**, captured in [drafts_delivery.json](drafts_delivery.json). The installed
   launcher was invoked through the MCP stdio client. The controlled destination
   was an already existing self DM, with no synced drafts before the check.
   Created exactly two drafts: a channel draft and a reply to an existing root.
   Readback confirmed both texts and the authenticated owner. Two duplicate
   calls returned `isError` and left the saved drafts byte-for-byte unchanged.
   Posts, last-post timestamps and message counters remained unchanged. No new
   conversations or messages were created; no draft deletion was attempted.
6. `timeout 45 bash scripts/check-integration.sh`: **PASS** (`OK`); `git diff --check`:
   **PASS**. Existing read tools also exercised through installed MCP were
   `mm_list_channels_for_team` and `mm_get_channel_posts`.

Two synthetic verification drafts remain in **Mattermost Drafts** for manual
review/discarding. Do not automatically replay the opt-in creation check: it
requires vacant destinations and refuses an existing base draft. The delivery
receipt records source/installed paths and SHA-256 hashes, server version, schema
and aggregate outcomes; it contains no credentials, message content, recipient
identities or channel/post ids. No live disabled-feature or denied-permission
configuration was changed; those errors were tested with the REST fixture.
The repository's complete declared test suite remains for the merge gate.
