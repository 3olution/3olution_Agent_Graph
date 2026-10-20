"""
Shared application state.

Populated by the FastAPI lifespan and consumed by routes, the graph
engine, and the agent loop. Avoids circular imports.
"""

from typing import Any

state: dict[str, Any] = {}