from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx


class Mattermost:
    def __init__(self, url: str, token: str, http: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None

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

    async def post_message(
        self,
        channel_id: str,
        message: str,
        root_id: str | None = None,
        props: dict[str, Any] | None = None,
    ) -> Any:
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id is not None:
            body["root_id"] = root_id
        if props is not None:
            body["props"] = props
        return await self._request("POST", "posts", json=body)

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
