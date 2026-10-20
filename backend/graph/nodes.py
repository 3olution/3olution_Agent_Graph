"""
Built-in node implementations for the 3olution Agent Graph.
https://3olution.com

This module is imported once at package load time and registers every
built-in node type on the shared registry. See backend/graph/model.py
for the NodeType, Param, and Port dataclasses, and
backend/graph/registry.py for the registration API.

================================================================
  NODE CATALOGUE
================================================================

    Input      input.text, input.number, input.json
    Output     output.text
    LLM        llm.prompt, llm.chat
    Tools      tool.list, tool.call
    Text       text.template, text.join
    Control    control.branch, control.merge, control.foreach
    Utility    util.constant, util.log, util.delay

================================================================
  THE llm.chat NODE AND PROVIDER ROUTING
================================================================

  The llm.chat node is the only node that talks to an LLM. It exposes
  three widgets:

    model          — a dynamic combo populated from /api/llama/all-models.
                     Values are encoded as `provider::model` (e.g.
                     "ollama::llama3.2" or "llama_cpp::foo.Q4_K_M.gguf").
                     An empty value or "(default)" uses AGENT_LLM_MODEL.

    provider       — a fixed select (auto | ollama | llama_cpp | fallback).
                     Used as the fallback if the model string does not
                     encode a provider. Ignored when it does.

    disable_tools  — a toggle. Strips the `tools` parameter from this
                     single call, regardless of the global setting.

  The node function decodes the model string, resolves the effective
  provider, and forwards everything to LLMClient.chat(). This makes
  the graph editor capable of routing individual LLM nodes to the
  local GGUF server, the fallback endpoint, or the primary endpoint,
  independently.
"""

from __future__ import annotations

import json

from .model import NodeType, Param, Port
from .registry import register


# ═══════════════════════════════════════════════════════════════════════
#  INPUT NODES
# ═══════════════════════════════════════════════════════════════════════

async def _input_text(ctx, inputs, params):
    return {"text": params.get("value", "")}


register(NodeType(
    name="input.text",
    display="Text Input",
    category="Input",
    description="A literal string value.",
    outputs=[Port("text", "string")],
    params=[Param("value", "text", "", description="The string to output")],
    color="#1e3a5f",
    fn=_input_text,
))


async def _input_number(ctx, inputs, params):
    return {"number": float(params.get("value", 0))}


register(NodeType(
    name="input.number",
    display="Number Input",
    category="Input",
    description="A numeric literal.",
    outputs=[Port("number", "number")],
    params=[Param("value", "number", 0)],
    color="#1e3a5f",
    fn=_input_number,
))


async def _input_json(ctx, inputs, params):
    try:
        data = json.loads(params.get("value", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    return {"data": data}


register(NodeType(
    name="input.json",
    display="JSON Input",
    category="Input",
    description="A JSON literal, parsed into a Python object.",
    outputs=[Port("data", "any")],
    params=[Param("value", "text", "{}")],
    color="#1e3a5f",
    fn=_input_json,
))


# ═══════════════════════════════════════════════════════════════════════
#  OUTPUT NODES
# ═══════════════════════════════════════════════════════════════════════

async def _output_text(ctx, inputs, params):
    text = inputs.get("text")
    return {"result": str(text) if text is not None else ""}


register(NodeType(
    name="output.text",
    display="Text Output",
    category="Output",
    description="Terminal sink. Marks the final result of the graph.",
    inputs=[Port("text", "string")],
    color="#5f1e2e",
    fn=_output_text,
))


# ═══════════════════════════════════════════════════════════════════════
#  LLM NODES
# ═══════════════════════════════════════════════════════════════════════

async def _llm_prompt(ctx, inputs, params):
    system = inputs.get("system") or params.get("system", "")
    user = inputs.get("user") or params.get("user", "")
    messages = []
    if system:
        messages.append({"role": "system", "content": str(system)})
    if user:
        messages.append({"role": "user", "content": str(user)})
    return {"messages": messages}


register(NodeType(
    name="llm.prompt",
    display="Prompt Builder",
    category="LLM",
    description="Compose a chat message list from system and user prompts.",
    inputs=[Port("system", "string", optional=True), Port("user", "string")],
    outputs=[Port("messages", "messages")],
    params=[Param("system", "text", ""), Param("user", "text", "")],
    color="#3b2a5f",
    fn=_llm_prompt,
))


# ─────────────────────────────────────────────────────────────────────
#  Decoding helper for the llm.chat node
# ─────────────────────────────────────────────────────────────────────

def _decode_model_and_provider(
    raw_model: str | None,
    raw_provider: str | None,
) -> tuple[str | None, str]:
    """
    Return (model, provider) from the two widget values.

    The `model` widget can hold three things:

        ""                    → no model; use AGENT_LLM_MODEL
        "(default)"           → same as above
        "provider::model"     → the encoded form the frontend sends
        "model"               → a bare model name (legacy / hand-edited)

    When the encoded form is present, the provider it carries wins over
    the `provider` widget. This is deliberate: the user picked a
    specific model from a specific provider, so that choice should not
    be silently overridden by a stale widget value.

    The returned provider is always a lowercase string; it may be
    "auto", "ollama", "llama_cpp", or "fallback". Empty values fall
    back to "auto".
    """
    provider = (raw_provider or "").strip().lower() or "auto"
    model: str | None = None

    raw = (raw_model or "").strip()
    if raw and raw not in ("(default)",):
        if "::" in raw:
            prov_part, model_part = raw.split("::", 1)
            prov_part = prov_part.strip().lower()
            model_part = model_part.strip()
            if prov_part:
                provider = prov_part
            if model_part:
                model = model_part
        else:
            model = raw

    return model, provider


async def _llm_chat(ctx, inputs, params):
    """
    Send a message list to an LLM and return the reply.

    Supports all three providers:

        primary   (ollama, openai, lm-studio, …)
        fallback  (an optional secondary endpoint)
        llama_cpp (the local llama-cpp-python server)

    The provider is resolved from the `model` widget's encoded prefix,
    falling back to the `provider` widget, falling back to "auto".
    """
    llm = ctx.app.get("llm")
    if llm is None:
        raise RuntimeError("LLM client not available in app state.")

    # ── Build the message list ───────────────────────────────────────
    messages = inputs.get("messages")
    if not messages:
        messages = params.get("messages") or [
            {"role": "user", "content": "Hello"}
        ]
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    # ── Decode provider and model ────────────────────────────────────
    model, provider = _decode_model_and_provider(
        params.get("model"),
        params.get("provider"),
    )

    # ── Resolve tools ────────────────────────────────────────────────
    tools = inputs.get("tools") or None
    disable_tools = bool(params.get("disable_tools", False))

    # ── Call the LLM ─────────────────────────────────────────────────
    response = await llm.chat(
        messages,
        tools=tools,
        model=model,
        provider=provider,
        disable_tools=disable_tools,
    )

    msg = response.choices[0].message

    tool_calls = []
    for tc in (msg.tool_calls or []):
        tool_calls.append({
            "id": tc.id,
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        })

    return {
        "response": msg.content or "",
        "tool_calls": tool_calls,
        "raw": msg.model_dump(exclude_none=True),
    }


register(NodeType(
    name="llm.chat",
    display="LLM Chat",
    category="LLM",
    description=(
        "Send a message list to a configured LLM endpoint and return "
        "the reply. Supports primary, fallback, and local llama-cpp "
        "providers, selected per node."
    ),
    inputs=[
        Port("messages", "messages"),
        Port("tools", "tools", optional=True),
    ],
    outputs=[
        Port("response", "string"),
        Port("tool_calls", "list"),
        Port("raw", "any"),
    ],
    params=[
        Param(
            name="model",
            type="select",
            default="",
            dynamic="all.models",
            description=(
                "Model to use. The dropdown lists every model from "
                "Ollama and every local GGUF file. The value is "
                "encoded as 'provider::model'. '(default)' uses "
                "AGENT_LLM_MODEL."
            ),
        ),
        Param(
            name="provider",
            type="select",
            default="auto",
            choices=["auto", "ollama", "llama_cpp", "fallback"],
            description=(
                "Provider to use when the model value does not encode "
                "one. 'auto' uses the primary endpoint."
            ),
        ),
        Param(
            name="disable_tools",
            type="bool",
            default=False,
            description=(
                "Strip the tools parameter from this call, regardless "
                "of the global AGENT_DISABLE_TOOLS setting."
            ),
        ),
    ],
    color="#3b2a5f",
    fn=_llm_chat,
))


# ═══════════════════════════════════════════════════════════════════════
#  TOOL NODES
# ═══════════════════════════════════════════════════════════════════════

async def _tool_list(ctx, inputs, params):
    mcp = ctx.app.get("mcp")
    if mcp is None:
        raise RuntimeError("MCP client not available in app state.")
    return {"tools": await mcp.list_tools_openai()}


register(NodeType(
    name="tool.list",
    display="List MCP Tools",
    category="Tools",
    description="Return all MCP tools in OpenAI function-calling schema.",
    outputs=[Port("tools", "tools")],
    color="#1e5f4d",
    fn=_tool_list,
))


async def _tool_call(ctx, inputs, params):
    mcp = ctx.app.get("mcp")
    if mcp is None:
        raise RuntimeError("MCP client not available in app state.")

    name = inputs.get("name") or params.get("name", "")
    args = inputs.get("args")
    if args is None:
        args = params.get("args", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    result = await mcp.call_tool(name, args or {})
    return {"result": result, "name": name}


register(NodeType(
    name="tool.call",
    display="Call MCP Tool",
    category="Tools",
    description="Invoke an MCP tool by name with JSON arguments.",
    inputs=[Port("name", "string"), Port("args", "any", optional=True)],
    outputs=[Port("result", "string"), Port("name", "string")],
    params=[Param("name", "string", ""), Param("args", "json", {})],
    color="#1e5f4d",
    fn=_tool_call,
))


# ═══════════════════════════════════════════════════════════════════════
#  TEXT NODES
# ═══════════════════════════════════════════════════════════════════════

async def _text_template(ctx, inputs, params):
    template = str(inputs.get("template") or params.get("template", ""))
    variables = inputs.get("vars") or params.get("vars", {}) or {}
    if not isinstance(variables, dict):
        variables = {"value": variables}
    try:
        out = template.format(**variables)
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Template error: {exc}") from exc
    return {"text": out}


register(NodeType(
    name="text.template",
    display="Template",
    category="Text",
    description=(
        "Python f-string-style templating using vars from an "
        "upstream node."
    ),
    inputs=[
        Port("template", "string", optional=True),
        Port("vars", "any", optional=True),
    ],
    outputs=[Port("text", "string")],
    params=[Param("template", "text", "{value}")],
    color="#5f4d1e",
    fn=_text_template,
))


async def _text_join(ctx, inputs, params):
    items = inputs.get("items") or params.get("items", []) or []
    sep = str(inputs.get("separator") or params.get("separator", "\n"))
    return {"text": sep.join(str(x) for x in items)}


register(NodeType(
    name="text.join",
    display="Join",
    category="Text",
    description="Join a list of values into a single string.",
    inputs=[
        Port("items", "list", optional=True),
        Port("separator", "string", optional=True),
    ],
    outputs=[Port("text", "string")],
    params=[Param("separator", "string", "\n")],
    color="#5f4d1e",
    fn=_text_join,
))


# ═══════════════════════════════════════════════════════════════════════
#  CONTROL NODES
# ═══════════════════════════════════════════════════════════════════════

async def _control_branch(ctx, inputs, params):
    cond = inputs.get("condition")
    if isinstance(cond, str):
        cond = cond.strip().lower() in ("true", "1", "yes", "y")
    value = inputs.get("value")
    return {
        "true": value if cond else None,
        "false": value if not cond else None,
    }


register(NodeType(
    name="control.branch",
    display="Branch",
    category="Control",
    description=(
        "Route a value to one of two outputs based on a boolean "
        "condition."
    ),
    inputs=[Port("condition", "any"), Port("value", "any", optional=True)],
    outputs=[Port("true", "any"), Port("false", "any")],
    color="#5f1e4d",
    fn=_control_branch,
))


async def _control_merge(ctx, inputs, params):
    a = inputs.get("a")
    b = inputs.get("b")
    return {"value": a if a is not None else b}


register(NodeType(
    name="control.merge",
    display="Merge",
    category="Control",
    description="Pass through the first non-null input.",
    inputs=[Port("a", "any", optional=True), Port("b", "any", optional=True)],
    outputs=[Port("value", "any")],
    color="#5f1e4d",
    fn=_control_merge,
))


async def _control_foreach(ctx, inputs, params):
    """Run `op` once per item in `items`."""
    from .registry import get as get_type

    items = inputs.get("items") or params.get("items", []) or []
    if not isinstance(items, list):
        items = [items]

    op_name = params.get("op", "")
    op_params = params.get("op_params", {}) or {}
    if not op_name:
        raise ValueError("control.foreach: 'op' param required")

    op_type = get_type(op_name)
    if op_type is None or op_type.fn is None:
        raise ValueError(f"control.foreach: unknown op '{op_name}'")

    results = []
    for i, item in enumerate(items):
        sub_inputs = {"item": item, "index": i}
        out = await op_type.fn(ctx, sub_inputs, op_params)
        results.append(out)
    return {"results": results}


register(NodeType(
    name="control.foreach",
    display="For Each",
    category="Control",
    description=(
        "Run an operation once per item in a list. The op receives "
        "{item, index}."
    ),
    inputs=[Port("items", "list", optional=True)],
    outputs=[Port("results", "list")],
    params=[
        Param("op", "string", "llm.chat"),
        Param("op_params", "json", {}),
    ],
    color="#5f1e4d",
    fn=_control_foreach,
))


# ═══════════════════════════════════════════════════════════════════════
#  UTILITY NODES
# ═══════════════════════════════════════════════════════════════════════

async def _util_constant(ctx, inputs, params):
    return {"value": params.get("value")}


register(NodeType(
    name="util.constant",
    display="Constant",
    category="Utility",
    description="Emit a fixed value.",
    outputs=[Port("value", "any")],
    params=[Param("value", "json", None)],
    color="#3a3a3a",
    fn=_util_constant,
))


async def _util_log(ctx, inputs, params):
    value = inputs.get("value")
    print(f"[3olution.graph.log] {value}")
    return {"value": value}


register(NodeType(
    name="util.log",
    display="Log",
    category="Utility",
    description="Print the incoming value to stdout, then pass it through.",
    inputs=[Port("value", "any")],
    outputs=[Port("value", "any")],
    color="#3a3a3a",
    fn=_util_log,
))


async def _util_delay(ctx, inputs, params):
    import asyncio
    seconds = float(params.get("seconds", 1.0))
    await asyncio.sleep(seconds)
    return {"value": inputs.get("value")}


register(NodeType(
    name="util.delay",
    display="Delay",
    category="Utility",
    description="Wait for a number of seconds, then pass the value through.",
    inputs=[Port("value", "any", optional=True)],
    outputs=[Port("value", "any")],
    params=[Param("seconds", "number", 1.0)],
    color="#3a3a3a",
    fn=_util_delay,
))