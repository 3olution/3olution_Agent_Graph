"""
System control and log access.
https://3olution.com

Endpoints:

    GET  /api/system/status     -- live subsystem state
    POST /api/system/stop-all   -- stop llama-cpp, MCP, and self
    GET  /api/logs/files        -- list log files
    GET  /api/logs/recent       -- tail the most recent log lines

================================================================
  STOP-ALL SEMANTICS
================================================================

  POST /api/system/stop-all returns immediately with a JSON
  acknowledgement, then terminates every subsystem in a background
  task. It does NOT wait for the shutdown to complete.

  Why: the HTTP response must be delivered BEFORE the process
  exits, otherwise the client sees a connection reset instead of
  a clean 200. We schedule the shutdown on the next tick, send the
  response, and only then actually shut down.

  The task reference is held in a module-level variable. Python's
  asyncio only keeps weak references to tasks; without a strong
  reference, the garbage collector may destroy the task before the
  coroutine runs.

  On Unix, the process terminates with SIGTERM (caught by uvicorn
  and re-raised as a clean exit). On Windows, os._exit(0) is used
  because there is no equivalent signal and uvicorn's graceful
  shutdown is unreliable when triggered from within a request
  handler.

================================================================
  LOG READING
================================================================

  The log viewer in the desktop client and the settings page uses
  /api/logs/recent. It reads the tail of the newest matching log
  file.

  Earlier implementations called `f.readlines()` on the whole file
  and then took the last N lines. That allocates the entire file in
  memory — for a 500 MB log, 500 MB of RAM. The current
  implementation iterates the file once and keeps only the last
  `lines` entries in a bounded deque, so memory is O(lines) not
  O(file size).

  Line count is clamped to 5000 to keep a runaway client from
  reading a multi-gigabyte log file into memory.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException
from loguru import logger

from ..config import settings
from ..state import state


router = APIRouter(prefix="/api", tags=["system"])


# ══════════════════════════════════════════════════════════════════════
#  Module-level state
# ══════════════════════════════════════════════════════════════════════
#
#  Strong reference to the shutdown task. Without this, Python's
#  garbage collector may destroy the Task object before its coroutine
#  runs, and the shutdown silently does nothing.

_shutdown_task: asyncio.Task | None = None


# ══════════════════════════════════════════════════════════════════════
#  System status
# ══════════════════════════════════════════════════════════════════════

@router.get("/system/status")
async def system_status():
    """
    Return the state of every subsystem the backend manages.

    Used by the desktop client's stop-all confirmation dialog and
    by the settings page's diagnostics panel.
    """
    llama = state.get("llama_server")
    mcp = state.get("mcp")

    llama_ready = False
    llama_running = False
    if llama is not None:
        try:
            llama_running = bool(llama.is_running)
            if llama_running:
                llama_ready = await llama.is_ready()
        except Exception:
            llama_ready = False

    return {
        "backend": True,
        "backend_pid": os.getpid(),
        "mcp_connected": bool(mcp and getattr(mcp, "_connected", False)),
        "llama_cpp_enabled": llama is not None,
        "llama_cpp_running": llama_running,
        "llama_cpp_ready": llama_ready,
        "logs_dir": str(settings.logs_path),
        "session_db": settings.session_db,
        "models_dir": str(settings.models_path),
    }


# ══════════════════════════════════════════════════════════════════════
#  Stop-all
# ══════════════════════════════════════════════════════════════════════

@router.post("/system/stop-all")
async def stop_all():
    """
    Stop every process the backend manages, then exit.

    Returns a JSON acknowledgement immediately; the actual shutdown
    runs in a background task.

    Order matters:

      1. Stop llama-cpp first. It owns the largest allocation and
         should release its memory before the MCP subprocess tears
         down.
      2. Close the MCP session cleanly.
      3. Terminate the backend process itself.
    """
    global _shutdown_task

    logger.warning("Stop-all requested from API. Shutting everything down...")

    async def _shutdown() -> None:
        # Give the HTTP response time to reach the client.
        await asyncio.sleep(0.8)

        # ── llama-cpp ───────────────────────────────────────────────
        llama = state.get("llama_server")
        if llama is not None:
            try:
                await llama.stop()
                logger.info("llama-cpp server stopped")
            except Exception as exc:
                logger.warning("Error stopping llama-cpp server: {}", exc)

        # ── MCP ─────────────────────────────────────────────────────
        mcp = state.get("mcp")
        if mcp is not None:
            try:
                await mcp.close()
                logger.info("MCP session closed")
            except Exception as exc:
                logger.warning("Error closing MCP: {}", exc)

        # ── Self ────────────────────────────────────────────────────
        logger.info("Terminating backend process")
        try:
            if sys.platform == "win32":
                # os._exit bypasses atexit handlers; nothing left to clean up.
                os._exit(0)
            else:
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)

    # Hold a strong reference so the task is not garbage-collected
    # before it runs. See the module docstring for the rationale.
    _shutdown_task = asyncio.create_task(_shutdown())

    return {
        "status": "stopping",
        "message": "Backend is shutting down.",
    }


# ══════════════════════════════════════════════════════════════════════
#  Log files listing
# ══════════════════════════════════════════════════════════════════════

@router.get("/logs/files")
async def list_log_files():
    """
    Return the list of log files in the logs directory.

    Files are sorted newest first by name. Each entry has the file's
    name, size in bytes, and size in kilobytes for display.
    """
    log_dir = settings.logs_path
    if not log_dir.exists():
        return {"files": [], "directory": str(log_dir)}

    files = []
    for f in sorted(log_dir.glob("*.log"), reverse=True):
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        files.append({
            "name": f.name,
            "size": size,
            "size_kb": round(size / 1024, 1),
        })
    return {"files": files, "directory": str(log_dir)}


# ══════════════════════════════════════════════════════════════════════
#  Recent log lines
# ══════════════════════════════════════════════════════════════════════

@router.get("/logs/recent")
async def recent_logs(lines: int = 500, source: str = "all"):
    """
    Return the tail of the newest log file.

    Query parameters:
        lines   Maximum number of lines to return. Clamped to [1, 5000].
        source  "backend" | "desktop" | "all". Selects which file
                pattern to look for: backend-*.log, desktop-*.log,
                or both.

    The response is a list of strings, one per line, with trailing
    newlines stripped. It includes the file name and total line count
    so the client can show "(showing last 500 of 12034 lines)".

    Memory use is O(lines), not O(file size). The file is streamed
    line by line into a bounded deque, so a 500 MB log does not
    allocate 500 MB of RAM.
    """
    lines = max(1, min(lines, 5000))

    log_dir = settings.logs_path
    if not log_dir.exists():
        return {"lines": [], "source": source}

    if source == "backend":
        patterns = ["backend-*.log"]
    elif source == "desktop":
        patterns = ["desktop-*.log"]
    else:
        patterns = ["backend-*.log", "desktop-*.log"]

    files: list[Path] = []
    for pat in patterns:
        files.extend(log_dir.glob(pat))

    if not files:
        return {"lines": [], "source": source}

    # Sort by modification time, newest first, and take the latest.
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest = files[0]

    # Stream the file into a bounded deque. The deque holds at most
    # `lines` entries; older entries are discarded as new ones arrive.
    # Total line count is tracked separately so the response can
    # report "showing last 500 of N".
    tail: deque[str] = deque(maxlen=lines)
    total_lines = 0

    try:
        with latest.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                tail.append(line)
                total_lines += 1
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read log file: {exc}",
        )

    return {
        "lines": [line.rstrip("\n") for line in tail],
        "file": latest.name,
        "source": source,
        "total_lines": total_lines,
        "shown_lines": len(tail),
    }