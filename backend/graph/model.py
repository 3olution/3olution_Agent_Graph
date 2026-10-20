"""Data model for the dynamic operating graph."""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Port:
    """An input or output connection point on a node."""
    name: str
    type: str = "any"
    optional: bool = False


@dataclass
class Param:
    """A configuration parameter shown as a widget on the node."""
    name: str
    type: str = "string"
    default: Any = None
    choices: list | None = None
    description: str = ""
    dynamic: str | None = None


NodeFn = Callable[[Any, dict, dict], Awaitable[dict]]


@dataclass
class NodeType:
    """Definition of a node type in the registry."""
    name: str
    display: str
    category: str
    description: str = ""
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    color: str = "#2d3748"
    fn: NodeFn | None = None


@dataclass
class Node:
    """An instance of a node type in a graph."""
    id: str
    type: str
    inputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    position: tuple = (0, 0)
    title: str = ""


@dataclass
class Edge:
    """A connection from one node's output to another node's input."""
    id: str
    from_node: str
    from_output: str
    to_node: str
    to_input: str


@dataclass
class Graph:
    """A complete graph definition."""
    id: str = "default"
    name: str = "Untitled"
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        node_fields = {"id", "type", "inputs", "params", "position", "title"}
        edge_fields = {"id", "from_node", "from_output", "to_node", "to_input"}

        nodes = []
        for n in data.get("nodes", []):
            filtered = {k: v for k, v in n.items() if k in node_fields}
            if "pos" in n and "position" not in filtered:
                filtered["position"] = tuple(n["pos"])
            nodes.append(Node(**filtered))

        edges = [
            Edge(**{k: v for k, v in e.items() if k in edge_fields})
            for e in data.get("edges", [])
        ]

        return cls(
            id=data.get("id", "default"),
            name=data.get("name", "Untitled"),
            nodes=nodes,
            edges=edges,
        )