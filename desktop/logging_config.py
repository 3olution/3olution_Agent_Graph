"""
Logging setup for the desktop application.
https://3olution.com

Mirrors the backend's logging setup:

    - A console sink at INFO level for the terminal
    - A rotating file sink at DEBUG level for diagnostics
    - Files written to the same `logs/` folder as the backend,
      with a `desktop-` prefix instead of `backend-`

================================================================
  WHY A SEPARATE FILE FROM backend/logging_config.py
================================================================

  The backend's logging_config imports backend.config, which in
  turn imports pydantic-settings. The desktop client does NOT need
  to pull in the whole backend config chain just to write logs —
  the values it needs (logs dir, retention days) are small and can
  be passed in as arguments.

  Keeping the two files separate also means the desktop client can
  be packaged (PyInstaller) without including the backend config
  module, shaving a small amount off the bundle.

================================================================
  ROTATION AND RETENTION
================================================================

  Files rotate at midnight UTC and are named:

      logs/desktop-YYYY-MM-DD.log

  Retention is 14 days, matching the backend. The retention policy
  is enforced per loguru sink, so the desktop log and backend log
  are pruned independently.

================================================================
  SAFE ON MULTIPLE LAUNCHES
================================================================

  The desktop client is single-instance by design — the Qt
  application model does not support multiple GUI processes
  sharing the same window state. But if a user does launch two
  copies, loguru's `enqueue=True` and Python's append mode make
  both writers cooperate without corrupting the file. Lines may
  interleave, but no data is lost.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_desktop_logging(
    log_dir: Path,
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    retention_days: int = 14,
) -> None:
    """
    Configure loguru for the desktop application.

    Removes any existing handlers (including the default stderr
    sink that loguru installs at import time) so the desktop
    client's logging is self-contained.

    Parameters
    ----------
    log_dir
        Directory for log files. Created if missing.
    console_level
        Minimum level written to stderr. The desktop client runs
        with a visible terminal during development; users who
        launch the packaged executable see nothing on stderr
        (console=False in the PyInstaller spec), which is fine.
    file_level
        Minimum level written to the log file. Defaults to DEBUG
        so that user bug reports contain full diagnostics.
    retention_days
        Number of daily log files to keep before pruning.
    """
    # Remove every existing handler (there is exactly one by default).
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
        level=console_level.upper(),
        format=console_format,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    # ── File sink ────────────────────────────────────────────────────
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "desktop-{time:YYYY-MM-DD}.log"

        file_format = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{line} — {message}"
        )
        logger.add(
            str(log_file),
            rotation="00:00",
            retention=f"{retention_days} days",
            level=file_level.upper(),
            format=file_format,
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )
        logger.info("Desktop logging enabled at {}", log_dir)
    except Exception as exc:
        # A read-only filesystem or permission error should not
        # prevent the desktop client from running — it degrades to
        # console-only logging.
        logger.warning(
            "Could not enable desktop file logging at {}: {}",
            log_dir,
            exc,
        )