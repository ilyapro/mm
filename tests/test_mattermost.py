from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from mm_mcp.mattermost import Mattermost


async def _exercise(
    call: Callable[[Mattermost], Awaitable[Any]],
    expected_method: str,
    expected_path: str,
    expected_body: Any,
) -> None:
    seen: dict[str, Any] = {}
    payload = {"parsed": True}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query.decode()}"
        seen["method"] = request.method
        seen["path"] = path
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode()) if request.content else None
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = Mattermost("https://mm.example.test/", "token", http)
        result = await call(client)
    finally:
        await http.aclose()

    assert result == payload
    assert seen == {
        "method": expected_method,
        "path": expected_path,
        "authorization": "Bearer token",
        "body": expected_body,
    }


@pytest.mark.parametrize(
    ("call", "method", "path", "body"),
    [
        (lambda mm: mm.get_me(), "GET", "/api/v4/users/me", None),
        (
            lambda mm: mm.search_users("ann", team_id="team1", limit=5),
            "POST",
            "/api/v4/users/search",
            {"term": "ann", "team_id": "team1", "limit": 5},
        ),
        (lambda mm: mm.list_teams(), "GET", "/api/v4/users/me/teams", None),
        (lambda mm: mm.get_team_by_name("core"), "GET", "/api/v4/teams/name/core", None),
        (
            lambda mm: mm.list_channels_for_team("team1"),
            "GET",
            "/api/v4/users/me/teams/team1/channels",
            None,
        ),
        (
            lambda mm: mm.list_channels_for_team("team1", query="dev"),
            "POST",
            "/api/v4/teams/team1/channels/search",
            {"term": "dev"},
        ),
        (lambda mm: mm.get_channel("channel1"), "GET", "/api/v4/channels/channel1", None),
        (
            lambda mm: mm.get_channel_by_name("team1", "town-square"),
            "GET",
            "/api/v4/teams/team1/channels/name/town-square",
            None,
        ),
        (
            lambda mm: mm.create_direct_channel("user1", "user2"),
            "POST",
            "/api/v4/channels/direct",
            ["user1", "user2"],
        ),
        (
            lambda mm: mm.post_message("channel1", "hello", root_id="root1", props={"x": 1}),
            "POST",
            "/api/v4/posts",
            {"channel_id": "channel1", "message": "hello", "root_id": "root1", "props": {"x": 1}},
        ),
        (
            lambda mm: mm.post_message("channel1", "see attached", file_ids=["file1", "file2"]),
            "POST",
            "/api/v4/posts",
            {"channel_id": "channel1", "message": "see attached", "file_ids": ["file1", "file2"]},
        ),
        (
            lambda mm: mm.update_post("post1", "edited"),
            "PUT",
            "/api/v4/posts/post1/patch",
            {"message": "edited"},
        ),
        (lambda mm: mm.delete_post("post1"), "DELETE", "/api/v4/posts/post1", None),
        (
            lambda mm: mm.get_channel_posts("channel1", per_page=10, before="before1", after="after1"),
            "GET",
            "/api/v4/channels/channel1/posts?per_page=10&before=before1&after=after1",
            None,
        ),
        (lambda mm: mm.get_thread("root1"), "GET", "/api/v4/posts/root1/thread", None),
        (
            lambda mm: mm.search_posts("team1", "terms", is_or_search=True),
            "POST",
            "/api/v4/teams/team1/posts/search",
            {"terms": "terms", "is_or_search": True},
        ),
        (
            lambda mm: mm.add_reaction("user1", "post1", "thumbsup"),
            "POST",
            "/api/v4/reactions",
            {"user_id": "user1", "post_id": "post1", "emoji_name": "thumbsup"},
        ),
        (lambda mm: mm.get_teams_unread(), "GET", "/api/v4/users/me/teams/unread", None),
        (
            lambda mm: mm.get_unread_threads("team1"),
            "GET",
            "/api/v4/users/me/teams/team1/threads?unread=true&pageSize=50",
            None,
        ),
        (
            lambda mm: mm.get_unread_threads("team1", user_id="user1", page_size=5),
            "GET",
            "/api/v4/users/user1/teams/team1/threads?unread=true&pageSize=5",
            None,
        ),
        (
            lambda mm: mm.get_channel_members_for_team("team1"),
            "GET",
            "/api/v4/users/me/teams/team1/channels/members",
            None,
        ),
        (
            lambda mm: mm.get_users_by_ids(["user1", "user2"]),
            "POST",
            "/api/v4/users/ids",
            ["user1", "user2"],
        ),
    ],
)
def test_helpers_issue_expected_requests(
    call: Callable[[Mattermost], Awaitable[Any]],
    method: str,
    path: str,
    body: Any,
) -> None:
    asyncio.run(_exercise(call, method, path, body))


def test_non_2xx_raises_http_status_error() -> None:
    async def exercise() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            client = Mattermost("https://mm.example.test", "token", http)
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_me()
        finally:
            await http.aclose()

    asyncio.run(exercise())


def test_upload_file_posts_multipart_body() -> None:
    async def exercise() -> None:
        seen: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["authorization"] = request.headers.get("authorization")
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            return httpx.Response(201, json={"file_infos": [{"id": "file1"}]})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            client = Mattermost("https://mm.example.test/", "token", http)
            result = await client.upload_file("channel1", "report.txt", b"hello bytes")
        finally:
            await http.aclose()

        assert result == {"file_infos": [{"id": "file1"}]}
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v4/files"
        assert seen["authorization"] == "Bearer token"
        assert seen["content_type"].startswith("multipart/form-data")
        assert b"channel1" in seen["body"]
        assert b"report.txt" in seen["body"]
        assert b"hello bytes" in seen["body"]

    asyncio.run(exercise())
