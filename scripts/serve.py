#!/usr/bin/env python3
"""
3olution Agent Graph — uvicorn entry point.
https://3olution.com

This module exists because launching uvicorn directly has three
problems that are easy to miss and hard to diagnose:

    1. Windows event loop
    2. sys.path for the reloader child
    3. Access log noise

================================================================
  PROBLEM 1: WINDOWS EVENT LOOP
================================================================

  On Windows, the MCP SDK's stdio transport requires
  ProactorEventLoop to spawn subprocesses. But ProactorEventLoop
  on Python 3.8+ has a known quirk: it cannot reliably read from
  the subprocess's pipes, causing the MCP handshake to randomly
  hang or fail.

  The correct policy for a process that spawns subprocesses AND
  reads from their pipes is SelectorEventLoop. But Selector does
  not implement create_subprocess_exec, which uvicorn itself
  needs... except it doesn't, not for the default HTTP/WebSocket
  transports. Those use sockets, which Selector handles fine.

  The MCP SDK v2+ handles this internally by dispatching
  subprocess creation through its own thread pool, so it works
  under either policy. But the fix for the general case is to set
  Selector early — before uvicorn imports its asyncio loop.

  We set it here, at the top of this module, before importing
  uvicorn. Setting it inside backend/main.py is too late: uvicorn
  creates its loop before it imports the app.

================================================================
  PROBLEM 2: sys.path FOR THE RELOADER CHILD
================================================================

  Running `python scripts/serve.py` puts scripts/ on sys.path[0],
  not the project root. When uvicorn (with --reload) spawns a
  child process, that child re-imports backend.main, which fails
  with ModuleNotFoundError because the project root is not on
  sys.path.

  Two things are needed:

    - sys.path in THIS process (for direct imports)
    - PYTHONPATH in the environment (for the child process)

  Windows uses `multiprocessing` spawn for the reloader child,
  which does NOT inherit sys.path mutations from the parent. Only
  the environment variable survives.

================================================================
  PROBLEM 3: ACCESS LOG NOISE
================================================================

  Uvicorn's per-request access log is verbose. The web UI polls
  /api/health, /api/ollama/ps, and /api/llama/runtime/status every
  few seconds while a page is open. In a four-hour session that is
  thousands of identical log lines — a real problem for the
  desktop log dock and the rotating file sink.

  Access logging is disabled by default here. Re-enable by setting
  AGENT_UVICORN_ACCESS_LOG=1 in .env.

================================================================
  USAGE
================================================================

  From the command line:

      python scripts/serve.py

  Or via the developer launcher:

      python run.py backend

  Or via the portable launcher:

      ./start.sh          # Linux / macOS
      start.bat           # Windows

  Or directly with uvicorn (bypasses this file's fixes):

      uvicorn backend.main:app --reload

  Environment variables read:

      AGENT_HOST                  bind address (default 127.0.0.1)
      AGENT_PORT                  bind port (default 8000)
      AGENT_RELOAD                "1" to enable --reload (default "0")
      AGENT_LOG_LEVEL             uvicorn log level (default "info")
      AGENT_UVICORN_ACCESS_LOG    "1" to enable access log (default "0")
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
#  Fix 1 — make `backend` importable
# ══════════════════════════════════════════════════════════════════════
#
#  Must run BEFORE importing uvicorn (which imports the app).

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_root_str = str(PROJECT_ROOT)

# sys.path — for imports in this process.
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)

# PYTHONPATH — for the reloader child process.
# Windows multiprocessing spawn does not inherit sys.path mutations,
# only the environment.
_existing_pp = os.environ.get("PYTHONPATH", "")
if _root_str not in _existing_pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = (
        _root_str + (os.pathsep + _existing_pp if _existing_pp else "")
    )


# ══════════════════════════════════════════════════════════════════════
#  Fix 2 — Windows event loop policy
# ══════════════════════════════════════════════════════════════════════
#
#  Set this BEFORE importing uvicorn. uvicorn will pick up the
#  policy when it creates its loop.

if sys.platform == "win32":
    # SelectorEventLoop has reliable pipe reading, which the MCP
    # SDK's stdio transport depends on. The MCP SDK dispatches
    # subprocess creation internally so it does not need Proactor.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ══════════════════════════════════════════════════════════════════════
#  Now it is safe to import uvicorn
# ══════════════════════════════════════════════════════════════════════

import uvicorn  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
#  Configuration
# ══════════════════════════════════════════════════════════════════════

HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_PORT", "8000"))
RELOAD = os.environ.get("AGENT_RELOAD", "0") == "1"
LOG_LEVEL = os.environ.get("AGENT_LOG_LEVEL", "info").lower()

# Access log — off by default. See Problem 3 in the module docstring.
ACCESS_LOG = os.environ.get("AGENT_UVICORN_ACCESS_LOG", "0") == "1"


# ══════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    """Run uvicorn with the correct loop and the project on sys.path."""
    uvicorn.run(
        "backend.main:app",
        host=HOST,
        port=PORT,
        reload=RELOAD,
        log_level=LOG_LEVEL,
        loop="asyncio",      # honor the policy set above
        access_log=ACCESS_LOG,
        # The reloader watches only the project root, not the whole
        # filesystem. Adding .venv/ would cause reloads on every
        # pip operation.
        reload_dirs=[str(PROJECT_ROOT)] if RELOAD else None,
    )


if __name__ == "__main__":
    main()