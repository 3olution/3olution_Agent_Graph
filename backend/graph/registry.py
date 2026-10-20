"""Node type registry — maps type names to NodeType definitions."""

from .model import NodeType

_REGISTRY: dict[str, NodeType] = {}


def register(nt: NodeType) -> NodeType:
    """Register a node type. Returns the type for decorator chaining."""
    _REGISTRY[nt.name] = nt
    return nt


def get(name: str) -> NodeType | None:
    """Look up a node type by name."""
    return _REGISTRY.get(name)


def all_types() -> list[NodeType]:
    return list(_REGISTRY.values())


def serialize_all() -> list[dict]:
    """Serialise all node types for the frontend."""
    out = []
    for nt in _REGISTRY.values():
        out.append({
            "name": nt.name,
            "display": nt.display,
            "category": nt.category,
            "description": nt.description,
            "color": nt.color,
            "inputs": [
                {"name": p.name, "type": p.type, "optional": p.optional}
                for p in nt.inputs
            ],
            "outputs": [
                {"name": p.name, "type": p.type}
                for p in nt.outputs
            ],
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "default": p.default,
                    "choices": p.choices,
                    "description": p.description,
                    "dynamic": p.dynamic,
                }
                for p in nt.params
            ],
        })
    return out