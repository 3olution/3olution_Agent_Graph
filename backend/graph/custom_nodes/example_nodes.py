

"""
## `backend/graph/custom_nodes/example_nodes.py`

Example custom nodes for the 3olution Agent Graph.
https://3olution.com

Seven working nodes demonstrating the full range of what a custom
node can do. Delete this file if you don't want the examples in
your palette — the loader simply finds fewer modules.

Each node below is a self-contained illustration of a pattern:

    1. custom.uppercase        — the simplest possible node
    2. custom.reverse          — one-line transformation
    3. custom.random           — node with numeric parameters
    4. custom.json_extract     — traversing structured input
    5. custom.llm_json         — LLM call with JSON parsing
    6. custom.sleep            — async waiting
    7. custom.random_string    — parameterised generation
"""

from __future__ import annotations

import asyncio
import json
import random
import string

from ..model import NodeType, Param, Port
from ..registry import register


# ══════════════════════════════════════════════════════════════════════
#  1. Uppercase — the simplest possible custom node
# ══════════════════════════════════════════════════════════════════════

async def _uppercase(ctx, inputs, params):
    text = inputs.get("text") or params.get("text", "")
    return {"text": str(text).upper()}


register(NodeType(
    name="custom.uppercase",
    display="Uppercase",
    category="Custom",
    description="Convert a string to uppercase.",
    inputs=[Port("text", "string")],
    outputs=[Port("text", "string")],
    params=[Param("text", "text", "")],
    color="#7c3aed",
    fn=_uppercase,
))


# ══════════════════════════════════════════════════════════════════════
#  2. Reverse Text — swaps the characters in a string
# ══════════════════════════════════════════════════════════════════════

async def _reverse_text(ctx, inputs, params):
    text = inputs.get("text") or params.get("text", "")
    return {"text": str(text)[::-1]}


register(NodeType(
    name="custom.reverse",
    display="Reverse Text",
    category="Custom",
    description="Reverse the characters in the given string.",
    inputs=[Port("text", "string")],
    outputs=[Port("text", "string")],
    params=[Param("text", "text", "")],
    color="#7c3aed",
    fn=_reverse_text,
))


# ══════════════════════════════════════════════════════════════════════
#  3. Random Number — emits a number in a range
# ══════════════════════════════════════════════════════════════════════

async def _random_number(ctx, inputs, params):
    lo = float(params.get("min", 0))
    hi = float(params.get("max", 100))
    if hi < lo:
        lo, hi = hi, lo
    value = random.uniform(lo, hi)
    if params.get("integer", True):
        return {"number": int(round(value))}
    return {"number": value}


register(NodeType(
    name="custom.random",
    display="Random Number",
    category="Custom",
    description="Emit a random number in the given range.",
    inputs=[
        Port("min", "number", optional=True),
        Port("max", "number", optional=True),
    ],
    outputs=[Port("number", "number")],
    params=[
        Param("min", "number", 1),
        Param("max", "number", 100),
        Param("integer", "bool", True),
    ],
    color="#7c3aed",
    fn=_random_number,
))


# ══════════════════════════════════════════════════════════════════════
#  4. JSON Extract — pull one field out of a JSON object
# ══════════════════════════════════════════════════════════════════════

async def _json_extract(ctx, inputs, params):
    """
    Extract a value from a JSON object using a dotted path.

    Paths support dict keys and list indices:

        "name"                → data["name"]
        "user.address.city"   → nested keys
        "items.0.name"        → list index
        "items.0.tags.2"      → deep mix
    """
    data = inputs.get("data")

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"json_extract: input is not valid JSON: {exc}"
            ) from exc

    path = str(params.get("path", "")).strip()
    if not path:
        return {"value": data}

    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise ValueError(f"json_extract: key '{part}' not found")
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError as exc:
                raise ValueError(
                    f"json_extract: '{part}' is not a valid list index"
                ) from exc
            current = current[idx]
        else:
            raise ValueError(
                f"json_extract: cannot descend into "
                f"{type(current).__name__} with '{part}'"
            )

    return {"value": current}


register(NodeType(
    name="custom.json_extract",
    display="JSON Extract",
    category="Custom",
    description=(
        "Extract a field from a JSON object using a dotted path "
        "(e.g. 'user.address.city' or 'items.0.name')."
    ),
    inputs=[Port("data", "any")],
    outputs=[Port("value", "any")],
    params=[Param("path", "string", "")],
    color="#7c3aed",
    fn=_json_extract,
))


# ══════════════════════════════════════════════════════════════════════
#  5. LLM JSON — asks the LLM for a structured JSON response
# ══════════════════════════════════════════════════════════════════════

async def _llm_json(ctx, inputs, params):
    """
    Send the prompt to the LLM and try to parse the reply as JSON.

    Emits both the raw text and the parsed value (or None if parsing
    failed). Pair this with custom.json_extract downstream to pull a
    specific field out of the LLM's structured output.

    The system prompt strongly discourages the model from wrapping
    its JSON in code fences, but we strip them defensively in case
    the model ignores the instruction.
    """
    llm = ctx.app.get("llm")
    if llm is None:
        raise RuntimeError("llm_json: LLM client not available")

    prompt = inputs.get("prompt") or params.get("prompt", "")
    if not prompt:
        raise ValueError("llm_json: no prompt supplied")

    system = params.get("system", "") or (
        "You are a helpful assistant. "
        "Reply with a single valid JSON object and nothing else — "
        "no code fences, no commentary, no explanation."
    )

    messages = [
        {"role": "system", "content": str(system)},
        {"role": "user", "content": str(prompt)},
    ]

    model_param = params.get("model") or ""
    model = None if model_param in ("", "(default)") else model_param

    response = await llm.chat(messages, model=model)
    text = response.choices[0].message.content or ""

    # Strip code fences if the model ignored the instruction.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    parsed = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None

    return {"text": text, "data": parsed}


register(NodeType(
    name="custom.llm_json",
    display="LLM → JSON",
    category="Custom",
    description=(
        "Ask the LLM for a JSON response. Emits the raw text and the "
        "parsed value (or None if the reply was not valid JSON)."
    ),
    inputs=[Port("prompt", "string")],
    outputs=[
        Port("text", "string"),
        Port("data", "any"),
    ],
    params=[
        Param("system", "text", ""),
        Param("prompt", "text", ""),
        Param("model", "select", "", dynamic="ollama.models"),
    ],
    color="#7c3aed",
    fn=_llm_json,
))


# ══════════════════════════════════════════════════════════════════════
#  6. Sleep — demonstrates async waiting
# ══════════════════════════════════════════════════════════════════════

async def _sleep(ctx, inputs, params):
    seconds = float(params.get("seconds", 1.0))
    # Clamp to keep a runaway graph from hanging forever.
    seconds = max(0.0, min(seconds, 60.0))
    await asyncio.sleep(seconds)
    return {"value": inputs.get("value")}


register(NodeType(
    name="custom.sleep",
    display="Sleep",
    category="Custom",
    description="Wait for N seconds, then pass the value through.",
    inputs=[Port("value", "any", optional=True)],
    outputs=[Port("value", "any")],
    params=[Param("seconds", "number", 1.0)],
    color="#7c3aed",
    fn=_sleep,
))


# ══════════════════════════════════════════════════════════════════════
#  7. Random String — generate a random token
# ══════════════════════════════════════════════════════════════════════

async def _random_string(ctx, inputs, params):
    length = int(params.get("length", 16))
    length = max(1, min(length, 256))
    alphabet = string.ascii_letters + string.digits
    value = "".join(random.choice(alphabet) for _ in range(length))
    return {"text": value}


register(NodeType(
    name="custom.random_string",
    display="Random String",
    category="Custom",
    description="Emit a random alphanumeric string of the given length.",
    outputs=[Port("text", "string")],
    params=[Param("length", "number", 16)],
    color="#7c3aed",
    fn=_random_string,
))
