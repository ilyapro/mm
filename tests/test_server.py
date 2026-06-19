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
