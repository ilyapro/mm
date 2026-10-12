from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import __version__
from .config import ConfigError, load_config
from .mattermost import Mattermost


INSTRUCTIONS_TEMPLATE = """Mattermost MCP for {url} — open this URL in the browser to see the same workspace this server talks to over REST.
Use mm_get_me first to identify the authenticated user, then list teams and channels before posting.
Tool results are returned as JSON text blocks from the Mattermost REST API.
mm_post_message accepts an optional file_paths array of local file paths; the server reads each file, uploads it to the channel, and attaches it to the post.
To answer "what is unread for me?" use the unread tools: mm_get_unread_summary for per-team counters, mm_get_unread_threads for unread threads with root-post snippets, mm_get_unread_direct_messages for unread DMs and group messages (with the counterpart's name), and mm_get_unread_channels for public/private channels with unread messages or mentions. These return compact summaries — counts plus channel/counterpart context — not raw REST payloads, and never mutate state."""
# No tool roster here on purpose: the client already has every name and schema
# from tools/list, and a second copy only drifts. Per-tool facts belong in that
# tool's description — the one channel every client delivers (measured: GigaCode
# 26.7.33 reads descriptions verbatim and drops instructions entirely). These
# instructions carry only what no single tool owns.


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOL_DEFINITIONS: tuple[types.Tool, ...] = (
    types.Tool(
        name="mm_get_me",
        description="Return the authenticated Mattermost user.",
        inputSchema=_schema({}),
    ),
    types.Tool(
        name="mm_search_users",
        description="Search Mattermost users by term, optionally scoped to a team.",
        inputSchema=_schema(
            {
                "term": {"type": "string", "description": "Search term."},
                "team_id": {"type": "string", "description": "Optional Mattermost team id."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum users to return.",
                    "default": 20,
                    "minimum": 1,
                },
            },
            ["term"],
        ),
    ),
    types.Tool(
        name="mm_list_teams",
        description="List teams visible to the authenticated user.",
        inputSchema=_schema({}),
    ),
    types.Tool(
        name="mm_get_team_by_name",
        description="Get a Mattermost team by its URL/name slug.",
        inputSchema=_schema({"name": {"type": "string", "description": "Team name slug."}}, ["name"]),
    ),
    types.Tool(
        name="mm_list_channels_for_team",
        description="List the authenticated user's channels for a team, or search channels by query.",
        inputSchema=_schema(
            {
                "team_id": {"type": "string", "description": "Mattermost team id."},
                "query": {"type": "string", "description": "Optional channel search term."},
            },
            ["team_id"],
        ),
    ),
    types.Tool(
        name="mm_get_channel",
        description="Get a channel by id.",
        inputSchema=_schema(
            {"channel_id": {"type": "string", "description": "Mattermost channel id."}},
            ["channel_id"],
        ),
    ),
    types.Tool(
        name="mm_get_channel_by_name",
        description="Get a channel by team id and channel name.",
        inputSchema=_schema(
            {
                "team_id": {"type": "string", "description": "Mattermost team id."},
                "name": {"type": "string", "description": "Channel name."},
            },
            ["team_id", "name"],
        ),
    ),
    types.Tool(
        name="mm_create_direct_channel",
        description="Create or return a direct-message channel for two users.",
        inputSchema=_schema(
            {
                "user_id_a": {"type": "string", "description": "First Mattermost user id."},
                "user_id_b": {"type": "string", "description": "Second Mattermost user id."},
            },
            ["user_id_a", "user_id_b"],
        ),
    ),
    types.Tool(
        name="mm_post_message",
        description="Post a message to a channel, optionally as a thread reply.",
        inputSchema=_schema(
            {
                "channel_id": {"type": "string", "description": "Mattermost channel id."},
                "message": {"type": "string", "description": "Message text."},
                "root_id": {"type": "string", "description": "Optional root post id for thread replies."},
                "props": {"type": "object", "description": "Optional Mattermost post props."},
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional local file paths to upload and attach to the message.",
                },
            },
            ["channel_id", "message"],
        ),
    ),
    types.Tool(
        name="mm_create_message_draft",
        description=(
            "Save text as a native synced Mattermost draft for the authenticated user, "
            "WITHOUT sending/publishing a message or notifying recipients. The user opens "
            "Mattermost Drafts, reviews it, and sends it themselves. Pass a joined channel's "
            "channel_id (including an existing direct/group-message channel); optionally pass "
            "root_id for a thread reply. Refuses any existing synced draft for that exact "
            "channel/thread, including attachment-only drafts. Never falls back to sending. "
            "Mattermost's API is not atomic: avoid concurrent editing of this destination in "
            "other clients; unsynced local drafts cannot be detected or protected."
        ),
        inputSchema=_schema(
            {
                "channel_id": {
                    "type": "string", "pattern": "^[a-z0-9]{26}$",
                    "description": "Joined channel or existing DM/group channel id; not a user id.",
                },
                "message": {"type": "string", "minLength": 1, "description": "Draft text to save without sending."},
                "root_id": {
                    "type": "string", "pattern": "^([a-z0-9]{26})?$", "default": "",
                    "description": "Optional root post id in the same channel; omit for a channel/DM draft.",
                },
            },
            ["channel_id", "message"],
        ),
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
    ),
    types.Tool(
        name="mm_update_post",
        description="Update a post's message text.",
        inputSchema=_schema(
            {
                "post_id": {"type": "string", "description": "Mattermost post id."},
                "message": {"type": "string", "description": "Replacement message text."},
            },
            ["post_id", "message"],
        ),
    ),
    types.Tool(
        name="mm_delete_post",
        description="Delete a Mattermost post.",
        inputSchema=_schema({"post_id": {"type": "string", "description": "Mattermost post id."}}, ["post_id"]),
    ),
    types.Tool(
        name="mm_get_channel_posts",
        description="Get recent posts for a channel.",
        inputSchema=_schema(
            {
                "channel_id": {"type": "string", "description": "Mattermost channel id."},
                "per_page": {
                    "type": "integer",
                    "description": "Maximum posts to return.",
                    "default": 30,
                    "minimum": 1,
                },
                "before": {"type": "string", "description": "Optional post id cursor before which to read."},
                "after": {"type": "string", "description": "Optional post id cursor after which to read."},
            },
            ["channel_id"],
        ),
    ),
    types.Tool(
        name="mm_get_thread",
        description=(
            "Get a post thread by root post id. Also the way to read a single post from a "
            "permalink: in https://<host>/<team>/pl/<id> that <id> IS the root_post_id — pass "
            "it here. Works for a post with no replies (the thread is just that post)."
        ),
        inputSchema=_schema(
            {
                "root_post_id": {
                    "type": "string",
                    "description": "Root Mattermost post id — the <id> tail of a /pl/<id> permalink.",
                }
            },
            ["root_post_id"],
        ),
    ),
    types.Tool(
        name="mm_search_posts",
        description=(
            "Search posts inside a team by TEXT. Not a lookup by id: a permalink id matches "
            "nothing here — use mm_get_thread for that."
        ),
        inputSchema=_schema(
            {
                "team_id": {
                    "type": "string",
                    "description": "Mattermost team id from mm_list_teams or mm_get_team_by_name.",
                },
                "terms": {"type": "string", "description": "Mattermost post search terms."},
                "is_or_search": {
                    "type": "boolean",
                    "description": "Use OR semantics for search terms.",
                    "default": False,
                },
            },
            ["team_id", "terms"],
        ),
    ),
    types.Tool(
        name="mm_add_reaction",
        description="Add an emoji reaction to a post.",
        inputSchema=_schema(
            {
                "user_id": {"type": "string", "description": "Mattermost user id adding the reaction."},
                "post_id": {"type": "string", "description": "Mattermost post id."},
                "emoji_name": {"type": "string", "description": "Emoji name without colons."},
            },
            ["user_id", "post_id", "emoji_name"],
        ),
    ),
    types.Tool(
        name="mm_get_unread_summary",
        description=(
            "Summarize unread messages, mentions, and threads per team for the authenticated "
            "user, plus grand totals. Read-only; a cheap first call for 'what is unread for me?'."
        ),
        inputSchema=_schema({}),
    ),
    types.Tool(
        name="mm_get_unread_threads",
        description=(
            "List the authenticated user's unread threads with a snippet of each root post, "
            "across all teams or a single team. Read-only."
        ),
        inputSchema=_schema(
            {
                "team_id": {
                    "type": "string",
                    "description": "Optional team id to scope to; omit to scan all of the user's teams.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum threads to return.",
                    "default": 50,
                    "minimum": 1,
                },
            },
        ),
    ),
    types.Tool(
        name="mm_get_unread_direct_messages",
        description=(
            "List unread direct and group messages with the counterpart's name/username, unread "
            "counts, and mentions, most recent first. Read-only."
        ),
        inputSchema=_schema(
            {
                "limit": {
                    "type": "integer",
                    "description": "Maximum conversations to return.",
                    "default": 50,
                    "minimum": 1,
                },
            },
        ),
    ),
    types.Tool(
        name="mm_get_unread_channels",
        description=(
            "List public/private channels with unread messages or mentions for the authenticated "
            "user, mentions first. Read-only."
        ),
        inputSchema=_schema(
            {
                "mentions_only": {
                    "type": "boolean",
                    "description": "Return only channels where the user is mentioned.",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum channels to return.",
                    "default": 50,
                    "minimum": 1,
                },
            },
        ),
    ),
)

TOOL_NAMES = tuple(tool.name for tool in TOOL_DEFINITIONS)


def _arg(arguments: dict[str, Any], key: str) -> Any:
    try:
        return arguments[key]
    except KeyError as exc:
        raise ValueError(f"Missing required argument: {key}") from exc


def _path_arg(arguments: dict[str, Any], key: str) -> str:
    """Required argument that is interpolated into the REST path.

    An empty one degenerates the URL instead of failing: ``teams//posts/search``
    answers 301 to the web app and ``channels//posts`` answers 404, so the caller
    reads a transport error and concludes the wrong thing. Name the argument here.
    """
    value = _arg(arguments, key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Argument {key} must be a non-empty string")
    return value


def _read_attachment(path: str) -> tuple[str, bytes]:
    file_path = Path(path)
    content = file_path.read_bytes()
    filename = file_path.name
    if not filename:
        raise ValueError(f"Cannot derive a filename from path: {path}")
    return filename, content


async def _post_message(client: Mattermost, arguments: dict[str, Any]) -> Any:
    channel_id = _path_arg(arguments, "channel_id")
    file_ids: list[str] = []
    for path in arguments.get("file_paths") or []:
        filename, content = _read_attachment(path)
        uploaded = await client.upload_file(channel_id, filename, content)
        file_ids.extend(info["id"] for info in uploaded.get("file_infos", []))
    return await client.post_message(
        channel_id,
        _arg(arguments, "message"),
        root_id=arguments.get("root_id"),
        props=arguments.get("props"),
        file_ids=file_ids or None,
    )


def _snippet(text: Any, limit: int = 200) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


async def _collect_unread_memberships(client: Mattermost) -> list[dict[str, Any]]:
    """Scan every team's channel memberships and return one entry per channel with
    any unread messages or mentions. Channels shared across teams (direct/group
    messages carry an empty team_id and repeat per team) are de-duplicated by id."""
    teams = await client.list_teams()
    seen: dict[str, dict[str, Any]] = {}
    for team in teams:
        team_id = team.get("id") if isinstance(team, dict) else None
        if not team_id:
            continue
        channels = await client.list_channels_for_team(team_id)
        members = await client.get_channel_members_for_team(team_id)
        member_by_channel = {
            m.get("channel_id"): m for m in members if isinstance(m, dict) and m.get("channel_id")
        }
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            channel_id = channel.get("id")
            if not channel_id or channel_id in seen:
                continue
            member = member_by_channel.get(channel_id)
            if member is None:
                continue
            unread = (channel.get("total_msg_count") or 0) - (member.get("msg_count") or 0)
            mentions = member.get("mention_count") or 0
            if unread <= 0 and mentions <= 0:
                continue
            seen[channel_id] = {
                "channel_id": channel_id,
                "type": channel.get("type"),
                "name": channel.get("name") or "",
                "display_name": channel.get("display_name") or "",
                "team_id": channel.get("team_id") or "",
                "unread_messages": unread if unread > 0 else 0,
                "mentions": mentions,
                "last_post_at": channel.get("last_post_at") or 0,
            }
    return list(seen.values())


async def _unread_summary(client: Mattermost) -> dict[str, Any]:
    teams = await client.list_teams()
    team_by_id = {t.get("id"): t for t in teams if isinstance(t, dict)}
    unread = await client.get_teams_unread()
    teams_out: list[dict[str, Any]] = []
    totals = {"unread_messages": 0, "mentions": 0, "unread_threads": 0, "thread_mentions": 0}
    for entry in unread:
        if not isinstance(entry, dict):
            continue
        team = team_by_id.get(entry.get("team_id"), {})
        messages = entry.get("msg_count") or 0
        mentions = entry.get("mention_count") or 0
        threads = entry.get("thread_count") or 0
        thread_mentions = entry.get("thread_mention_count") or 0
        totals["unread_messages"] += messages
        totals["mentions"] += mentions
        totals["unread_threads"] += threads
        totals["thread_mentions"] += thread_mentions
        if messages or mentions or threads or thread_mentions:
            teams_out.append(
                {
                    "team_id": entry.get("team_id"),
                    "team_name": team.get("name"),
                    "team_display_name": team.get("display_name"),
                    "unread_messages": messages,
                    "mentions": mentions,
                    "unread_threads": threads,
                    "thread_mentions": thread_mentions,
                }
            )
    teams_out.sort(key=lambda e: (e["mentions"], e["unread_messages"]), reverse=True)
    return {"teams": teams_out, "totals": totals}


async def _unread_threads(
    client: Mattermost, team_id: str | None = None, limit: int = 50
) -> dict[str, Any]:
    if team_id:
        team_ids = [team_id]
    else:
        teams = await client.list_teams()
        team_ids = [t.get("id") for t in teams if isinstance(t, dict) and t.get("id")]
    collected: list[tuple[str, dict[str, Any]]] = []
    totals = {"unread_threads": 0, "unread_mentions": 0}
    for tid in team_ids:
        response = await client.get_unread_threads(tid, page_size=limit)
        totals["unread_threads"] += response.get("total_unread_threads") or 0
        totals["unread_mentions"] += response.get("total_unread_mentions") or 0
        for thread in response.get("threads") or []:
            if isinstance(thread, dict):
                collected.append((tid, thread))
    author_ids = {
        (t.get("post") or {}).get("user_id")
        for _tid, t in collected
        if (t.get("post") or {}).get("user_id")
    }
    channel_ids = {
        (t.get("post") or {}).get("channel_id")
        for _tid, t in collected
        if (t.get("post") or {}).get("channel_id")
    }
    users_by_id: dict[str, Any] = {}
    if author_ids:
        try:
            users = await client.get_users_by_ids(sorted(author_ids))
            users_by_id = {u.get("id"): u for u in users if isinstance(u, dict)}
        except httpx.HTTPError:
            users_by_id = {}
    channels_by_id: dict[str, Any] = {}
    for channel_id in sorted(channel_ids)[:25]:
        try:
            channels_by_id[channel_id] = await client.get_channel(channel_id)
        except httpx.HTTPError:
            continue
    threads_out: list[dict[str, Any]] = []
    for tid, thread in collected:
        post = thread.get("post") or {}
        author = users_by_id.get(post.get("user_id")) or {}
        channel = channels_by_id.get(post.get("channel_id")) or {}
        threads_out.append(
            {
                "thread_id": thread.get("id"),
                "team_id": tid,
                "channel_id": post.get("channel_id"),
                "channel_display_name": channel.get("display_name") or None,
                "unread_replies": thread.get("unread_replies") or 0,
                "unread_mentions": thread.get("unread_mentions") or 0,
                "is_urgent": bool(thread.get("is_urgent")),
                "reply_count": thread.get("reply_count") or 0,
                "last_reply_at": thread.get("last_reply_at") or 0,
                "root_author": author.get("username") or post.get("user_id"),
                "snippet": _snippet(post.get("message")),
            }
        )
    threads_out.sort(key=lambda e: (e["unread_mentions"], e["last_reply_at"]), reverse=True)
    return {"threads": threads_out[:limit], "totals": totals}


async def _unread_direct_messages(client: Mattermost, limit: int = 50) -> dict[str, Any]:
    memberships = [m for m in await _collect_unread_memberships(client) if m["type"] in ("D", "G")]
    me = await client.get_me()
    my_id = me.get("id")
    counterpart_ids: set[str] = set()
    for membership in memberships:
        if membership["type"] == "D":
            parts = [p for p in membership["name"].split("__") if p]
            others = [p for p in parts if p != my_id] or parts
            membership["_counterpart_id"] = others[0] if others else None
            if membership["_counterpart_id"]:
                counterpart_ids.add(membership["_counterpart_id"])
    users_by_id: dict[str, Any] = {}
    if counterpart_ids:
        users = await client.get_users_by_ids(sorted(counterpart_ids))
        users_by_id = {u.get("id"): u for u in users if isinstance(u, dict)}
    entries: list[dict[str, Any]] = []
    for membership in memberships:
        entry: dict[str, Any] = {
            "channel_id": membership["channel_id"],
            "unread_messages": membership["unread_messages"],
            "mentions": membership["mentions"],
            "last_post_at": membership["last_post_at"],
        }
        if membership["type"] == "D":
            counterpart_id = membership.get("_counterpart_id")
            user = users_by_id.get(counterpart_id)
            entry["kind"] = "direct"
            if counterpart_id == my_id:
                entry["counterpart"] = {
                    "user_id": my_id,
                    "username": me.get("username"),
                    "full_name": "(you)",
                }
            elif user:
                full_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                entry["counterpart"] = {
                    "user_id": counterpart_id,
                    "username": user.get("username"),
                    "full_name": full_name or None,
                }
            else:
                entry["counterpart"] = {
                    "user_id": counterpart_id,
                    "username": None,
                    "full_name": None,
                }
        else:
            entry["kind"] = "group"
            entry["members"] = membership["display_name"] or None
        entries.append(entry)
    entries.sort(key=lambda e: e["last_post_at"], reverse=True)
    return {
        "direct_messages": entries[:limit],
        "totals": {
            "conversations": len(memberships),
            "unread_messages": sum(e["unread_messages"] for e in entries),
            "mentions": sum(e["mentions"] for e in entries),
        },
    }


async def _unread_channels(
    client: Mattermost, mentions_only: bool = False, limit: int = 50
) -> dict[str, Any]:
    memberships = [m for m in await _collect_unread_memberships(client) if m["type"] in ("O", "P")]
    if mentions_only:
        memberships = [m for m in memberships if m["mentions"] > 0]
    entries = [
        {
            "channel_id": m["channel_id"],
            "name": m["name"] or None,
            "display_name": m["display_name"] or None,
            "type": "public" if m["type"] == "O" else "private",
            "team_id": m["team_id"] or None,
            "unread_messages": m["unread_messages"],
            "mentions": m["mentions"],
            "last_post_at": m["last_post_at"],
        }
        for m in memberships
    ]
    entries.sort(key=lambda e: (e["mentions"], e["last_post_at"]), reverse=True)
    return {
        "channels": entries[:limit],
        "totals": {
            "channels": len(entries),
            "unread_messages": sum(e["unread_messages"] for e in entries),
            "mentions": sum(e["mentions"] for e in entries),
        },
    }


async def _call_mattermost(client: Mattermost, name: str, arguments: dict[str, Any]) -> Any:
    if name == "mm_get_me":
        return await client.get_me()
    if name == "mm_search_users":
        return await client.search_users(
            _arg(arguments, "term"),
            team_id=arguments.get("team_id"),
            limit=arguments.get("limit", 20),
        )
    if name == "mm_list_teams":
        return await client.list_teams()
    if name == "mm_get_team_by_name":
        return await client.get_team_by_name(_path_arg(arguments, "name"))
    if name == "mm_list_channels_for_team":
        return await client.list_channels_for_team(_path_arg(arguments, "team_id"), query=arguments.get("query"))
    if name == "mm_get_channel":
        return await client.get_channel(_path_arg(arguments, "channel_id"))
    if name == "mm_get_channel_by_name":
        return await client.get_channel_by_name(_path_arg(arguments, "team_id"), _path_arg(arguments, "name"))
    if name == "mm_create_direct_channel":
        return await client.create_direct_channel(_arg(arguments, "user_id_a"), _arg(arguments, "user_id_b"))
    if name == "mm_post_message":
        return await _post_message(client, arguments)
    if name == "mm_create_message_draft":
        return await client.create_message_draft(
            _arg(arguments, "channel_id"), _arg(arguments, "message"),
            root_id=arguments.get("root_id", ""),
        )
    if name == "mm_update_post":
        return await client.update_post(_path_arg(arguments, "post_id"), _arg(arguments, "message"))
    if name == "mm_delete_post":
        return await client.delete_post(_path_arg(arguments, "post_id"))
    if name == "mm_get_channel_posts":
        return await client.get_channel_posts(
            _path_arg(arguments, "channel_id"),
            per_page=arguments.get("per_page", 30),
            before=arguments.get("before"),
            after=arguments.get("after"),
        )
    if name == "mm_get_thread":
        return await client.get_thread(_path_arg(arguments, "root_post_id"))
    if name == "mm_search_posts":
        return await client.search_posts(
            _path_arg(arguments, "team_id"),
            _arg(arguments, "terms"),
            is_or_search=arguments.get("is_or_search", False),
        )
    if name == "mm_add_reaction":
        return await client.add_reaction(
            _arg(arguments, "user_id"),
            _arg(arguments, "post_id"),
            _arg(arguments, "emoji_name"),
        )
    if name == "mm_get_unread_summary":
        return await _unread_summary(client)
    if name == "mm_get_unread_threads":
        return await _unread_threads(
            client,
            team_id=arguments.get("team_id"),
            limit=arguments.get("limit", 50),
        )
    if name == "mm_get_unread_direct_messages":
        return await _unread_direct_messages(client, limit=arguments.get("limit", 50))
    if name == "mm_get_unread_channels":
        return await _unread_channels(
            client,
            mentions_only=arguments.get("mentions_only", False),
            limit=arguments.get("limit", 50),
        )
    raise ValueError(f"Unknown tool: {name}")


def create_server(client: Mattermost | None = None) -> Server:
    server = Server("mm-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return list(TOOL_DEFINITIONS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        if client is None:
            raise RuntimeError("Mattermost client is not configured")
        try:
            result = await _call_mattermost(client, name, arguments or {})
        except httpx.HTTPError as exc:
            raise RuntimeError(str(exc)) from exc
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, sort_keys=True))]

    return server


def initialization_options(server: Server, url: str) -> InitializationOptions:
    return InitializationOptions(
        server_name="mm-mcp",
        server_version=__version__,
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        instructions=INSTRUCTIONS_TEMPLATE.format(url=url),
    )


async def _run_stdio(server: Server, url: str) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, initialization_options(server, url))


async def _serve(url: str, token: str) -> None:
    client = Mattermost(url, token)
    try:
        await _run_stdio(create_server(client), url)
    finally:
        await client.aclose()


def main() -> None:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"mm-mcp: missing config: {exc}", file=sys.stderr)
        sys.exit(2)

    asyncio.run(_serve(cfg.url, cfg.token))
