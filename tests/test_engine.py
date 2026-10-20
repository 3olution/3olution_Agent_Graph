"""
Tests for the graph execution engine.
https://3olution.com

Covers the pure-logic parts of backend/graph/engine.py:

    _topo_sort()   — Kahn's algorithm for topological sorting
    _safe()        — JSON-safety helper used when serialising outputs

The full execute() generator is not tested here because it depends
on the registry being populated with node types, the shared app
state being populated with LLM/MCP clients, and every node's
implementation being deterministic. Those dependencies are
integration-level concerns.

The topological sort is the critical algorithm — everything else
in the engine is a straightforward loop around it.
"""

from __future__ import annotations

import pytest

from backend.graph.engine import _safe, _topo_sort
from backend.graph.model import Edge, Graph, Node


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _node(node_id: str, node_type: str = "input.text") -> Node:
    """Short constructor for a Node with sensible defaults."""
    return Node(id=node_id, type=node_type)


def _edge(
    edge_id: str,
    from_node: str,
    from_output: str,
    to_node: str,
    to_input: str,
) -> Edge:
    """Short constructor for an Edge."""
    return Edge(
        id=edge_id,
        from_node=from_node,
        from_output=from_output,
        to_node=to_node,
        to_input=to_input,
    )


# ══════════════════════════════════════════════════════════════════════
#  _topo_sort — happy path
# ══════════════════════════════════════════════════════════════════════

def test_topo_sort_linear():
    """
    A three-node chain sorts in strict order: a → b → c.

    This is the simplest possible graph shape and verifies the core
    invariant: every node appears after all of its dependencies.
    """
    g = Graph(
        nodes=[
            _node("a"),
            _node("b", "llm.chat"),
            _node("c", "output.text"),
        ],
        edges=[
            _edge("e1", "a", "text", "b", "messages"),
            _edge("e2", "b", "response", "c", "text"),
        ],
    )
    assert _topo_sort(g) == ["a", "b", "c"]


def test_topo_sort_fan_out():
    """
    A single root feeding two children places the root first and
    the two children in either order after it.
    """
    g = Graph(
        nodes=[
            _node("root"),
            _node("left", "output.text"),
            _node("right", "output.text"),
        ],
        edges=[
            _edge("e1", "root", "text", "left", "text"),
            _edge("e2", "root", "text", "right", "text"),
        ],
    )
    order = _topo_sort(g)

    assert order[0] == "root"
    assert set(order[1:]) == {"left", "right"}


def test_topo_sort_fan_in():
    """
    Two roots merging into one sink places both roots before the sink.
    """
    g = Graph(
        nodes=[
            _node("a"),
            _node("b"),
            _node("merge", "control.merge"),
        ],
        edges=[
            _edge("e1", "a", "text", "merge", "a"),
            _edge("e2", "b", "text", "merge", "b"),
        ],
    )
    order = _topo_sort(g)

    assert order[-1] == "merge"
    assert set(order[:-1]) == {"a", "b"}


def test_topo_sort_diamond():
    """
    A diamond graph (a → b, a → c, b → d, c → d) has exactly one
    valid topological order for the endpoints: a first, d last.
    """
    g = Graph(
        nodes=[
            _node("a"),
            _node("b"),
            _node("c"),
            _node("d"),
        ],
        edges=[
            _edge("e1", "a", "text", "b", "text"),
            _edge("e2", "a", "text", "c", "text"),
            _edge("e3", "b", "text", "d", "a"),
            _edge("e4", "c", "text", "d", "b"),
        ],
    )
    order = _topo_sort(g)

    assert order[0] == "a"
    assert order[-1] == "d"
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_topo_sort_disconnected():
    """
    A graph with two disconnected components still sorts every node.

    Kahn's algorithm handles disconnected graphs naturally: every
    node with indegree 0 is enqueued as a starting point.
    """
    g = Graph(
        nodes=[
            _node("a"),
            _node("b"),
            _node("x"),
            _node("y"),
        ],
        edges=[
            _edge("e1", "a", "text", "b", "text"),
            _edge("e2", "x", "text", "y", "text"),
        ],
    )
    order = _topo_sort(g)

    assert len(order) == 4
    assert order.index("a") < order.index("b")
    assert order.index("x") < order.index("y")


def test_topo_sort_single_node():
    """
    A single node with no edges is trivially sortable.
    """
    g = Graph(nodes=[_node("only")], edges=[])
    assert _topo_sort(g) == ["only"]


def test_topo_sort_empty():
    """
    An empty graph sorts to an empty list.
    """
    g = Graph(nodes=[], edges=[])
    assert _topo_sort(g) == []


# ══════════════════════════════════════════════════════════════════════
#  _topo_sort — error cases
# ══════════════════════════════════════════════════════════════════════

def test_topo_sort_cycle_raises():
    """
    A two-node cycle (a ↔ b) raises ValueError.

    The error message must contain the substring "cycle" so callers
    can detect this specific failure mode without string-matching
    the whole message.
    """
    g = Graph(
        nodes=[_node("a"), _node("b", "llm.chat")],
        edges=[
            _edge("e1", "a", "text", "b", "messages"),
            _edge("e2", "b", "response", "a", "text"),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        _topo_sort(g)


def test_topo_sort_self_loop_raises():
    """
    A self-loop (a → a) is a cycle of length 1.
    """
    g = Graph(
        nodes=[_node("a")],
        edges=[_edge("e1", "a", "text", "a", "text")],
    )
    with pytest.raises(ValueError, match="cycle"):
        _topo_sort(g)


def test_topo_sort_three_node_cycle_raises():
    """
    A longer cycle (a → b → c → a) is still detected.
    """
    g = Graph(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[
            _edge("e1", "a", "text", "b", "text"),
            _edge("e2", "b", "text", "c", "text"),
            _edge("e3", "c", "text", "a", "text"),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        _topo_sort(g)


def test_topo_sort_ignores_dangling_edges():
    """
    Edges that reference unknown nodes are ignored.

    This is a defensive behaviour: Graph.from_dict() already drops
    such edges, but _topo_sort re-checks to avoid a KeyError if the
    Graph is constructed directly.
    """
    g = Graph(
        nodes=[_node("a")],
        edges=[
            _edge("e1", "a", "text", "ghost", "text"),
            _edge("e2", "phantom", "text", "a", "text"),
        ],
    )
    assert _topo_sort(g) == ["a"]


# ══════════════════════════════════════════════════════════════════════
#  _safe — JSON serialisation helper
# ══════════════════════════════════════════════════════════════════════

def test_safe_primitive():
    """Primitives pass through unchanged."""
    assert _safe("hello") == "hello"
    assert _safe(42) == 42
    assert _safe(3.14) == 3.14
    assert _safe(True) is True
    assert _safe(None) is None


def test_safe_container():
    """JSON-serialisable containers pass through unchanged."""
    assert _safe([1, 2, 3]) == [1, 2, 3]
    assert _safe({"a": 1, "b": 2}) == {"a": 1, "b": 2}


def test_safe_unserialisable():
    """
    Objects that json.dumps cannot serialise are converted to str().

    This is what keeps the graph_done event from raising when a
    custom node returns a datetime or a custom class instance.
    """
    from datetime import datetime, timezone

    dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    result = _safe(dt)

    assert isinstance(result, str)
    assert "2025" in result


def test_safe_custom_object():
    """A user-defined class instance is stringified."""
    class Widget:
        def __str__(self) -> str:
            return "<widget>"

    assert _safe(Widget()) == "<widget>"