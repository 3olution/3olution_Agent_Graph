"""
Auto-loader for custom graph nodes.
https://3olution.com

Every .py file in this folder (except those starting with an
underscore) is imported when the graph package is imported. Modules
that call `register(NodeType(...))` get their nodes added to the
editor's palette under whatever category they specify.

================================================================
  HOW TO ADD A CUSTOM NODE
================================================================

  1. Create a .py file here, e.g. my_nodes.py.
  2. Import the building blocks:

         from ..model import NodeType, Param, Port
         from ..registry import register

  3. Define an async function:

         async def _my_node(ctx, inputs, params) -> dict:
             return {"output_name": value}

  4. Register it:

         register(NodeType(name="custom.my_node", ...))

  5. Restart the backend (python run.py backend). The node appears
     in the palette under the category you set.

================================================================
  LOADER BEHAVIOUR
================================================================

  Each module is imported in a try/except block. If one module
  fails to load — for example because it imports a package that is
  not installed — the failure is logged with a full traceback and
  the remaining modules continue to load.

  This makes the loader safe for a folder that contains experiments
  in various states of repair.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from loguru import logger


_PACKAGE_NAME = "backend.graph.custom_nodes"


def load_all() -> None:
    """
    Import every custom node module in this folder.

    Files whose names start with an underscore are skipped, so you
    can keep private helpers next to your node definitions.
    """
    pkg_dir = Path(__file__).resolve().parent
    loaded = 0
    failed = 0

    for py_file in sorted(pkg_dir.glob("*.py")):
        stem = py_file.stem

        if stem.startswith("_"):
            continue

        module_name = f"{_PACKAGE_NAME}.{stem}"

        try:
            importlib.import_module(module_name)
            logger.info("Loaded custom node module: {}", stem)
            loaded += 1
        except Exception:
            logger.exception(
                "Failed to load custom node module '{}'. "
                "Skipping — other custom nodes will still load.",
                stem,
            )
            failed += 1

    if loaded or failed:
        logger.info(
            "Custom node loader: {} loaded, {} failed", loaded, failed,
        )