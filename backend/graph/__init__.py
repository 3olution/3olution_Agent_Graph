"""
Graph engine package.
https://3olution.com

On import, this module:

  1. Registers every built-in node type defined in nodes.py.
  2. Loads every custom node module found in custom_nodes/ unless
     AGENT_LOAD_CUSTOM_NODES is false.

The custom-node loader mirrors the pattern used by the MCP tool
loader — drop a .py file in custom_nodes/, restart the backend, and
its nodes appear in the graph editor's palette.

================================================================
  LOAD ORDER
================================================================

  Built-in nodes are loaded first. Custom nodes are loaded second,
  so a custom node with the same name as a built-in can override it
  (this is deliberate — custom overrides built-in).

  The custom-node loader wraps each module import in a try/except,
  so a broken custom node does not prevent the rest from loading.
  The failure is logged at ERROR level with a full traceback.
"""

from __future__ import annotations

from loguru import logger

from . import nodes  # noqa: F401  (registers built-in node types)
from ..config import settings


def _load_custom_nodes() -> None:
    """
    Import every custom node module in custom_nodes/.

    Skipped entirely when AGENT_LOAD_CUSTOM_NODES is false. Useful
    when a broken custom node is blocking backend startup.
    """
    if not settings.load_custom_nodes:
        logger.info(
            "Custom node loading disabled "
            "(AGENT_LOAD_CUSTOM_NODES=false)"
        )
        return

    try:
        from .custom_nodes import load_all
        load_all()
    except Exception:
        logger.exception(
            "Custom node loading failed. The backend will continue "
            "with built-in nodes only. Fix the custom node file and "
            "restart the backend."
        )


_load_custom_nodes()