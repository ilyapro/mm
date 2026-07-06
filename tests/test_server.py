from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types
import pytest

import mm_mcp.server as server


class _FakeClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, bytes]] = []
        self.post: dict[str, Any] | None = None

    async def upload_file(self, channel_id: str, filename: str, content: bytes) -> Any:
        self.uploads.append((channel_id, filename, content))
        return {"file_infos": [{"id": f"id-{filename}"}]}

    async def post_message(
        self,
        channel_id: str,
        message: str,
        root_id: str | None = None,
        props: dict[str, Any] | None = None,
        file_ids: list[str] | None = None,
    ) -> Any:
        self.post = {
            "channel_id": channel_id,
            "message": message,
            "root_id": root_id,
            "props": props,
            "file_ids": file_ids,
        }
        return {"id": "post1"}


def test_server_registers_expected_tools_and_instructions() -> None:
    mcp_server = server.create_server()

    async def list_names() -> set[str]:
        result = await mcp_server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
        return {tool.name for tool in result.root.tools}

    assert asyncio.run(list_names()) == set(server.TOOL_NAMES)
    assert set(server.TOOL_NAMES) == {
        "mm_get_me",
        "mm_search_users",
        "mm_list_teams",
        "mm_get_team_by_name",
        "mm_list_channels_for_team",
        "mm_get_channel",
        "mm_get_channel_by_name",
        "mm_create_direct_channel",
        "mm_post_message",
        "mm_update_post",
        "mm_delete_post",
        "mm_get_channel_posts",
        "mm_get_thread",
        "mm_search_posts",
        "mm_add_reaction",
        "mm_get_unread_summary",
        "mm_get_unread_threads",
        "mm_get_unread_direct_messages",
        "mm_get_unread_channels",
    }
    assert "https://mattermost.example.com" in server.initialization_options(mcp_server).instructions


def test_main_missing_config_exits_2_before_stdio(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MM_URL", raising=False)
    monkeypatch.delenv("MM_TOKEN", raising=False)
    monkeypatch.delenv("MM_MCP_ENV_FILE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        server.main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "mm-mcp: missing config:" in captured.err
    assert captured.out == ""


def test_post_message_uploads_file_paths_and_attaches_ids(tmp_path) -> None:
    file_a = tmp_path / "a.txt"
    file_a.write_bytes(b"alpha")
    file_b = tmp_path / "b.txt"
    file_b.write_bytes(b"beta")

    client = _FakeClient()
    result = asyncio.run(
        server._call_mattermost(
            client,
            "mm_post_message",
            {
                "channel_id": "channel1",
                "message": "see files",
                "file_paths": [str(file_a), str(file_b)],
            },
        )
    )

    assert result == {"id": "post1"}
    assert client.uploads == [
        ("channel1", "a.txt", b"alpha"),
        ("channel1", "b.txt", b"beta"),
    ]
    assert client.post == {
        "channel_id": "channel1",
        "message": "see files",
        "root_id": None,
        "props": None,
        "file_ids": ["id-a.txt", "id-b.txt"],
    }


def test_post_message_without_file_paths_sends_no_file_ids() -> None:
    client = _FakeClient()
    result = asyncio.run(
        server._call_mattermost(
            client,
            "mm_post_message",
            {"channel_id": "channel1", "message": "plain"},
        )
    )

    assert result == {"id": "post1"}
    assert client.uploads == []
    assert client.post == {
        "channel_id": "channel1",
        "message": "plain",
        "root_id": None,
        "props": None,
        "file_ids": None,
    }


def test_post_message_missing_file_raises() -> None:
    client = _FakeClient()
    with pytest.raises(FileNotFoundError):
        asyncio.run(
            server._call_mattermost(
                client,
                "mm_post_message",
                {
                    "channel_id": "channel1",
                    "message": "oops",
                    "file_paths": ["/no/such/file.bin"],
                },
            )
        )
    assert client.post is None


_LONG_MESSAGE = "unread thread root post body " * 20  # > 200 chars, forces truncation


class _UnreadFakeClient:
    """Fake Mattermost client exercising the read-only unread compaction helpers.

    A single DM (``dm1``) is intentionally returned under both teams to prove
    de-duplication; the group message ``gm1`` is fully read (excluded)."""

    def __init__(self) -> None:
        self._me = {"id": "meid", "username": "me"}
        self._teams = [
            {"id": "t1", "name": "team-one", "display_name": "Team One"},
            {"id": "t2", "name": "team-two", "display_name": "Team Two"},
        ]
        dm1 = {
            "id": "dm1", "type": "D", "name": "meid__otherid", "display_name": "",
            "team_id": "", "total_msg_count": 10, "last_post_at": 200,
        }
        gm1 = {
            "id": "gm1", "type": "G", "name": "gmhash", "display_name": "me, ann, bob",
            "team_id": "", "total_msg_count": 5, "last_post_at": 150,
        }
        self._channels = {
            "t1": [
                dm1, gm1,
                {"id": "c1", "type": "O", "name": "town-square", "display_name": "Town Square",
                 "team_id": "t1", "total_msg_count": 8, "last_post_at": 100},
            ],
            "t2": [
                dm1, gm1,
                {"id": "c2", "type": "P", "name": "secret", "display_name": "Secret",
                 "team_id": "t2", "total_msg_count": 20, "last_post_at": 120},
            ],
        }
        self._members = {
            "t1": [
                {"channel_id": "dm1", "msg_count": 8, "mention_count": 1},
                {"channel_id": "gm1", "msg_count": 5, "mention_count": 0},
                {"channel_id": "c1", "msg_count": 3, "mention_count": 0},
            ],
            "t2": [
                {"channel_id": "dm1", "msg_count": 8, "mention_count": 1},
                {"channel_id": "gm1", "msg_count": 5, "mention_count": 0},
                {"channel_id": "c2", "msg_count": 18, "mention_count": 2},
            ],
        }

    async def list_teams(self) -> Any:
        return self._teams

    async def list_channels_for_team(self, team_id: str, query: str | None = None) -> Any:
        return self._channels[team_id]

    async def get_channel_members_for_team(self, team_id: str) -> Any:
        return self._members[team_id]

    async def get_me(self) -> Any:
        return self._me

    async def get_users_by_ids(self, user_ids: list[str]) -> Any:
        catalog = {
            "otherid": {"id": "otherid", "username": "other", "first_name": "Oth", "last_name": "Er"},
        }
        return [catalog[uid] for uid in user_ids if uid in catalog]

    async def get_teams_unread(self) -> Any:
        return [
            {"team_id": "t1", "msg_count": 5, "mention_count": 0,
             "thread_count": 0, "thread_mention_count": 0},
            {"team_id": "t2", "msg_count": 100, "mention_count": 3,
             "thread_count": 1, "thread_mention_count": 1},
        ]

    async def get_unread_threads(self, team_id: str, page_size: int = 50) -> Any:
        if team_id == "t2":
            return {
                "total_unread_threads": 1,
                "total_unread_mentions": 1,
                "threads": [
                    {
                        "id": "root1", "reply_count": 4, "last_reply_at": 300,
                        "unread_replies": 2, "unread_mentions": 1, "is_urgent": False,
                        "post": {"id": "root1", "channel_id": "c2", "user_id": "otherid",
                                 "message": _LONG_MESSAGE},
                    }
                ],
            }
        return {"total_unread_threads": 0, "total_unread_mentions": 0, "threads": []}

    async def get_channel(self, channel_id: str) -> Any:
        return {"id": channel_id, "display_name": "Secret" if channel_id == "c2" else channel_id}


def test_unread_direct_messages_dedupes_and_resolves_counterpart() -> None:
    result = asyncio.run(
        server._call_mattermost(_UnreadFakeClient(), "mm_get_unread_direct_messages", {})
    )
    # gm1 is fully read; dm1 appears in both teams but is reported once.
    assert result["direct_messages"] == [
        {
            "channel_id": "dm1",
            "unread_messages": 2,
            "mentions": 1,
            "last_post_at": 200,
            "kind": "direct",
            "counterpart": {"user_id": "otherid", "username": "other", "full_name": "Oth Er"},
        }
    ]
    assert result["totals"] == {"conversations": 1, "unread_messages": 2, "mentions": 1}


def test_unread_channels_filters_types_and_mentions_only() -> None:
    both = asyncio.run(
        server._call_mattermost(_UnreadFakeClient(), "mm_get_unread_channels", {})
    )
    # c2 (2 mentions) sorts before c1 (0 mentions); DM/GM excluded.
    assert [c["channel_id"] for c in both["channels"]] == ["c2", "c1"]
    assert both["channels"][0]["type"] == "private"
    assert both["channels"][1] == {
        "channel_id": "c1", "name": "town-square", "display_name": "Town Square",
        "type": "public", "team_id": "t1", "unread_messages": 5, "mentions": 0, "last_post_at": 100,
    }
    assert both["totals"] == {"channels": 2, "unread_messages": 7, "mentions": 2}

    mentions = asyncio.run(
        server._call_mattermost(
            _UnreadFakeClient(), "mm_get_unread_channels", {"mentions_only": True}
        )
    )
    assert [c["channel_id"] for c in mentions["channels"]] == ["c2"]


def test_unread_summary_joins_team_names_and_totals() -> None:
    result = asyncio.run(
        server._call_mattermost(_UnreadFakeClient(), "mm_get_unread_summary", {})
    )
    # Sorted by mentions desc: t2 (3 mentions) before t1 (0).
    assert [t["team_id"] for t in result["teams"]] == ["t2", "t1"]
    assert result["teams"][1] == {
        "team_id": "t1", "team_name": "team-one", "team_display_name": "Team One",
        "unread_messages": 5, "mentions": 0, "unread_threads": 0, "thread_mentions": 0,
    }
    assert result["totals"] == {
        "unread_messages": 105, "mentions": 3, "unread_threads": 1, "thread_mentions": 1,
    }


def test_unread_threads_compacts_snippet_and_resolves_context() -> None:
    result = asyncio.run(
        server._call_mattermost(_UnreadFakeClient(), "mm_get_unread_threads", {})
    )
    assert result["totals"] == {"unread_threads": 1, "unread_mentions": 1}
    assert len(result["threads"]) == 1
    thread = result["threads"][0]
    assert thread["thread_id"] == "root1"
    assert thread["team_id"] == "t2"
    assert thread["channel_id"] == "c2"
    assert thread["channel_display_name"] == "Secret"
    assert thread["root_author"] == "other"
    assert thread["unread_replies"] == 2 and thread["unread_mentions"] == 1
    assert thread["snippet"].endswith("…") and len(thread["snippet"]) <= 201
