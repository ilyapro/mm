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


INSTRUCTIONS = """Mattermost MCP for https://mattermost.example.com — open this URL in the browser to see the same workspace this server talks to over REST.
Use mm_get_me first to identify the authenticated user, then list teams and channels before posting.
Tool results are returned as JSON text blocks from the Mattermost REST API.
mm_post_message accepts an optional file_paths array of local file paths; the server reads each file, uploads it to the channel, and attaches it to the post.
Tools: mm_get_me, mm_search_users, mm_list_teams, mm_get_team_by_name, mm_list_channels_for_team, mm_get_channel, mm_get_channel_by_name, mm_create_direct_channel, mm_post_message, mm_update_post, mm_delete_post, mm_get_channel_posts, mm_get_thread, mm_search_posts, mm_add_reaction."""


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
        description="Get a post thread by root post id.",
        inputSchema=_schema(
            {"root_post_id": {"type": "string", "description": "Root Mattermost post id."}},
            ["root_post_id"],
        ),
    ),
    types.Tool(
        name="mm_search_posts",
        description="Search posts inside a team.",
        inputSchema=_schema(
            {
                "team_id": {"type": "string", "description": "Mattermost team id."},
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
)

TOOL_NAMES = tuple(tool.name for tool in TOOL_DEFINITIONS)


def _arg(arguments: dict[str, Any], key: str) -> Any:
    try:
        return arguments[key]
    except KeyError as exc:
        raise ValueError(f"Missing required argument: {key}") from exc


def _read_attachment(path: str) -> tuple[str, bytes]:
    file_path = Path(path)
    content = file_path.read_bytes()
    filename = file_path.name
    if not filename:
        raise ValueError(f"Cannot derive a filename from path: {path}")
    return filename, content


async def _post_message(client: Mattermost, arguments: dict[str, Any]) -> Any:
    channel_id = _arg(arguments, "channel_id")
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
        return await client.get_team_by_name(_arg(arguments, "name"))
    if name == "mm_list_channels_for_team":
        return await client.list_channels_for_team(_arg(arguments, "team_id"), query=arguments.get("query"))
    if name == "mm_get_channel":
        return await client.get_channel(_arg(arguments, "channel_id"))
    if name == "mm_get_channel_by_name":
        return await client.get_channel_by_name(_arg(arguments, "team_id"), _arg(arguments, "name"))
    if name == "mm_create_direct_channel":
        return await client.create_direct_channel(_arg(arguments, "user_id_a"), _arg(arguments, "user_id_b"))
    if name == "mm_post_message":
        return await _post_message(client, arguments)
    if name == "mm_update_post":
        return await client.update_post(_arg(arguments, "post_id"), _arg(arguments, "message"))
    if name == "mm_delete_post":
        return await client.delete_post(_arg(arguments, "post_id"))
    if name == "mm_get_channel_posts":
        return await client.get_channel_posts(
            _arg(arguments, "channel_id"),
            per_page=arguments.get("per_page", 30),
            before=arguments.get("before"),
            after=arguments.get("after"),
        )
    if name == "mm_get_thread":
        return await client.get_thread(_arg(arguments, "root_post_id"))
    if name == "mm_search_posts":
        return await client.search_posts(
            _arg(arguments, "team_id"),
            _arg(arguments, "terms"),
            is_or_search=arguments.get("is_or_search", False),
        )
    if name == "mm_add_reaction":
        return await client.add_reaction(
            _arg(arguments, "user_id"),
            _arg(arguments, "post_id"),
            _arg(arguments, "emoji_name"),
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


def initialization_options(server: Server) -> InitializationOptions:
    return InitializationOptions(
        server_name="mm-mcp",
        server_version=__version__,
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
        instructions=INSTRUCTIONS,
    )


async def _run_stdio(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, initialization_options(server))


async def _serve(url: str, token: str) -> None:
    client = Mattermost(url, token)
    try:
        await _run_stdio(create_server(client))
    finally:
        await client.aclose()


def main() -> None:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"mm-mcp: missing config: {exc}", file=sys.stderr)
        sys.exit(2)

    asyncio.run(_serve(cfg.url, cfg.token))
