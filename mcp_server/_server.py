"""
Shared MCP server instance.
https://3olution.com

This module owns the global `mcp` object that every tool module
decorates against. It also handles the two compatibility shims that
would otherwise need to be duplicated in every tool file:

    1. The Windows event loop policy fix. On Windows, Python's
       default ProactorEventLoop is incompatible with stdio
       subprocesses; FastMCP will randomly hang during the initial
       handshake. Setting SelectorEventLoopPolicy before importing
       the SDK fixes it.

    2. The mcp 1.x / 2.x class name change. mcp 1.x exposes
       `FastMCP`; mcp 2.x renamed the class to `MCPServer` and
       moved it to `mcp.server.mcpserver`. We try the 2.x import
       first and fall back to 1.x.

================================================================
  WHY A SEPARATE MODULE
================================================================

  Tool modules live in `tools/` and need to import the `mcp`
  instance. If the instance were created inside `server.py`, then
  importing a tool file would trigger a circular import:

        server.py
          imports tools/__init__.py
            imports tools/math_tools.py
              imports server.py  ← circular

  By placing the instance in `_server.py`, the cycle is broken:

        server.py
          imports _server.py       ← OK, no cycles
          imports tools/__init__.py
            imports tools/math_tools.py
              imports _server.py   ← already loaded, fine
"""

from __future__ import annotations

import asyncio
import sys


# ══════════════════════════════════════════════════════════════════════
#  Windows event loop fix
# ══════════════════════════════════════════════════════════════════════
#
#  MUST run before importing the MCP SDK. Python 3.8+ on Windows
#  defaults to ProactorEventLoop, which implements subprocess
#  creation but has known bugs when reading from the subprocess's
#  pipes. FastMCP's stdio transport reads from pipes continuously;
#  Proactor's pipe handling causes the initial handshake to hang
#  roughly 20% of the time.
#
#  SelectorEventLoop has reliable pipe support. It lacks
#  subprocess.create_subprocess_exec, but the MCP server does not
#  need that — it only reads and writes to its own stdio.

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ══════════════════════════════════════════════════════════════════════
#  SDK import with 1.x / 2.x compatibility
# ══════════════════════════════════════════════════════════════════════
#
#  The MCP Python SDK renamed its high-level server class between
#  major versions:
#
#      mcp 1.x:  from mcp.server.fastmcp import FastMCP
#      mcp 2.x:  from mcp.server.mcpserver import MCPServer
#
#  Some 2.x point releases also exposed MCPServer directly from
#  mcp.server. We try the newest path first, fall back through the
#  alternatives, and finally accept the 1.x name.

_SERVER_CLASS = None

try:
    # mcp >= 2.0 canonical path
    from mcp.server.mcpserver import MCPServer  # type: ignore[import-not-found]
    _SERVER_CLASS = MCPServer
except ImportError:
    try:
        # Some 2.x point releases exposed it directly
        from mcp.server import MCPServer  # type: ignore[import-not-found,no-redef]
        _SERVER_CLASS = MCPServer
    except ImportError:
        try:
            # mcp 1.x fallback
            from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found,no-redef]
            _SERVER_CLASS = FastMCP
        except ImportError as exc:
            raise ImportError(
                "Could not import a supported MCP server class from "
                "the installed `mcp` package.\n"
                "Expected one of:\n"
                "    mcp.server.mcpserver.MCPServer    (mcp >= 2.0)\n"
                "    mcp.server.MCPServer              (some 2.x)\n"
                "    mcp.server.fastmcp.FastMCP        (mcp 1.x)\n"
                "Install a supported version with:\n"
                "    pip install 'mcp>=2.0.0'"
            ) from exc


# ══════════════════════════════════════════════════════════════════════
#  The shared instance
# ══════════════════════════════════════════════════════════════════════
#
#  Every tool module does:
#
#      from .._server import mcp
#
#  and then decorates its functions with `@mcp.tool()`. The
#  decorator registers the tool on this instance.

mcp = _SERVER_CLASS("3olution Tools")


# ══════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════

def run() -> None:
    """
    Start the MCP server over stdio.

    Tries `show_banner=False` first. The banner is a Rich-rendered
    box that includes box-drawing characters (╭, │, ╰). Those are
    not valid JSON-RPC and corrupt the first message the client
    reads, causing an immediate "Connection closed" on the backend
    side. Suppressing it is essential on all platforms.

    Older SDK versions (mcp 1.0) do not accept the `show_banner`
    kwarg and raise TypeError. We catch that and call run() without
    arguments — the banner will appear but the server still works.
    """
    try:
        mcp.run(show_banner=False)
    except TypeError:
        # Older SDK — fall through to the plain call.
        mcp.run()