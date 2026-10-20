"""
Logging setup for the backend using loguru.
https://3olution.com

Two sinks are configured:

  Console (stderr) — level AGENT_LOG_LEVEL (default INFO)
  File (rotating)  — level AGENT_LOG_FILE_LEVEL (default DEBUG)

The console sink is meant for a human operator watching the terminal.
The file sink captures everything at DEBUG level so that a bug report
can be diagnosed after the fact without re-enabling verbose logging.

================================================================
  ROTATION AND RETENTION
================================================================
#
  Log files rotate at midnight UTC and are named:

      logs/backend-YYYY-MM-DD.log

  Retention is AGENT_LOG_RETENTION_DAYS (default 14). Files older
  than that are deleted automatically.

================================================================
  FILE HANDLING ON WINDOWS
================================================================
#
  The file sink uses `enqueue=True`, which routes every log call
  through a background thread. This makes logging safe to call from
  asyncio without blocking the event loop, and it also avoids the
  classic Windows issue where an antivirus scanner holds the file
  open while Python tries to rotate it.

  If the process is killed with SIGKILL (or Ctrl+Break on Windows),
  some log lines still in the queue may be lost. This is acceptable:
  the alternative — synchronous file writes on every log line — is
  a significant performance cost on the hot path.
"""

from __future__ import annotations

import sys

from loguru import logger

from .config import settings


def setup_logging() -> None:
    """
    Configure loguru. Safe to call multiple times — the first call
    removes the default handler, subsequent calls are no-ops because
    `logger.remove()` clears everything.
    """
    logger.remove()

    # ── Console sink ─────────────────────────────────────────────────
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    )
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        format=console_format,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    # ── File sink ────────────────────────────────────────────────────
    if settings.log_to_file:
        _setup_file_sink()

    logger.info(
        "Logging initialised (console={}, file={})",
        settings.log_level.upper(),
        settings.log_file_level.upper() if settings.log_to_file else "off",
    )


def _setup_file_sink() -> None:
    """
    Add the rotating file sink.

    If the logs directory cannot be created (read-only filesystem,
    permission denied), the file sink is skipped and a warning is
    logged. The app continues with console-only logging.
    """
    try:
        log_dir = settings.logs_path
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "backend-{time:YYYY-MM-DD}.log"

        file_format = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{line} — {message}"
        )

        logger.add(
            str(log_file),
            rotation="00:00",                       # rotate at midnight
            retention=f"{settings.log_retention_days} days",
            level=settings.log_file_level.upper(),
            format=file_format,
            encoding="utf-8",
            enqueue=True,                           # thread-safe
            backtrace=True,
            diagnose=False,                         # no local var dumps
        )
    except Exception as exc:
        logger.warning(
            "Could not enable file logging at {}: {}",
            settings.logs_path,
            exc,
        )