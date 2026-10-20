"""
Auto-loader for MCP tool modules.
https://3olution.com

Every non-underscore .py file in this folder is imported when the
tools package is imported. Each tool module is expected to:

    1. Import the shared instance:  from .._server import mcp
    2. Decorate its functions with @mcp.tool()

The decorator registers the tool on the shared instance. There is no
explicit `load()` call — the import of this package does the work.

================================================================
  LOADER BEHAVIOUR
================================================================

  Failures are isolated. If one tool module fails to import (missing
  dependency, syntax error, bad import), the loader logs a full
  traceback and continues loading the remaining modules. The
  backend will see fewer tools than expected, but it will not
  crash.

  This design means a broken experimental tool cannot brick the
  whole server. You can comment out or rename the offending file
  to remove it from the palette.

================================================================
  FILE NAMING CONVENTION
================================================================

  Files whose names start with an underscore (_private.py) are
  skipped. Use this for shared helpers that are not themselves
  tool definitions:

      tools/
          __init__.py         ← this file, auto-runs on import
          README.md           ← documentation
          math_tools.py       ← loaded
          time_tools.py       ← loaded
          text_tools.py       ← loaded
          _helpers.py         ← skipped (leading underscore)
"""

from __future__ import annotations

import importlib
from pathlib import Path

from loguru import logger


_PACKAGE = "mcp_server.tools"


def load_all() -> None:
    """
    Import every tool module in this folder.

    Idempotent: a second call re-imports the same modules, which
    Python's import system treats as a no-op. Safe to call from
    tests.
    """
    pkg_dir = Path(__file__).resolve().parent
    loaded = 0
    failed = 0

    for py_file in sorted(pkg_dir.glob("*.py")):
        stem = py_file.stem

        # Skip __init__.py and any explicitly-private module.
        if stem.startswith("_"):
            continue

        module_name = f"{_PACKAGE}.{stem}"

        try:
            importlib.import_module(module_name)
            logger.info("Loaded MCP tool module: {}", stem)
            loaded += 1
        except Exception:
            logger.exception(
                "Failed to load MCP tool module '{}'. "
                "Skipping — other tool modules will still load.",
                stem,
            )
            failed += 1

    if loaded or failed:
        logger.info(
            "MCP tool loader: {} loaded, {} failed", loaded, failed,
        )


# ══════════════════════════════════════════════════════════════════════
#  Run the loader on package import
# ══════════════════════════════════════════════════════════════════════
#
#  This side effect is what makes `from . import tools` sufficient
#  in server.py. No explicit call needed.

load_all()