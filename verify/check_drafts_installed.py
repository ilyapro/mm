#!/usr/bin/env python3
"""Verify installed MCP read-only; --create-self-drafts opts into a controlled smoke.

The smoke only creates two new drafts in an EXISTING self DM, leaves them for
manual review, and never publishes/deletes posts or changes an existing draft.
Only aggregate results and code hashes are printed; no tokens or colleague data.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mm_mcp
from mm_mcp.config import load_config
from mm_mcp.mattermost import Mattermost
from mm_mcp.server import TOOL_NAMES

REPO = Path(__file__).resolve().parent.parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def installed_receipt() -> dict:
    installed = Path(mm_mcp.__file__).resolve().parent
    assert installed != REPO / "src/mm_mcp", "Run without PYTHONPATH: verify the installed package"
    version = re.search(r'^version = "([^"\n]+)"', (REPO / "pyproject.toml").read_text(), re.M)[1]
    assert mm_mcp.__version__ == version
    files = []
    for source in sorted((REPO / "src/mm_mcp").glob("*.py")):
        loaded = installed / source.name
        assert source.read_bytes() == loaded.read_bytes(), f"Installed bytes differ: {source.name}"
        files.append({"source": str(source.relative_to(REPO)), "installed": str(loaded),
                      "sha256": sha(loaded.read_bytes())})
    launcher = Path.home() / ".local/bin/mm-mcp"
    return {"package_version": version, "source_files": files,
            "launcher": str(launcher), "launcher_sha256": sha(launcher.read_bytes())}


async def run(create: bool) -> dict:
    receipt = installed_receipt()
    cfg = load_config({"MM_MCP_ENV_FILE": str(Path.home() / ".config/mm.env")})

    async def read_only(request: httpx.Request) -> None:
        assert request.method == "GET", "The verification REST client is read-only"

    async with httpx.AsyncClient(timeout=20, event_hooks={"request": [read_only]}) as http:
        rest = Mattermost(cfg.url, cfg.token, http)
        launcher = receipt["launcher"]
        params = StdioServerParameters(command=launcher, args=[])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.version == receipt["package_version"]
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} == set(TOOL_NAMES)
                tool = next(t for t in tools if t.name == "mm_create_message_draft")
                assert "WITHOUT sending/publishing" in tool.description
                assert "not atomic" in tool.description
                receipt.update({"server_version": init.serverInfo.version, "tool_count": len(tools),
                                "draft_schema": tool.inputSchema, "description_verified": True})

                async def call(name: str, arguments: dict | None = None, error: bool = False):
                    result = await session.call_tool(name, arguments or {})
                    assert result.isError is error, f"Unexpected MCP result for {name}: {result.content}"
                    if error:
                        return result.content[0].text
                    return json.loads(result.content[0].text)

                me = await call("mm_get_me")
                teams = await call("mm_list_teams")
                assert me.get("id") and teams
                receipt["existing_read_tools_passed"] = ["mm_get_me", "mm_list_teams"]
                if not create:
                    receipt["mode"] = "read-only installed verification"
                    return receipt

                async def drafts_snapshot():
                    found = {}
                    for team in teams:
                        drafts = await rest._request("GET", f"users/me/teams/{team['id']}/drafts")
                        assert drafts is None or isinstance(drafts, list)
                        for draft in drafts or []:
                            found[(draft["channel_id"], draft.get("root_id", ""))] = draft
                    return found

                before_drafts = await drafts_snapshot()
                channels = {}
                for team in teams:
                    for channel in await call("mm_list_channels_for_team", {"team_id": team["id"]}):
                        if channel.get("type") == "D" and channel.get("name") == me["id"] + "__" + me["id"]:
                            channels[channel["id"]] = channel
                assert len(channels) == 1, "An existing self DM is required; no new conversation is created"
                channel_id = next(iter(channels))
                channel_before = await rest.get_channel(channel_id)
                posts_before = await call("mm_get_channel_posts", {"channel_id": channel_id, "per_page": 100})
                receipt["existing_read_tools_passed"].extend(["mm_list_channels_for_team", "mm_get_channel_posts"])
                roots = [p["id"] for p in posts_before.get("posts", {}).values()
                         if not p.get("root_id") and not p.get("delete_at")
                         and (channel_id, p["id"]) not in before_drafts]
                assert roots and (channel_id, "") not in before_drafts, "Vacant channel and thread destinations required"
                root_id = roots[0]
                keys = [(channel_id, ""), (channel_id, root_id)]
                stamp = datetime.now(timezone.utc).isoformat()
                messages = [f"MCP draft verification ({stamp}). Saved without sending; review or discard manually.",
                            f"MCP thread-draft verification ({stamp}). Saved without sending; review or discard manually."]
                created = []
                for key, message in zip(keys, messages):
                    result = await call("mm_create_message_draft", {
                        "channel_id": key[0], "root_id": key[1], "message": message,
                    })
                    assert result["sent"] is False and result["status"] == "saved"
                    assert result["draft"]["user_id"] == me["id"]
                    created.append(result["draft"])
                after_create = await drafts_snapshot()
                for key, message in zip(keys, messages):
                    assert after_create[key]["message"] == message
                    assert after_create[key]["user_id"] == me["id"]
                for key in keys:
                    error = await call("mm_create_message_draft", {
                        "channel_id": key[0], "root_id": key[1], "message": "MUST NOT OVERWRITE",
                    }, error=True)
                    assert "already exists" in error and "left unchanged" in error
                after_rejections = await drafts_snapshot()
                assert after_create == after_rejections, "Rejected calls must preserve drafts exactly"
                assert all(after_create.get(k) == v for k, v in before_drafts.items())
                assert set(after_create) - set(before_drafts) == set(keys)
                posts_after = await rest.get_channel_posts(channel_id, per_page=100)
                channel_after = await rest.get_channel(channel_id)
                assert posts_after == posts_before, "Posts must remain unchanged"
                for field in ("total_msg_count", "last_post_at"):
                    assert channel_after.get(field) == channel_before.get(field)
                receipt.update({
                    "mode": "controlled self-DM creation", "created_drafts": 2,
                    "destinations": ["self-DM", "self-DM existing thread"],
                    "saved_text_sha256": [sha(m.encode()) for m in messages],
                    "retrieved_native_drafts_match": True, "authenticated_owner_match": True,
                    "existing_drafts_before": len(before_drafts), "existing_drafts_unchanged": True,
                    "duplicate_calls_returned_isError": 2, "rejected_drafts_unchanged": True,
                    "posts_unchanged": True, "channel_post_counters_unchanged": True,
                    "drafts_left_for_manual_review": 2, "delete_calls": 0,
                })
                return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-self-drafts", action="store_true", help="Create two NEW self-DM drafts; never sends or deletes")
    args = parser.parse_args()
    receipt = asyncio.run(asyncio.wait_for(run(args.create_self_drafts), timeout=90))
    receipt["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["result"] = "PASS"
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
