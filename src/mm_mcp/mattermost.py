from __future__ import annotations

import asyncio
import json as json_module
import re
from typing import Any
from urllib.parse import urlencode

import httpx


class DraftError(RuntimeError):
    """A draft could not be safely created. Never fall back to posting."""


class Mattermost:
    def __init__(self, url: str, token: str, http: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._draft_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        response = await self._http.request(
            method,
            f"{self.url}/api/v4/{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            json=json,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def get_me(self) -> Any:
        return await self._request("GET", "users/me")

    async def search_users(self, term: str, team_id: str | None = None, limit: int = 20) -> Any:
        body: dict[str, Any] = {"term": term, "limit": limit}
        if team_id is not None:
            body["team_id"] = team_id
        return await self._request("POST", "users/search", json=body)

    async def list_teams(self) -> Any:
        return await self._request("GET", "users/me/teams")

    async def get_team_by_name(self, name: str) -> Any:
        return await self._request("GET", f"teams/name/{name}")

    async def list_channels_for_team(self, team_id: str, query: str | None = None) -> Any:
        if query:
            return await self._request(
                "POST",
                f"teams/{team_id}/channels/search",
                json={"term": query},
            )
        return await self._request("GET", f"users/me/teams/{team_id}/channels")

    async def get_channel(self, channel_id: str) -> Any:
        return await self._request("GET", f"channels/{channel_id}")

    async def get_channel_by_name(self, team_id: str, name: str) -> Any:
        return await self._request("GET", f"teams/{team_id}/channels/name/{name}")

    async def create_direct_channel(self, user_id_a: str, user_id_b: str) -> Any:
        return await self._request("POST", "channels/direct", json=[user_id_a, user_id_b])

    async def create_message_draft(
        self, channel_id: str, message: str, root_id: str = ""
    ) -> dict[str, Any]:
        """Save a native synced draft, refusing an existing destination draft.

        Mattermost only offers an upsert, not atomic insert-if-absent. Serialize
        calls on this client and check immediately before writing; other clients
        and unsynced local drafts remain outside this lock (disclosed to callers).
        """
        for name, value in (("channel_id", channel_id), ("root_id", root_id)):
            if name == "root_id" and value == "":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]{26}", value):
                raise ValueError(f"{name} must be a 26-character Mattermost id")
        # Empty-message upserts can delete a draft on some server versions.
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be non-empty text")

        save_attempted = False
        try:
            async with self._draft_lock:
                me = await self.get_me()
                user_id = me["id"]
                channel = await self.get_channel(channel_id)
                if channel.get("id") != channel_id or channel.get("type") not in ("O", "P", "D", "G"):
                    raise DraftError("Cannot verify the destination channel; no draft was written.")
                if channel.get("delete_at"):
                    raise DraftError("Cannot create a draft in an archived channel.")
                # Draft listings omit channels the user has not joined. Membership
                # must be confirmed so a missing listing is evidence of absence.
                member = await self._request("GET", f"channels/{channel_id}/members/{user_id}")
                if member.get("channel_id") != channel_id or member.get("user_id") != user_id:
                    raise DraftError("Cannot verify channel membership; no draft was written.")
                if root_id:
                    root = await self._request("GET", f"posts/{root_id}")
                    if root.get("id") != root_id or root.get("channel_id") != channel_id:
                        raise DraftError("root_id must identify a post in the destination channel.")
                    if root.get("root_id") or root.get("delete_at"):
                        raise DraftError("root_id must identify a non-deleted thread root, not a reply.")

                team_id = channel.get("team_id")
                if not team_id and channel["type"] in ("D", "G"):
                    teams = await self.list_teams()
                    if not teams:
                        raise DraftError(
                            "Cannot safely check direct-message drafts without a joined team; "
                            "no draft was written."
                        )
                    # Every team listing also includes all teamless DM/group drafts.
                    team_id = teams[0]["id"]
                if not isinstance(team_id, str) or not re.fullmatch(r"[a-z0-9]{26}", team_id):
                    raise DraftError("Cannot determine a team for the draft safety check.")
                drafts = await self._request("GET", f"users/me/teams/{team_id}/drafts")
                if drafts is None:
                    drafts = []  # Mattermost serializes an empty draft slice as null.
                if not isinstance(drafts, list) or any(
                    not isinstance(d, dict) or not isinstance(d.get("channel_id"), str)
                    or not isinstance(d.get("root_id", ""), str) for d in drafts
                ):
                    raise DraftError("Invalid draft listing; cannot safely rule out an existing draft.")
                if any(d["channel_id"] == channel_id and d.get("root_id", "") == root_id for d in drafts):
                    raise DraftError(
                        "A synced draft already exists for this channel/thread. "
                        "It was left unchanged; review it in Mattermost Drafts."
                    )

                save_attempted = True
                draft = await self._request("POST", "drafts", json={
                    "channel_id": channel_id, "root_id": root_id, "message": message,
                })
                if not isinstance(draft, dict) or any(
                    draft.get(key) != value for key, value in {
                        "user_id": user_id, "channel_id": channel_id,
                        "root_id": root_id, "message": message,
                    }.items()
                ):
                    raise DraftError(
                        "Mattermost returned an unexpected draft response. Save outcome is uncertain; "
                        "inspect Mattermost Drafts before retrying. No message was sent."
                    )
                return {
                    "status": "saved", "sent": False, "draft": draft,
                    "review": "Open Mattermost Drafts, review the text, and send it yourself.",
                    "safety_note": (
                        "Existing synced drafts are checked before saving. Mattermost has no atomic "
                        "create-only API: concurrent edits in other clients and unsynced local drafts "
                        "cannot be protected. Avoid editing this destination during the call."
                    ),
                }
        except httpx.HTTPError as exc:
            reason = "network/API failure"
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                try:
                    error = exc.response.json()
                except ValueError:
                    error = {}
                error_id = error.get("id", "") if isinstance(error, dict) else ""
                if status == 501 or "drafts.disabled" in error_id or "draft.feature_disabled" in error_id:
                    reason = "synced drafts are disabled on this Mattermost server"
                elif status == 401:
                    reason = "Mattermost authentication failed"
                elif status == 403:
                    reason = "insufficient Mattermost permissions for this draft/destination"
                elif status == 404:
                    reason = "destination/thread not found or synced-draft API unavailable"
                else:
                    reason = f"Mattermost API error (HTTP {status})"
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    reason += f": {error['message']}"
            outcome = (
                "Save outcome is uncertain; inspect Mattermost Drafts before retrying."
                if save_attempted else "No draft was written."
            )
            raise DraftError(f"Cannot create draft: {reason}. {outcome} No message was sent.") from exc
        except (json_module.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            outcome = "Inspect Mattermost Drafts before retrying." if save_attempted else "No draft was written."
            raise DraftError(f"Unexpected Mattermost API response. {outcome} No message was sent.") from exc

    async def post_message(
        self,
        channel_id: str,
        message: str,
        root_id: str | None = None,
        props: dict[str, Any] | None = None,
        file_ids: list[str] | None = None,
    ) -> Any:
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id is not None:
            body["root_id"] = root_id
        if props is not None:
            body["props"] = props
        if file_ids:
            body["file_ids"] = file_ids
        return await self._request("POST", "posts", json=body)

    async def upload_file(self, channel_id: str, filename: str, content: bytes) -> Any:
        response = await self._http.post(
            f"{self.url}/api/v4/files",
            headers={"Authorization": f"Bearer {self.token}"},
            data={"channel_id": channel_id},
            files={"files": (filename, content)},
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def update_post(self, post_id: str, message: str) -> Any:
        return await self._request("PUT", f"posts/{post_id}/patch", json={"message": message})

    async def delete_post(self, post_id: str) -> Any:
        return await self._request("DELETE", f"posts/{post_id}")

    async def get_channel_posts(
        self,
        channel_id: str,
        per_page: int = 30,
        before: str | None = None,
        after: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"per_page": per_page}
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        return await self._request("GET", f"channels/{channel_id}/posts?{urlencode(params)}")

    async def get_thread(self, root_post_id: str) -> Any:
        return await self._request("GET", f"posts/{root_post_id}/thread")

    async def search_posts(self, team_id: str, terms: str, is_or_search: bool = False) -> Any:
        return await self._request(
            "POST",
            f"teams/{team_id}/posts/search",
            json={"terms": terms, "is_or_search": is_or_search},
        )

    async def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> Any:
        return await self._request(
            "POST",
            "reactions",
            json={"user_id": user_id, "post_id": post_id, "emoji_name": emoji_name},
        )

    async def get_teams_unread(self) -> Any:
        return await self._request("GET", "users/me/teams/unread")

    async def get_unread_threads(self, team_id: str, user_id: str = "me", page_size: int = 50) -> Any:
        params = {"unread": "true", "pageSize": page_size}
        return await self._request(
            "GET", f"users/{user_id}/teams/{team_id}/threads?{urlencode(params)}"
        )

    async def get_channel_members_for_team(self, team_id: str) -> Any:
        return await self._request("GET", f"users/me/teams/{team_id}/channels/members")

    async def get_users_by_ids(self, user_ids: list[str]) -> Any:
        return await self._request("POST", "users/ids", json=list(user_ids))
