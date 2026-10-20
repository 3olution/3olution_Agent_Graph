"""
3olution MCP tool server.
https://3olution.com

This package contains a FastMCP-compatible tool server that the
backend spawns as a stdio subprocess. It exposes a set of tools
(math, time, filesystem, text) that the agent can invoke through
the LLM's function-calling interface.

================================================================
  ARCHITECTURE
================================================================

  ┌────────────────────────────┐
  │  Backend process           │
  │  (backend/mcp_client.py)   │
  │                            │
  │  stdio_client ──┐          │
  └─────────────────┼──────────┘
                    │ stdin/stdout (JSON-RPC)
                    ▼
  ┌────────────────────────────┐
  │  MCP server subprocess     │
  │  (python -m mcp_server)    │
  │                            │
  │  ┌──────────────────────┐  │
  │  │ _server.py           │  │
  │  │   the shared mcp     │  │
  │  │   instance           │  │
  │  └──────────────────────┘  │
  │           ▲                │
  │           │ registers      │
  │           │ tools on       │
  │  ┌────────┴─────────────┐  │
  │  │ tools/*.py           │  │
  │  │   math_tools         │  │
  │  │   time_tools         │  │
  │  │   filesystem_tools   │  │
  │  │   text_tools         │  │
  │  └──────────────────────┘  │
  └────────────────────────────┘

================================================================
  WHY A SUBPROCESS?
================================================================

  Running the MCP server as a separate process (rather than
  embedding it in the backend) provides three advantages:

    1. Isolation: a crashed tool cannot take down the backend.
       The backend detects the dead subprocess and logs a clear
       error, but stays up so the user can use chat without tools.

    2. Language freedom: the MCP protocol is language-agnostic.
       Today the server is Python; tomorrow it could be Node.js
       or Go, and the backend would not change.

    3. Reusability: the same MCP server can be attached to any
       other MCP-compatible client — Claude Desktop, Continue,
       Cursor, etc. — without modification.

================================================================
  ENTRY POINT
================================================================

  Running this package as a module:

      python -m mcp_server

  ...is equivalent to running:

      python -m mcp_server.server

  Both start the stdio server and block waiting for JSON-RPC
  messages on stdin.
"""

from .server import mcp, run


__all__ = ["mcp", "run"]