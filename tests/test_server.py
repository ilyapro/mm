from __future__ import annotations

import asyncio

import mcp.types as types
import pytest

import mm_mcp.server as server


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
