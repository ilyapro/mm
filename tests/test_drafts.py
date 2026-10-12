from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import httpx
import mcp.types as types
import pytest

from mm_mcp.mattermost import DraftError, Mattermost
from mm_mcp.server import TOOL_DEFINITIONS, _call_mattermost, create_server

USER = "u" * 26
CHANNEL = "c" * 26
TEAM = "t" * 26
ROOT = "r" * 26
OTHER = "o" * 26


class DraftAPI:
    """Stateful REST fixture; an accidental post/delete is an assertion failure."""

    def __init__(self, channel_type: str = "O", drafts: Any = None):
        self.channel = {"id": CHANNEL, "type": channel_type, "delete_at": 0,
                        "team_id": TEAM if channel_type in ("O", "P") else ""}
        self.member = {"channel_id": CHANNEL, "user_id": USER}
        self.root = {"id": ROOT, "channel_id": CHANNEL, "root_id": "", "delete_at": 0}
        self.teams = [{"id": TEAM}]
        self.drafts = copy.deepcopy(drafts)
        self.failure: tuple[str, str, int, Any] | None = None
        self.invalid_json_at: str | None = None
        self.saved_response: Any = "normal"
        self.timeout_at: str | None = None
        self.requests: list[httpx.Request] = []
        self.saves: list[dict[str, Any]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer existing-token"
        path = request.url.path.removeprefix("/api/v4/")
        if self.timeout_at == path:
            raise httpx.ReadTimeout("test timeout", request=request)
        if self.failure and (request.method, path) == self.failure[:2]:
            return httpx.Response(self.failure[2], json=self.failure[3])
        if self.invalid_json_at == path:
            return httpx.Response(200, text="<html>not JSON</html>")
        if request.method == "GET":
            payloads = {
                "users/me": {"id": USER},
                f"channels/{CHANNEL}": self.channel,
                f"channels/{CHANNEL}/members/{USER}": self.member,
                f"posts/{ROOT}": self.root,
                "users/me/teams": self.teams,
                f"users/me/teams/{TEAM}/drafts": self.drafts,
            }
            assert path in payloads, f"Unexpected read: {path}"
            # Yield with the captured snapshot, like a network round-trip. Without
            # the lock, two callers can both observe an empty draft listing.
            await asyncio.sleep(0)
            # json=None means no body to httpx; Mattermost sends literal null.
            return httpx.Response(200, content=json.dumps(payloads[path]),
                                  headers={"Content-Type": "application/json"})
        assert (request.method, path) == ("POST", "drafts"), "Must never publish/delete/upload"
        body = json.loads(request.content)
        assert set(body) == {"channel_id", "root_id", "message"}
        self.saves.append(body)
        draft = {**body, "user_id": USER, "create_at": 123, "update_at": 123}
        self.drafts = [*(self.drafts or []), draft]
        return httpx.Response(201, content=json.dumps(
            draft if self.saved_response == "normal" else self.saved_response
        ), headers={"Content-Type": "application/json"})


async def call(api: DraftAPI, message: Any = "  Привет **draft**\n@all  ", root_id: Any = "", channel_id: Any = CHANNEL):
    async with httpx.AsyncClient(transport=httpx.MockTransport(api.handle)) as http:
        client = Mattermost("https://mm.example.test", "existing-token", http)
        return await _call_mattermost(client, "mm_create_message_draft", {
            "channel_id": channel_id, "message": message, "root_id": root_id,
        })


@pytest.mark.parametrize("channel_type", ["O", "P", "D", "G"])
@pytest.mark.parametrize("root_id", ["", ROOT])
def test_native_draft_preserves_text_and_never_posts(channel_type, root_id):
    api = DraftAPI(channel_type)
    result = asyncio.run(call(api, root_id=root_id))
    assert result["status"] == "saved" and result["sent"] is False
    assert result["draft"] == api.drafts[0]
    assert result["draft"]["user_id"] == USER
    assert api.saves == [{"channel_id": CHANNEL, "root_id": root_id,
                          "message": "  Привет **draft**\n@all  "}]
    assert "no atomic" in result["safety_note"]
    assert "send it yourself" in result["review"]
    assert api.requests[-2].url.path == f"/api/v4/users/me/teams/{TEAM}/drafts"
    assert api.requests[-1].url.path == "/api/v4/drafts"
    assert any(r.url.path == "/api/v4/users/me/teams" for r in api.requests) == (channel_type in ("D", "G"))


@pytest.mark.parametrize("root_id", ["", ROOT])
@pytest.mark.parametrize("existing", [
    {"message": "user text", "file_ids": []},
    {"message": "", "file_ids": ["attachment"]},
    {"message": "", "file_ids": [], "props": {"pending": True}},
])
def test_existing_destination_draft_is_preserved(root_id, existing):
    old = [{**existing, "user_id": USER, "channel_id": CHANNEL, "root_id": root_id}]
    api = DraftAPI("D", old)
    with pytest.raises(DraftError, match="already exists.*left unchanged"):
        asyncio.run(call(api, root_id=root_id))
    assert api.drafts == old and api.saves == []
    assert all(r.method == "GET" for r in api.requests)


@pytest.mark.parametrize("root_id,old_root", [(ROOT, ""), ("", ROOT), (ROOT, OTHER)])
def test_other_destinations_do_not_block_or_change(root_id, old_root):
    old = [{"channel_id": CHANNEL, "root_id": old_root, "message": "untouched"},
           {"channel_id": OTHER, "root_id": root_id, "message": "other channel"}]
    api = DraftAPI(drafts=old)
    asyncio.run(call(api, root_id=root_id))
    assert api.drafts[:2] == old
    assert len(api.saves) == 1


@pytest.mark.parametrize("message", ["", "  \n", None, 1, {}])
def test_empty_or_non_text_message_never_reaches_api(message):
    api = DraftAPI()
    with pytest.raises(ValueError, match="message"):
        asyncio.run(call(api, message=message))
    assert api.requests == []


@pytest.mark.parametrize("field,value", [
    ("channel_id", ""), ("channel_id", "../users/me"), ("channel_id", None),
    ("root_id", "bad"), ("root_id", None), ("root_id", "../posts"),
])
def test_invalid_ids_never_reach_api(field, value):
    api = DraftAPI()
    with pytest.raises(ValueError, match=field):
        asyncio.run(call(api, **{field: value}))
    assert api.requests == []


@pytest.mark.parametrize("attribute,change,reason", [
    ("channel", {"delete_at": 1}, "archived"),
    ("channel", {"type": "bad"}, "destination channel"),
    ("member", {"user_id": OTHER}, "membership"),
    ("root", {"channel_id": OTHER}, "destination channel"),
    ("root", {"root_id": OTHER}, "thread root"),
    ("root", {"delete_at": 1}, "thread root"),
])
def test_unsafe_destinations_do_not_write(attribute, change, reason):
    api = DraftAPI()
    getattr(api, attribute).update(change)
    with pytest.raises(DraftError, match=reason):
        asyncio.run(call(api, root_id=ROOT))
    assert api.saves == []


def test_teamless_user_cannot_skip_existing_dm_draft_check():
    api = DraftAPI("D")
    api.teams = []
    with pytest.raises(DraftError, match="without a joined team"):
        asyncio.run(call(api))
    assert api.saves == []


@pytest.mark.parametrize("method,path,status,error,reason", [
    ("GET", "users/me", 401, {}, "authentication"),
    ("GET", f"channels/{CHANNEL}/members/{USER}", 403, {}, "permissions"),
    ("GET", f"posts/{ROOT}", 404, {}, "not found"),
    ("GET", f"users/me/teams/{TEAM}/drafts", 501, {"id": "api.drafts.disabled.app_error"}, "disabled"),
    ("GET", f"users/me/teams/{TEAM}/drafts", 404, {}, "unavailable"),
    ("GET", f"users/me/teams/{TEAM}/drafts", 500, {}, "HTTP 500"),
    ("POST", "drafts", 501, {}, "disabled"),
    ("POST", "drafts", 403, {"message": "Cannot create posts"}, "permissions.*Cannot create posts"),
    ("POST", "drafts", 400, {"message": "Message is too long"}, "HTTP 400.*too long"),
    ("POST", "drafts", 500, {}, "HTTP 500"),
])
def test_api_failures_are_clear_and_never_fall_back(method, path, status, error, reason):
    api = DraftAPI()
    api.failure = (method, path, status, error)
    with pytest.raises(DraftError, match=reason) as failure:
        asyncio.run(call(api, root_id=ROOT))
    assert "No message was sent" in str(failure.value)
    assert ("Save outcome is uncertain" in str(failure.value)) == (method == "POST")
    assert api.saves == []


@pytest.mark.parametrize("drafts", [{}, "oops", [None], [{"message": "unsafe"}], [{"channel_id": CHANNEL, "root_id": None}]])
def test_malformed_listing_fails_closed(drafts):
    api = DraftAPI(drafts=drafts)
    with pytest.raises(DraftError, match="Invalid draft listing"):
        asyncio.run(call(api))
    assert api.saves == []


def test_non_json_preflight_fails_closed():
    api = DraftAPI()
    api.invalid_json_at = f"users/me/teams/{TEAM}/drafts"
    with pytest.raises(DraftError, match="Unexpected Mattermost API response.*No draft was written"):
        asyncio.run(call(api))
    assert api.saves == []


@pytest.mark.parametrize("response", [None, {}, {"user_id": OTHER}])
def test_unexpected_save_response_is_not_claimed_as_success(response):
    api = DraftAPI()
    api.saved_response = response
    with pytest.raises(DraftError, match="Save outcome is uncertain.*No message was sent"):
        asyncio.run(call(api))
    assert len(api.saves) == 1


@pytest.mark.parametrize("path", [f"users/me/teams/{TEAM}/drafts", "drafts"])
def test_network_error_and_uncertain_save(path):
    api = DraftAPI()
    api.timeout_at = path
    with pytest.raises(DraftError, match="network/API failure") as failure:
        asyncio.run(call(api))
    assert ("Save outcome is uncertain" in str(failure.value)) == (path == "drafts")
    assert api.saves == []


def test_concurrent_calls_on_one_connection_cannot_overwrite():
    api = DraftAPI()
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api.handle)) as http:
            client = Mattermost("https://mm.example.test", "existing-token", http)
            return await asyncio.gather(
                client.create_message_draft(CHANNEL, "first"),
                client.create_message_draft(CHANNEL, "second"), return_exceptions=True,
            )
    results = asyncio.run(exercise())
    assert results[0]["draft"]["message"] == "first"
    assert isinstance(results[1], DraftError)
    assert api.saves == [{"channel_id": CHANNEL, "root_id": "", "message": "first"}]


def test_mcp_schema_says_save_without_sending():
    tool = next(t for t in TOOL_DEFINITIONS if t.name == "mm_create_message_draft")
    assert "WITHOUT sending/publishing" in tool.description
    assert "Refuses any existing" in tool.description
    assert "not atomic" in tool.description
    assert tool.inputSchema["required"] == ["channel_id", "message"]
    assert set(tool.inputSchema["properties"]) == {"channel_id", "message", "root_id"}
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False


@pytest.mark.parametrize("fail", [False, True])
def test_mcp_wire_returns_saved_result_or_is_error(fail):
    api = DraftAPI()
    if fail:
        api.failure = ("GET", f"users/me/teams/{TEAM}/drafts", 501, {})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(api.handle)) as http:
            client = Mattermost("https://mm.example.test", "existing-token", http)
            server = create_server(client)
            response = await server.request_handlers[types.CallToolRequest](types.CallToolRequest(
                method="tools/call", params=types.CallToolRequestParams(
                    name="mm_create_message_draft", arguments={"channel_id": CHANNEL, "message": "review me"},
                ),
            ))
            return response.root
    result = asyncio.run(exercise())
    assert result.isError is fail
    if fail:
        assert "disabled" in result.content[0].text
        assert api.saves == []
    else:
        assert json.loads(result.content[0].text)["sent"] is False
        assert len(api.saves) == 1
