"""
Topological execution engine for the dynamic operating graph.
https://3olution.com

The engine is a single async generator, `execute()`, that:

  1. Computes the execution order via Kahn's algorithm.
  2. Runs each node in order, yielding progress events.
  3. Collects the final outputs.

Only directed acyclic graphs (DAGs) are supported. Cycles are
detected and reported as a `graph_error` event.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, AsyncGenerator

from loguru import logger

from . import registry
from .model import Graph, Node


class ExecutionContext:
    """Holds outputs and app state during a graph run."""

    def __init__(self, graph: Graph, app_state: dict) -> None:
        self.graph = graph
        self.app = app_state
        self.outputs: dict[str, dict] = {}
        self.status: dict[str, str] = {}


def _topo_sort(graph: Graph) -> list[str]:
    """
    Kahn's algorithm — returns node IDs in execution order.

    Uses `collections.deque` for the frontier so that popping the
    next node is O(1). A plain list would make `pop(0)` an O(n)
    operation, turning the whole sort into O(n²) for large graphs.

    Raises ValueError if the graph contains a cycle.
    """
    indeg: dict[str, int] = {n.id: 0 for n in graph.nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}

    for e in graph.edges:
        if e.from_node in adj and e.to_node in indeg:
            adj[e.from_node].append(e.to_node)
            indeg[e.to_node] += 1

    queue: deque[str] = deque(
        nid for nid, d in indeg.items() if d == 0
    )
    order: list[str] = []

    while queue:
        nid = queue.popleft()
        order.append(nid)
        for m in adj[nid]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    if len(order) != len(graph.nodes):
        raise ValueError(
            "Graph contains a cycle. Only DAGs are supported in v1. "
            "Use control.foreach for iteration."
        )
    return order


def _gather_inputs(graph: Graph, node: Node, ctx: ExecutionContext) -> dict:
    """Collect a node's inputs from static values and upstream outputs."""
    inputs = dict(node.inputs)
    for e in graph.edges:
        if e.to_node == node.id:
            src = ctx.outputs.get(e.from_node, {})
            inputs[e.to_input] = src.get(e.from_output)
    return inputs


def _safe(v: Any) -> Any:
    """Ensure a value is JSON-serialisable."""
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return str(v)


async def execute(graph: Graph, app_state: dict) -> AsyncGenerator[dict, None]:
    """Run a graph and yield progress events."""
    ctx = ExecutionContext(graph, app_state)
    node_by_id = {n.id: n for n in graph.nodes}

    try:
        order = _topo_sort(graph)
    except ValueError as exc:
        yield {"type": "graph_error", "error": str(exc)}
        return

    yield {"type": "graph_start", "order": order}

    for node_id in order:
        node = node_by_id[node_id]
        nt = registry.get(node.type)

        if nt is None or nt.fn is None:
            yield {
                "type": "node_error",
                "node": node_id,
                "error": f"Unknown node type: {node.type}",
            }
            continue

        yield {"type": "node_start", "node": node_id}
        inputs = _gather_inputs(graph, node, ctx)

        try:
            result = await nt.fn(ctx, inputs, node.params or {})
        except Exception as exc:
            logger.exception("Node '{}' ({}) failed", node_id, node.type)
            yield {"type": "node_error", "node": node_id, "error": str(exc)}
            ctx.outputs[node_id] = {}
            ctx.status[node_id] = "error"
            continue

        ctx.outputs[node_id] = result or {}
        ctx.status[node_id] = "done"

        preview: dict[str, str] = {}
        for k, v in (result or {}).items():
            try:
                s = json.dumps(v, default=str)
            except (TypeError, ValueError):
                s = str(v)
            preview[k] = s[:400]

        yield {"type": "node_done", "node": node_id, "outputs": preview}

    yield {
        "type": "graph_done",
        "outputs": {
            nid: {k: _safe(v) for k, v in outs.items()}
            for nid, outs in ctx.outputs.items()
        },
    }