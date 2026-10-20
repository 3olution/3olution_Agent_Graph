"""
3olution Agent Graph — demo custom nodes.
https://3olution.com

Reference implementations for eight common agent patterns. Copy
this file into `backend/graph/custom_nodes/`, restart the backend,
and each node appears in the graph editor's palette.

================================================================
  WHAT THIS FILE DEMONSTRATES
================================================================

  The graph engine is not "prompt → LLM → answer". These nodes
  combine LLM calls with Python control flow, so users can see
  what is achievable when a graph becomes a small program:

  ┌────────────────────────────────────────────────────────────┐
  │  custom.agent_chain     Sequential prompts, each sees the  │
  │                         previous answer. Follow-up trigger.│
  ├────────────────────────────────────────────────────────────┤
  │  custom.agent_pool      Run N "agents" in parallel over a  │
  │                         list of items. Concurrency-limited.│
  ├────────────────────────────────────────────────────────────┤
  │  custom.agent_router    LLM-based dynamic branching. The   │
  │                         LLM picks which output port gets   │
  │                         the value (5 branches max).        │
  ├────────────────────────────────────────────────────────────┤
  │  custom.map_llm         Map one prompt across a list, one  │
  │                         LLM call per item, in parallel.    │
  ├────────────────────────────────────────────────────────────┤
  │  custom.reduce_llm      Reduce a list of results into one  │
  │                         combined answer via the LLM.       │
  ├────────────────────────────────────────────────────────────┤
  │  custom.critique_refine Draft → critique → refine loop     │
  │                         with a quality threshold and a     │
  │                         maximum iteration count.           │
  ├────────────────────────────────────────────────────────────┤
  │  custom.consensus       Ask several models the same        │
  │                         question, vote on the majority.    │
  ├────────────────────────────────────────────────────────────┤
  │  custom.json_extract    Schema-validated JSON extraction   │
  │                         with automatic retry on parse      │
  │                         failure.                           │
  └────────────────────────────────────────────────────────────┘

  map_llm + reduce_llm compose into the classic map-reduce
  pattern — attach them in sequence.

================================================================
  WHAT THE NODES DO NOT DO
================================================================

  - They do not pass `tools` to the LLM. Every internal call uses
    disable_tools=True. If your pattern needs the model to invoke
    MCP tools mid-turn, use the built-in `llm.chat` node instead
    (it exposes a tools input) or extend these nodes.

  - They do not stream tokens. Each internal LLM call is a
    non-streaming chat completion. The pattern is "one node = one
    atomic result", which is exactly what a graph node should be.
    If you want streamed reasoning to reach the UI, use the
    built-in `llm.chat` node.

  - They do not persist. Each node is stateless. To save a chain's
    transcript for later inspection, attach a `util.log` node, or
    write your own `custom.save_to_file` node.

================================================================
  HOW TO COMPOSE THEM (example graphs)
================================================================

  Sequential agent (customer-support escalation):
      input.text ─► custom.agent_chain ─► output.text
      prompts param: [
        "Draft an initial reply to: {input}",
        "Now critique your reply and improve it: {previous}",
        "Rewrite the improved reply more concisely: {previous}"
      ]

  Parallel fan-out (bulk translation):
      input.json ─► custom.agent_pool ─► output.text
      items: ["Hello", "Bonjour", "Hola", "Ciao"]
      prompt_template: "Translate to German: {item}"

  Dynamic routing (ticket triage):
      input.text ─► custom.agent_router ─┬─► branch_0 → billing flow
                                          ├─► branch_1 → tech-support
                                          └─► branch_2 → other
      branches: "billing,tech-support,other"

  Map-reduce (summarise many documents):
      input.json ─► custom.map_llm ─► custom.reduce_llm ─► output.text
      map.prompt:    "Summarise this document in 2 sentences: {item}"
      reduce.prompt: "Combine these summaries: {results}"

  Draft-refine (writing assistant):
      input.text ─► custom.critique_refine ─► output.text
      critique_prompt: "Score 1-10 and list issues for: {draft}\nTask: {task}"
      quality_threshold: 9.0
      max_iterations: 3

  Ensemble (fact-checking):
      input.text ─► custom.consensus ─► output.text
      models: "llama3.2, qwen2.5, mistral:7b"
      → returns the majority answer plus all responses

  Structured output (form-filling from email):
      input.text ─► custom.json_extract ─► output.text
      schema: {"type": "object", "properties": {"name": {"type":"string"},
                "date": {"type":"string"}, "amount": {"type":"number"}}}
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from ..model import NodeType, Param, Port
from ..registry import register


# ══════════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════════

def _decode_model_and_provider(
    raw_model: str | None,
    raw_provider: str | None,
) -> tuple[str | None, str]:
    """Decode the `provider::model` encoding the frontend sends."""
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


def _get_llm(ctx):
    llm = ctx.app.get("llm")
    if llm is None:
        raise RuntimeError(
            "LLM client not available in app state. "
            "Check the backend startup log — the primary LLM "
            "endpoint may be unreachable."
        )
    return llm


async def _one_chat(
    llm,
    user_content: str,
    *,
    model: str | None,
    provider: str,
    system: str = "",
    temperature: float = 0.0,
) -> str:
    """Single non-streaming LLM call. Returns the assistant text."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})

    resp = await llm.chat(
        messages,
        model=model,
        provider=provider,
        disable_tools=True,
        temperature=temperature,
    )
    msg = resp.choices[0].message
    return msg.content or ""


def _safe_format(template: str, **kwargs: Any) -> str:
    """
    str.format() with a fallback for missing keys.

    A user prompt containing `{unknown_key}` would normally raise
    KeyError. Here it is left verbatim so the LLM sees the literal
    text instead of the whole node failing.
    """
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


# ══════════════════════════════════════════════════════════════════════
#  1. custom.agent_chain — sequential prompts with follow-up
# ══════════════════════════════════════════════════════════════════════

async def _agent_chain(ctx, inputs, params):
    """
    Run a list of prompts in sequence, feeding each answer to the next.

    This is the "follow-up trigger" pattern: every turn has access
    to the previous turn's output, so you can build refine chains,
    multi-step reasoning, or escalation ladders without writing
    any loop code.

    Templates support three placeholders:
        {input}     — the initial input (fixed across all steps)
        {previous}  — the previous step's answer (empty on step 1)
        {step}      — the 1-based step number
    """
    llm = _get_llm(ctx)

    initial = str(inputs.get("initial") or params.get("initial", ""))
    system = str(params.get("system_prompt", ""))
    stop_on_error = bool(params.get("stop_on_error", True))

    # prompts can be a JSON array (recommended) or a single string.
    raw_prompts = params.get("prompts", [])
    if isinstance(raw_prompts, str):
        try:
            raw_prompts = json.loads(raw_prompts)
        except json.JSONDecodeError:
            raw_prompts = [raw_prompts]
    if not isinstance(raw_prompts, list) or not raw_prompts:
        raise ValueError(
            "custom.agent_chain: 'prompts' must be a non-empty "
            "JSON array of strings."
        )

    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )

    transcript: list[dict] = []
    previous = ""
    for i, tmpl in enumerate(raw_prompts, start=1):
        prompt = _safe_format(
            str(tmpl),
            input=initial,
            previous=previous,
            step=i,
        )

        try:
            answer = await _one_chat(
                llm, prompt,
                model=model, provider=provider, system=system,
            )
        except Exception as exc:
            logger.warning(
                "agent_chain step {} failed: {}", i, exc,
            )
            if stop_on_error:
                raise
            answer = f"[step {i} error: {exc}]"

        transcript.append({
            "step": i,
            "prompt": prompt,
            "response": answer,
        })
        previous = answer

    return {
        "final": previous,
        "transcript": transcript,
        "steps": len(transcript),
    }


register(NodeType(
    name="custom.agent_chain",
    display="Agent Chain (sequential)",
    category="Custom Patterns",
    description=(
        "Run a list of prompts one after another. Each prompt sees "
        "the previous answer via {previous}, enabling follow-up "
        "chains, refinement ladders, and multi-step reasoning."
    ),
    inputs=[Port("initial", "any", optional=True)],
    outputs=[
        Port("final", "string"),
        Port("transcript", "list"),
        Port("steps", "number"),
    ],
    params=[
        Param("system_prompt", "text", "",
              description="Optional system prompt applied to every step"),
        Param("prompts", "json",
              ["{input}", "Improve this: {previous}"],
              description=(
                  "JSON array of prompt templates. Placeholders: "
                  "{input}, {previous}, {step}."
              )),
        Param("model", "select", "", dynamic="all.models",
              description="Model to use for every step"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"],
              description="Provider override if model has no prefix"),
        Param("stop_on_error", "bool", True,
              description="Abort the chain on the first failed step"),
    ],
    color="#4b2a5f",
    fn=_agent_chain,
))


# ══════════════════════════════════════════════════════════════════════
#  2. custom.agent_pool — parallel workers over a list
# ══════════════════════════════════════════════════════════════════════

async def _agent_pool(ctx, inputs, params):
    """
    Run a prompt across N items in parallel, bounded by a concurrency
    semaphore.

    Each "agent" is an independent LLM call. The output preserves
    the input order, so downstream nodes see a stable list.
    """
    llm = _get_llm(ctx)

    items = inputs.get("items") or params.get("items", []) or []
    if not isinstance(items, list):
        items = [items]
    if not items:
        return {"results": [], "merged": "", "count": 0, "errors": []}

    prompt_tpl = str(params.get("prompt_template", "{item}"))
    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )
    concurrency = max(1, int(params.get("concurrency", 4)))
    merge_sep = str(params.get("merge_separator", "\n\n---\n\n"))

    sem = asyncio.Semaphore(concurrency)

    async def run_one(index: int, item: Any) -> dict:
        async with sem:
            prompt = _safe_format(
                prompt_tpl, item=item, index=index,
            )
            try:
                answer = await _one_chat(
                    llm, prompt,
                    model=model, provider=provider,
                )
                return {
                    "index": index,
                    "item": str(item),
                    "response": answer,
                    "error": None,
                }
            except Exception as exc:
                logger.warning(
                    "agent_pool item {} failed: {}", index, exc,
                )
                return {
                    "index": index,
                    "item": str(item),
                    "response": "",
                    "error": str(exc),
                }

    unordered = await asyncio.gather(
        *[run_one(i, it) for i, it in enumerate(items)]
    )
    results = sorted(unordered, key=lambda r: r["index"])

    merged = merge_sep.join(
        r["response"] for r in results if r["response"]
    )
    errors = [r for r in results if r["error"]]

    return {
        "results": results,
        "merged": merged,
        "count": len(results),
        "errors": errors,
    }


register(NodeType(
    name="custom.agent_pool",
    display="Agent Pool (parallel)",
    category="Custom Patterns",
    description=(
        "Run a prompt template across N items in parallel. "
        "Concurrency-limited so you don't hit provider rate limits. "
        "Preserves input order in the output."
    ),
    inputs=[Port("items", "list")],
    outputs=[
        Port("results", "list"),
        Port("merged", "string"),
        Port("count", "number"),
        Port("errors", "list"),
    ],
    params=[
        Param("prompt_template", "text", "Process: {item}",
              description=(
                  "Prompt template. Placeholders: {item}, {index}."
              )),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
        Param("concurrency", "number", 4,
              description="Max simultaneous LLM calls"),
        Param("merge_separator", "text", "\n\n---\n\n",
              description="Separator used when building 'merged'"),
    ],
    color="#2a4b5f",
    fn=_agent_pool,
))


# ══════════════════════════════════════════════════════════════════════
#  3. custom.agent_router — LLM-based branching
# ══════════════════════════════════════════════════════════════════════

async def _agent_router(ctx, inputs, params):
    """
    Use the LLM to classify the input into one of up to five branches,
    then route the value to the matching output port.

    Only the chosen branch receives the value; the others receive
    None. Downstream nodes can rely on this — a branch that never
    fires is simply not traversed.
    """
    llm = _get_llm(ctx)

    value = inputs.get("input")
    empty_out = {
        "chosen": "", "chosen_index": -1, "reason": "no input",
        "branch_0": None, "branch_1": None, "branch_2": None,
        "branch_3": None, "branch_4": None,
    }
    if value is None:
        return empty_out

    branches_raw = str(params.get("branches", "positive,negative,neutral"))
    branches = [b.strip() for b in branches_raw.split(",") if b.strip()][:5]
    if not branches:
        raise ValueError(
            "custom.agent_router: 'branches' must be a comma-separated "
            "list of at least one category name."
        )

    router_prompt = str(params.get(
        "router_prompt",
        "Classify the following input into exactly one of these "
        "categories: {branches}.\n\n"
        "Respond with only the category name, nothing else.\n\n"
        "Input: {input}",
    ))
    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )

    prompt = _safe_format(
        router_prompt,
        branches=", ".join(branches),
        input=value,
    )
    answer = (await _one_chat(
        llm, prompt, model=model, provider=provider, temperature=0.0,
    )).strip()

    chosen_idx = -1
    chosen = ""
    for i, b in enumerate(branches):
        if b.lower() in answer.lower():
            chosen_idx = i
            chosen = b
            break

    # If the LLM refused to pick a branch, fall back to the first so
    # the graph keeps running instead of stalling.
    if chosen_idx == -1:
        chosen_idx = 0
        chosen = branches[0]
        logger.warning(
            "agent_router: LLM response {} did not match any branch; "
            "falling back to '{}'", answer, chosen,
        )

    out = {
        "chosen": chosen,
        "chosen_index": chosen_idx,
        "reason": answer,
    }
    for i in range(5):
        out[f"branch_{i}"] = value if i == chosen_idx else None
    return out


register(NodeType(
    name="custom.agent_router",
    display="Router (LLM branch)",
    category="Custom Patterns",
    description=(
        "Ask the LLM to classify the input into one of up to five "
        "named branches, then route the value to the matching "
        "output port. Enables dynamic fan-out based on content."
    ),
    inputs=[Port("input", "any")],
    outputs=[
        Port("branch_0", "any"),
        Port("branch_1", "any"),
        Port("branch_2", "any"),
        Port("branch_3", "any"),
        Port("branch_4", "any"),
        Port("chosen", "string"),
        Port("chosen_index", "number"),
        Port("reason", "string"),
    ],
    params=[
        Param("branches", "text", "positive,negative,neutral",
              description=(
                  "Comma-separated branch names (max 5). The order "
                  "determines which output port each one maps to."
              )),
        Param("router_prompt", "text",
              "Classify the following input into exactly one of these "
              "categories: {branches}.\n\nRespond with only the "
              "category name, nothing else.\n\nInput: {input}",
              description=(
                  "Classification prompt. Placeholders: {branches}, "
                  "{input}."
              )),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
    ],
    color="#5f2a4b",
    fn=_agent_router,
))


# ══════════════════════════════════════════════════════════════════════
#  4. custom.map_llm — map a prompt over a list
# ══════════════════════════════════════════════════════════════════════

async def _map_llm(ctx, inputs, params):
    """
    Process each item with an LLM call and return a parallel list of
    results.

    This is the "map" half of map-reduce. Combine with
    `custom.reduce_llm` to get the full pattern.
    """
    llm = _get_llm(ctx)

    items = inputs.get("items") or params.get("items", []) or []
    if not isinstance(items, list):
        items = [items]
    if not items:
        return {"results": [], "errors": [], "count": 0}

    prompt_tpl = str(params.get("prompt", "{item}"))
    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )
    concurrency = max(1, int(params.get("concurrency", 4)))

    sem = asyncio.Semaphore(concurrency)

    async def run(index: int, item: Any) -> tuple[int, str | None, str | None]:
        async with sem:
            try:
                prompt = _safe_format(
                    prompt_tpl, item=item, index=index,
                )
                answer = await _one_chat(
                    llm, prompt,
                    model=model, provider=provider,
                )
                return (index, answer, None)
            except Exception as exc:
                return (index, None, str(exc))

    triples = await asyncio.gather(
        *[run(i, it) for i, it in enumerate(items)]
    )
    triples = sorted(triples, key=lambda t: t[0])

    results: list[Any] = []
    errors: list[dict] = []
    for i, content, err in triples:
        results.append(content if content is not None else "")
        if err:
            errors.append({"index": i, "error": err})

    return {
        "results": results,
        "errors": errors,
        "count": len(results),
    }


register(NodeType(
    name="custom.map_llm",
    display="Map (LLM per item)",
    category="Custom Patterns",
    description=(
        "Apply a prompt template to every item in a list, one LLM "
        "call per item, in parallel. The 'map' half of map-reduce."
    ),
    inputs=[Port("items", "list")],
    outputs=[
        Port("results", "list"),
        Port("errors", "list"),
        Port("count", "number"),
    ],
    params=[
        Param("prompt", "text", "Process this item: {item}",
              description=(
                  "Prompt template. Placeholders: {item}, {index}."
              )),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
        Param("concurrency", "number", 4,
              description="Max simultaneous LLM calls"),
    ],
    color="#2a5f4b",
    fn=_map_llm,
))


# ══════════════════════════════════════════════════════════════════════
#  5. custom.reduce_llm — fold a list into one answer
# ══════════════════════════════════════════════════════════════════════

async def _reduce_llm(ctx, inputs, params):
    """
    Combine a list of results into one answer via an LLM call.

    This is the "reduce" half of map-reduce. It formats the input
    list with numbered prefixes so the LLM can see each item
    distinctly, then asks it to merge them.
    """
    llm = _get_llm(ctx)

    items = inputs.get("items") or params.get("items", []) or []
    if not isinstance(items, list):
        items = [items]
    if not items:
        return {"result": ""}

    reduce_prompt = str(params.get(
        "reduce_prompt",
        "Combine the following items into one coherent summary. "
        "Preserve every distinct point. Do not invent details.\n\n"
        "{results}",
    ))
    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )
    separator = str(params.get("separator", "\n\n"))

    formatted = separator.join(
        f"[{i + 1}] {item}" for i, item in enumerate(items)
    )
    prompt = _safe_format(
        reduce_prompt,
        results=formatted,
        count=len(items),
    )

    answer = await _one_chat(
        llm, prompt, model=model, provider=provider,
    )
    return {"result": answer}


register(NodeType(
    name="custom.reduce_llm",
    display="Reduce (combine via LLM)",
    category="Custom Patterns",
    description=(
        "Combine a list of results into one answer via an LLM call. "
        "The 'reduce' half of map-reduce. Feed it the 'results' "
        "output from a Map node."
    ),
    inputs=[Port("items", "list")],
    outputs=[Port("result", "string")],
    params=[
        Param("reduce_prompt", "text",
              "Combine the following items into one coherent summary. "
              "Preserve every distinct point. Do not invent details."
              "\n\n{results}",
              description=(
                  "Reduction prompt. Placeholders: {results}, {count}."
              )),
        Param("separator", "text", "\n\n",
              description="Joins the numbered items in {results}"),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
    ],
    color="#2a5f4b",
    fn=_reduce_llm,
))


# ══════════════════════════════════════════════════════════════════════
#  6. custom.critique_refine — iterative refinement
# ══════════════════════════════════════════════════════════════════════

def _parse_score(text: str) -> float:
    """Extract the first 1-10 numeric score from a critique."""
    for line in text.splitlines():
        if "score" in line.lower():
            digits = "".join(
                c for c in line if c.isdigit() or c == "."
            )
            try:
                return float(digits)
            except ValueError:
                return 0.0
    # No SCORE line found — fall back to the first standalone number.
    for word in text.split():
        try:
            return float(word)
        except ValueError:
            continue
    return 0.0


async def _critique_refine(ctx, inputs, params):
    """
    Draft → critique → refine loop.

    Runs up to `max_iterations` cycles. Stops early when the
    critique's score reaches `quality_threshold`.

    If no `draft` is provided, the first iteration generates one
    from the `task`. This makes the node usable either as a
    "start from scratch and iterate" or as "polish this existing
    draft".
    """
    llm = _get_llm(ctx)

    task = str(inputs.get("task") or params.get("task", "")).strip()
    if not task:
        raise ValueError(
            "custom.critique_refine: 'task' input (or param) required"
        )

    current = str(inputs.get("draft") or "").strip()
    system = str(params.get("system_prompt", ""))

    critique_tpl = str(params.get(
        "critique_prompt",
        "Critique the following draft for the task below.\n\n"
        "Task: {task}\n\nDraft:\n{draft}\n\n"
        "Respond in exactly this format, on two lines:\n"
        "SCORE: <a number 1-10>\n"
        "ISSUES: <one specific improvement>\n",
    ))
    refine_tpl = str(params.get(
        "refine_prompt",
        "Task: {task}\n\n"
        "Current draft:\n{draft}\n\n"
        "Critique:\n{critique}\n\n"
        "Produce an improved draft. Output the draft only — no "
        "explanations, no critique, no meta commentary.",
    ))

    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )
    threshold = float(params.get("quality_threshold", 8.0))
    max_iter = max(1, int(params.get("max_iterations", 3)))

    history: list[dict] = []
    score = 0.0

    for iteration in range(1, max_iter + 1):
        # First iteration with no draft: generate one.
        if not current:
            try:
                current = await _one_chat(
                    llm,
                    f"Complete the following task:\n\n{task}",
                    model=model, provider=provider, system=system,
                )
            except Exception as exc:
                logger.warning(
                    "critique_refine: initial draft generation "
                    "failed: {}", exc,
                )
                return {
                    "final": "",
                    "iterations": 0,
                    "score": 0.0,
                    "history": [],
                    "error": str(exc),
                }

        # Critique
        critique_prompt = _safe_format(
            critique_tpl, task=task, draft=current,
            iteration=iteration,
        )
        try:
            critique = await _one_chat(
                llm, critique_prompt,
                model=model, provider=provider,
            )
        except Exception as exc:
            logger.warning(
                "critique_refine: critique failed at iter {}: {}",
                iteration, exc,
            )
            critique = f"[critique error: {exc}]"

        score = _parse_score(critique)

        history.append({
            "iteration": iteration,
            "draft": current,
            "critique": critique,
            "score": score,
        })

        # Stop early if the score is high enough.
        if score >= threshold:
            logger.info(
                "critique_refine: threshold {} met at iteration {} "
                "(score {})", threshold, iteration, score,
            )
            break

        # Don't refine after the last iteration — we'd never use it.
        if iteration >= max_iter:
            break

        # Refine
        refine_prompt = _safe_format(
            refine_tpl, task=task, draft=current,
            critique=critique, score=score,
        )
        try:
            current = await _one_chat(
                llm, refine_prompt,
                model=model, provider=provider, system=system,
            )
        except Exception as exc:
            logger.warning(
                "critique_refine: refine failed at iter {}: {}",
                iteration, exc,
            )
            break

    return {
        "final": current,
        "iterations": len(history),
        "score": score,
        "history": history,
    }


register(NodeType(
    name="custom.critique_refine",
    display="Critique & Refine (loop)",
    category="Custom Patterns",
    description=(
        "Draft → critique → refine loop. Runs up to max_iterations "
        "cycles; stops early when the critique score reaches "
        "quality_threshold. Accepts an optional starting draft."
    ),
    inputs=[
        Port("task", "string"),
        Port("draft", "string", optional=True),
    ],
    outputs=[
        Port("final", "string"),
        Port("iterations", "number"),
        Port("score", "number"),
        Port("history", "list"),
    ],
    params=[
        Param("task", "text", "",
              description="Task description (used if task input is empty)"),
        Param("system_prompt", "text", "",
              description="System prompt applied to every call"),
        Param("critique_prompt", "text",
              "Critique the following draft for the task below."
              "\n\nTask: {task}\n\nDraft:\n{draft}\n\n"
              "Respond in exactly this format, on two lines:\n"
              "SCORE: <a number 1-10>\n"
              "ISSUES: <one specific improvement>\n",
              description=(
                  "Critique prompt. Placeholders: {task}, {draft}, "
                  "{iteration}. The response must contain 'SCORE:'."
              )),
        Param("refine_prompt", "text",
              "Task: {task}\n\nCurrent draft:\n{draft}\n\n"
              "Critique:\n{critique}\n\n"
              "Produce an improved draft. Output the draft only.",
              description=(
                  "Refinement prompt. Placeholders: {task}, {draft}, "
                  "{critique}, {score}."
              )),
        Param("quality_threshold", "number", 8.0,
              description="Stop when the critique score reaches this"),
        Param("max_iterations", "number", 3,
              description="Hard cap on draft-critique-refine cycles"),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
    ],
    color="#5f4b2a",
    fn=_critique_refine,
))


# ══════════════════════════════════════════════════════════════════════
#  7. custom.consensus — multi-model voting
# ══════════════════════════════════════════════════════════════════════

def _fingerprint(text: str, tokens: int = 5) -> str:
    """Lowercase, first-N-token signature used for majority voting."""
    return " ".join(text.lower().split()[:tokens])


async def _consensus(ctx, inputs, params):
    """
    Ask several models the same question in parallel, then return
    the majority answer and a 0-1 agreement score.

    Majority is determined by the first five tokens of each answer
    (case-insensitive). This is a fast heuristic, not semantic
    voting — but for factual questions ("What is 12×8?") it works
    well because the answer is short and starts with the same word.
    """
    llm = _get_llm(ctx)

    prompt = str(inputs.get("prompt") or params.get("prompt", "")).strip()
    if not prompt:
        raise ValueError(
            "custom.consensus: 'prompt' input (or param) required"
        )

    models_raw = str(params.get("models", ""))
    models = [m.strip() for m in models_raw.split(",") if m.strip()]
    if not models:
        raise ValueError(
            "custom.consensus: 'models' must be a comma-separated "
            "list of model names (e.g. 'llama3.2, qwen2.5, mistral:7b')."
        )

    provider = str(params.get("provider", "auto")).strip().lower() or "auto"
    temperature = float(params.get("temperature", 0.7))

    async def ask(model_name: str) -> dict:
        try:
            answer = await _one_chat(
                llm, prompt,
                model=model_name, provider=provider,
                temperature=temperature,
            )
            return {
                "model": model_name,
                "answer": answer,
                "error": None,
            }
        except Exception as exc:
            return {
                "model": model_name,
                "answer": "",
                "error": str(exc),
            }

    responses = await asyncio.gather(*[ask(m) for m in models])

    valid = [r for r in responses if r["answer"]]
    if not valid:
        return {
            "answer": "",
            "all_responses": responses,
            "agreement": 0.0,
            "majority_count": 0,
        }

    # Count fingerprints.
    buckets: dict[str, list[str]] = {}
    for r in valid:
        key = _fingerprint(r["answer"])
        buckets.setdefault(key, []).append(r["answer"])

    majority_key = max(buckets, key=lambda k: len(buckets[k]))
    majority_answers = buckets[majority_key]
    majority_answer = majority_answers[0]
    agreement = len(majority_answers) / len(valid)

    return {
        "answer": majority_answer,
        "all_responses": responses,
        "agreement": round(agreement, 3),
        "majority_count": len(majority_answers),
    }


register(NodeType(
    name="custom.consensus",
    display="Consensus (multi-model vote)",
    category="Custom Patterns",
    description=(
        "Ask several models the same prompt in parallel and return "
        "the majority answer plus an agreement score. Useful for "
        "fact-checking, sanity checks, and cross-model ensembles."
    ),
    inputs=[Port("prompt", "string")],
    outputs=[
        Port("answer", "string"),
        Port("all_responses", "list"),
        Port("agreement", "number"),
        Port("majority_count", "number"),
    ],
    params=[
        Param("prompt", "text", "",
              description="Question (used if prompt input is empty)"),
        Param("models", "text", "llama3.2, qwen2.5, mistral:7b",
              description=(
                  "Comma-separated model names to query in parallel."
              )),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"],
              description=(
                  "Provider applied to every model. Set to 'ollama' "
                  "when the model names are Ollama tags."
              )),
        Param("temperature", "number", 0.7,
              description="Sampling temperature for every model"),
    ],
    color="#2a2a5f",
    fn=_consensus,
))


# ══════════════════════════════════════════════════════════════════════
#  8. custom.json_extract — schema-validated JSON
# ══════════════════════════════════════════════════════════════════════

def _strip_code_fence(text: str) -> str:
    """Remove ```json … ``` fences the LLM may add despite instructions."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    # Drop the opening fence line.
    lines = lines[1:]
    # Drop the closing fence if present.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def _json_extract(ctx, inputs, params):
    """
    Ask the LLM to produce JSON matching a schema, validate the
    result, and retry on parse failure.

    On success: `data` holds the parsed object, `valid` is True.
    On failure: `data` is None, `valid` is False, `errors` lists
    every attempt's failure reason.

    The retry prompt includes the previous parse error so the model
    can correct its mistake — much more reliable than blind retries
    with the same prompt.
    """
    llm = _get_llm(ctx)

    text = str(inputs.get("text") or params.get("text", "")).strip()
    if not text:
        return {
            "data": None,
            "valid": False,
            "errors": ["empty input"],
            "raw": "",
        }

    raw_schema = params.get("schema", "{}")
    if isinstance(raw_schema, str):
        try:
            schema = json.loads(raw_schema)
        except json.JSONDecodeError as exc:
            return {
                "data": None,
                "valid": False,
                "errors": [f"invalid schema parameter: {exc}"],
                "raw": "",
            }
    else:
        schema = raw_schema

    extract_tpl = str(params.get(
        "extract_prompt",
        "Extract structured data from the following text according "
        "to this JSON schema:\n\n{schema}\n\n"
        "Respond with ONLY valid JSON. No markdown fences, no "
        "explanation, no preamble.\n\n"
        "Text:\n{text}",
    ))
    model, provider = _decode_model_and_provider(
        params.get("model"), params.get("provider"),
    )
    max_retries = max(0, int(params.get("max_retries", 2)))

    errors: list[str] = []
    last_raw = ""

    for attempt in range(max_retries + 1):
        prompt = _safe_format(
            extract_tpl,
            schema=json.dumps(schema, indent=2),
            text=text,
        )
        if attempt > 0:
            prompt = (
                f"Previous attempt failed with: {errors[-1]}\n\n"
                f"Try again. Output only valid JSON.\n\n{prompt}"
            )

        try:
            raw = await _one_chat(
                llm, prompt,
                model=model, provider=provider, temperature=0.0,
            )
        except Exception as exc:
            errors.append(f"attempt {attempt + 1}: LLM call failed: {exc}")
            continue

        last_raw = raw
        cleaned = _strip_code_fence(raw)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            errors.append(
                f"attempt {attempt + 1}: JSON decode failed: {exc}"
            )
            continue

        return {
            "data": data,
            "valid": True,
            "errors": errors,
            "raw": cleaned,
        }

    return {
        "data": None,
        "valid": False,
        "errors": errors,
        "raw": last_raw,
    }


register(NodeType(
    name="custom.json_extract",
    display="JSON Extract (schema)",
    category="Custom Patterns",
    description=(
        "Extract JSON matching a schema from free-form text, with "
        "automatic retry on parse failure. Each retry includes the "
        "previous error in the prompt so the model can self-correct."
    ),
    inputs=[Port("text", "string")],
    outputs=[
        Port("data", "any"),
        Port("valid", "boolean"),
        Port("errors", "list"),
        Port("raw", "string"),
    ],
    params=[
        Param("text", "text", "",
              description="Source text (used if text input is empty)"),
        Param("schema", "json",
              {
                  "type": "object",
                  "properties": {
                      "name": {"type": "string"},
                      "value": {"type": "number"},
                  },
              },
              description=(
                  "JSON schema describing the desired output shape."
              )),
        Param("extract_prompt", "text",
              "Extract structured data from the following text "
              "according to this JSON schema:\n\n{schema}\n\n"
              "Respond with ONLY valid JSON. No markdown fences, no "
              "explanation, no preamble.\n\n"
              "Text:\n{text}",
              description=(
                  "Extraction prompt. Placeholders: {schema}, {text}."
              )),
        Param("max_retries", "number", 2,
              description=(
                  "Extra attempts on JSON parse failure. Total "
                  "attempts = max_retries + 1."
              )),
        Param("model", "select", "", dynamic="all.models"),
        Param("provider", "select", "auto",
              choices=["auto", "ollama", "llama_cpp", "fallback"]),
    ],
    color="#5f2a2a",
    fn=_json_extract,
))


# ══════════════════════════════════════════════════════════════════════
#  Logger breadcrumb
# ══════════════════════════════════════════════════════════════════════
#
#  Logged once at module import time so the backend startup log shows
#  which custom pattern nodes were registered.

logger.info(
    "Registered 8 custom pattern nodes: agent_chain, agent_pool, "
    "agent_router, map_llm, reduce_llm, critique_refine, consensus, "
    "json_extract"
)