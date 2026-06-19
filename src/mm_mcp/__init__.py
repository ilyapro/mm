"""mm-mcp — stdio MCP server for Mattermost over the Mattermost REST API.

The server exposes one MCP tool per Mattermost helper (teams, channels,
posts, threads, reactions). Connection details come from `MM_URL` and
`MM_TOKEN` in the environment; see :mod:`mm_mcp.config` for the fallback
to a shell-style env file via `MM_MCP_ENV_FILE`.

Public entry points:

* :func:`mm_mcp.server.main` — stdio server entry point (also exposed as
  the `mm-mcp` console script).
* :class:`mm_mcp.config.MmConfig` — validated server configuration.
* :class:`mm_mcp.mattermost.Mattermost` — async REST client wrapping the
  helpers the MCP tools delegate to.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
