"""
MCP server entry point.
https://3olution.com

This module is intentionally thin. Its only job is to:

    1. Trigger creation of the shared `mcp` instance (via the
       import of `_server`).
    2. Import the `tools` package, whose module-level code
       registers every tool on the shared instance.
    3. Expose a `run()` function that starts the stdio server.

Everything else lives in `_server.py` (the instance and Windows
fixes) or in `tools/*.py` (the tool implementations).

================================================================
  HOW TO ADD A TOOL
================================================================

  Drop a new .py file into `mcp_server/tools/`. Inside it:

      from .._server import mcp

      @mcp.tool()
      def my_tool(arg1: str, arg2: int) -> str:
          \"\"\"One-line description the LLM will read.\"\"\"
          return "result"

  Restart the backend. The tool appears in the agent's tool list on
  the next turn.

  See `tools/README.md` for the full guide.

================================================================
  RUNNING STANDALONE
================================================================

  For debugging, you can run the server directly:

      python -m mcp_server.server

  It will start and wait for JSON-RPC messages on stdin. You will
  not see any output unless you send valid MCP requests — the
  server is silent until spoken to. Press Ctrl+C to stop.
"""

from __future__ import annotations

# Step 1 — create the shared instance (import side effect).
from ._server import mcp, run

# Step 2 — import the tools package. The tools/__init__.py loader
# runs at import time and pulls in every tool module, registering
# their @mcp.tool() functions on the shared instance.
from . import tools as _tools  # noqa: F401


__all__ = ["mcp", "run"]


# ══════════════════════════════════════════════════════════════════════
#  Standalone entry point
# ══════════════════════════════════════════════════════════════════════
#
#  This block makes `python -m mcp_server.server` work. It also makes
#  the module safe to import from tests, because the run() call only
#  happens when the module is executed directly.

if __name__ == "__main__":
    run()