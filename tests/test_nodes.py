"""
Tests for the built-in graph node implementations.
https://3olution.com

Each test instantiates a node via the registry and calls its
function directly. This bypasses the engine, the LLM client, and
the MCP client — the goal is to verify the node's pure logic.

Nodes that call the LLM or MCP (llm.chat, tool.list, tool.call,
llm_json in custom_nodes) are not tested here because they require
external services to be running. A future integration-test file
could cover them with mocks.
"""

from __future__ import annotations

import json

import pytest

from backend.graph.registry import get


# ══════════════════════════════════════════════════════════════════════
#  Registry
# ══════════════════════════════════════════════════════════════════════

def test_registry_has_builtins():
    """
    Importing backend.graph must register every built-in node type.

    This is the canary test: if the registry ends up empty, every
    other test in this file fails with a confusing message. Asserting
    the count here makes the failure obvious.
    """
    from backend.graph import registry

    all_types = registry.all_types()
    assert len(all_types) >= 16, (
        f"Expected at least 16 built-in node types, "
        f"found {len(all_types)}"
    )

    names = {nt.name for nt in all_types}
    assert "input.text" in names
    assert "input.number" in names
    assert "input.json" in names
    assert "output.text" in names
    assert "llm.prompt" in names
    assert "llm.chat" in names
    assert "tool.list" in names
    assert "tool.call" in names
    assert "text.template" in names
    assert "text.join" in names
    assert "control.branch" in names
    assert "control.merge" in names
    assert "control.foreach" in names
    assert "util.constant" in names
    assert "util.log" in names
    assert "util.delay" in names


def test_registry_lookup_missing():
    """Looking up an unknown type returns None."""
    assert get("does.not.exist") is None


# ══════════════════════════════════════════════════════════════════════
#  Input nodes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_input_text():
    nt = get("input.text")
    assert nt is not None

    result = await nt.fn(None, {}, {"value": "hello world"})
    assert result == {"text": "hello world"}


@pytest.mark.asyncio
async def test_input_text_default():
    """Missing params fall back to an empty string."""
    nt = get("input.text")
    result = await nt.fn(None, {}, {})
    assert result == {"text": ""}


@pytest.mark.asyncio
async def test_input_number():
    nt = get("input.number")
    result = await nt.fn(None, {}, {"value": 42})
    assert result == {"number": 42.0}
    assert isinstance(result["number"], float)


@pytest.mark.asyncio
async def test_input_number_string_coercion():
    """String input is coerced to float."""
    nt = get("input.number")
    result = await nt.fn(None, {}, {"value": "3.14"})
    assert result == {"number": 3.14}


@pytest.mark.asyncio
async def test_input_json_valid():
    nt = get("input.json")
    result = await nt.fn(None, {}, {"value": '{"a": 1, "b": [2, 3]}'})
    assert result == {"data": {"a": 1, "b": [2, 3]}}


@pytest.mark.asyncio
async def test_input_json_invalid():
    """Invalid JSON raises ValueError, which the engine catches."""
    nt = get("input.json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        await nt.fn(None, {}, {"value": "not json"})


# ══════════════════════════════════════════════════════════════════════
#  Output nodes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_output_text():
    nt = get("output.text")
    result = await nt.fn(None, {"text": "final answer"}, {})
    assert result == {"result": "final answer"}


@pytest.mark.asyncio
async def test_output_text_none():
    """A missing input becomes an empty string, not None."""
    nt = get("output.text")
    result = await nt.fn(None, {}, {})
    assert result == {"result": ""}


@pytest.mark.asyncio
async def test_output_text_coerces():
    """Non-string inputs are stringified."""
    nt = get("output.text")
    result = await nt.fn(None, {"text": 42}, {})
    assert result == {"result": "42"}


# ══════════════════════════════════════════════════════════════════════
#  LLM nodes (pure logic only)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_llm_prompt_system_and_user():
    nt = get("llm.prompt")
    result = await nt.fn(
        None,
        {"system": "You are helpful.", "user": "Hi"},
        {},
    )
    assert result == {
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
    }


@pytest.mark.asyncio
async def test_llm_prompt_user_only():
    """A missing system prompt is simply omitted from the list."""
    nt = get("llm.prompt")
    result = await nt.fn(None, {"user": "Hi"}, {})
    assert result == {
        "messages": [{"role": "user", "content": "Hi"}],
    }


@pytest.mark.asyncio
async def test_llm_prompt_from_params():
    """Params fill in when inputs are absent."""
    nt = get("llm.prompt")
    result = await nt.fn(
        None,
        {},
        {"system": "Sys", "user": "Usr"},
    )
    assert result == {
        "messages": [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Usr"},
        ]
    }


@pytest.mark.asyncio
async def test_llm_prompt_empty():
    """Both missing produces an empty message list."""
    nt = get("llm.prompt")
    result = await nt.fn(None, {}, {})
    assert result == {"messages": []}


# ══════════════════════════════════════════════════════════════════════
#  Text nodes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_text_template_basic():
    nt = get("text.template")
    result = await nt.fn(
        None,
        {"vars": {"name": "World"}},
        {"template": "Hello {name}!"},
    )
    assert result == {"text": "Hello World!"}


@pytest.mark.asyncio
async def test_text_template_missing_key():
    """A missing key raises a clear ValueError."""
    nt = get("text.template")
    with pytest.raises(ValueError, match="Template error"):
        await nt.fn(
            None,
            {"vars": {"name": "World"}},
            {"template": "Hello {missing}!"},
        )


@pytest.mark.asyncio
async def test_text_template_non_dict_vars():
    """A scalar vars input is wrapped in {'value': ...}."""
    nt = get("text.template")
    result = await nt.fn(
        None,
        {"vars": "Alice"},
        {"template": "Hi {value}"},
    )
    assert result == {"text": "Hi Alice"}


@pytest.mark.asyncio
async def test_text_join_default_separator():
    nt = get("text.join")
    result = await nt.fn(
        None,
        {"items": ["a", "b", "c"]},
        {},
    )
    assert result == {"text": "a\nb\nc"}


@pytest.mark.asyncio
async def test_text_join_custom_separator():
    nt = get("text.join")
    result = await nt.fn(
        None,
        {"items": [1, 2, 3]},
        {"separator": ", "},
    )
    assert result == {"text": "1, 2, 3"}


@pytest.mark.asyncio
async def test_text_join_empty():
    nt = get("text.join")
    result = await nt.fn(None, {"items": []}, {})
    assert result == {"text": ""}


# ══════════════════════════════════════════════════════════════════════
#  Control nodes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_control_branch_true():
    nt = get("control.branch")
    result = await nt.fn(
        None,
        {"condition": True, "value": "yes"},
        {},
    )
    assert result == {"true": "yes", "false": None}


@pytest.mark.asyncio
async def test_control_branch_false():
    nt = get("control.branch")
    result = await nt.fn(
        None,
        {"condition": False, "value": "yes"},
        {},
    )
    assert result == {"true": None, "false": "yes"}


@pytest.mark.asyncio
async def test_control_branch_string_condition():
    """Common truthy/falsy strings are recognised."""
    nt = get("control.branch")

    for truthy in ("true", "TRUE", "1", "yes", "y"):
        result = await nt.fn(
            None, {"condition": truthy, "value": "x"}, {},
        )
        assert result["true"] == "x", f"{truthy!r} should be truthy"

    for falsy in ("false", "0", "no", "n", ""):
        result = await nt.fn(
            None, {"condition": falsy, "value": "x"}, {},
        )
        assert result["false"] == "x", f"{falsy!r} should be falsy"


@pytest.mark.asyncio
async def test_control_merge_first():
    nt = get("control.merge")
    result = await nt.fn(None, {"a": "first", "b": "second"}, {})
    assert result == {"value": "first"}


@pytest.mark.asyncio
async def test_control_merge_second_when_first_none():
    nt = get("control.merge")
    result = await nt.fn(None, {"a": None, "b": "second"}, {})
    assert result == {"value": "second"}


@pytest.mark.asyncio
async def test_control_merge_both_none():
    nt = get("control.merge")
    result = await nt.fn(None, {"a": None, "b": None}, {})
    assert result == {"value": None}


@pytest.mark.asyncio
async def test_control_foreach_basic():
    """
    control.foreach runs its inner op once per item.

    We use util.constant as the inner op — it just returns a fixed
    value, so we can verify the loop count and structure without
    depending on any external service.
    """
    nt = get("control.foreach")
    result = await nt.fn(
        None,
        {"items": [1, 2, 3]},
        {"op": "input.number", "op_params": {"value": 99}},
    )
    assert "results" in result
    assert len(result["results"]) == 3
    for r in result["results"]:
        assert r == {"number": 99.0}


@pytest.mark.asyncio
async def test_control_foreach_non_list():
    """A scalar items input is wrapped in a one-element list."""
    nt = get("control.foreach")
    result = await nt.fn(
        None,
        {"items": 42},
        {"op": "input.number", "op_params": {}},
    )
    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_control_foreach_requires_op():
    """A missing op param is a clear error."""
    nt = get("control.foreach")
    with pytest.raises(ValueError, match="'op' param required"):
        await nt.fn(None, {"items": [1, 2]}, {})


@pytest.mark.asyncio
async def test_control_foreach_unknown_op():
    """An unregistered op name is a clear error."""
    nt = get("control.foreach")
    with pytest.raises(ValueError, match="unknown op"):
        await nt.fn(
            None,
            {"items": [1, 2]},
            {"op": "not.a.real.node"},
        )


# ══════════════════════════════════════════════════════════════════════
#  Utility nodes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_util_constant():
    nt = get("util.constant")
    result = await nt.fn(None, {}, {"value": {"nested": [1, 2, 3]}})
    assert result == {"value": {"nested": [1, 2, 3]}}


@pytest.mark.asyncio
async def test_util_constant_none():
    nt = get("util.constant")
    result = await nt.fn(None, {}, {})
    assert result == {"value": None}


@pytest.mark.asyncio
async def test_util_log_passthrough(capsys):
    """util.log prints and passes through unchanged."""
    nt = get("util.log")
    result = await nt.fn(None, {"value": "test message"}, {})
    assert result == {"value": "test message"}

    captured = capsys.readouterr()
    assert "test message" in captured.out


@pytest.mark.asyncio
async def test_util_delay_passthrough():
    """util.delay waits then passes the value through."""
    nt = get("util.delay")
    result = await nt.fn(None, {"value": "after delay"}, {"seconds": 0.01})
    assert result == {"value": "after delay"}


# ══════════════════════════════════════════════════════════════════════
#  Graph.from_dict — shape conversion
# ══════════════════════════════════════════════════════════════════════

def test_graph_from_dict_basic():
    """Standard serialised format round-trips correctly."""
    from backend.graph.model import Graph

    payload = {
        "id": "test",
        "name": "Test",
        "nodes": [
            {
                "id": "n1",
                "type": "input.text",
                "inputs": {},
                "params": {"value": "hi"},
                "position": [100, 200],
                "title": "Text",
            },
        ],
        "edges": [],
    }
    g = Graph.from_dict(payload)

    assert g.id == "test"
    assert g.name == "Test"
    assert len(g.nodes) == 1
    assert g.nodes[0].id == "n1"
    assert g.nodes[0].position == (100, 200)


def test_graph_from_dict_accepts_pos():
    """The LiteGraph spelling 'pos' is accepted as an alias."""
    from backend.graph.model import Graph

    payload = {
        "nodes": [
            {
                "id": "n1",
                "type": "input.text",
                "pos": [300, 400],
            },
        ],
    }
    g = Graph.from_dict(payload)

    assert g.nodes[0].position == (300, 400)


def test_graph_from_dict_drops_dangling_edges():
    """Edges referencing unknown nodes are silently dropped."""
    from backend.graph.model import Graph

    payload = {
        "nodes": [{"id": "n1", "type": "input.text"}],
        "edges": [
            {
                "id": "e1",
                "from_node": "n1",
                "from_output": "text",
                "to_node": "ghost",
                "to_input": "text",
            },
        ],
    }
    g = Graph.from_dict(payload)
    assert len(g.edges) == 0


def test_graph_from_dict_drops_unknown_fields():
    """Extra keys in nodes and edges are silently dropped."""
    from backend.graph.model import Graph

    payload = {
        "nodes": [
            {
                "id": "n1",
                "type": "input.text",
                "some_random_field": "should be dropped",
            },
        ],
        "edges": [
            {
                "id": "e1",
                "from_node": "n1",
                "from_output": "text",
                "to_node": "n1",
                "to_input": "text",
                "extra": "dropped",
            },
        ],
    }
    # This should not raise, even though some_random_field is not
    # a valid Node field.
    g = Graph.from_dict(payload)
    assert len(g.nodes) == 1
    assert not hasattr(g.nodes[0], "some_random_field")