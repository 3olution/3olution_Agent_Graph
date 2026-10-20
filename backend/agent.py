"""
Linear agent loop — perceive → decide → act → repeat.
https://3olution.com

This is the orchestrator that turns a user message into a sequence
of LLM calls and tool invocations. It is a plain async generator
that yields events; the caller (a FastAPI route or a WebSocket
handler) is responsible for delivering them to the client.

================================================================
  EVENTS EMITTED
================================================================

  {"type": "token",       "content": "..."}   — a streamed token
  {"type": "thinking",    "content": "..."}   — reasoning token
  {"type": "retry",       "message": "..."}   — a retry is happening
  {"type": "fallback",    "message": "..."}   — switching to fallback
  {"type": "info",        "message": "..."}   — informational notice
  {"type": "tool_call",   "name": ..., "args": ...}
  {"type": "tool_result", "name": ..., "result": ...}
  {"type": "final",       "content": ..., "messages": [...]}

  The `final` event always appears exactly once, and terminates the
  stream. Its `messages` field is the updated conversation history
  the client should pass back on the next turn.

================================================================
  EMPTY CONTENT ON ASSISTANT MESSAGES
================================================================

  When the LLM returns only tool calls and no text, the assembled
  assistant message has empty content. Two conventions exist:

    1. OpenAI and several OpenAI-compatible servers accept
       `"content": null` on an assistant message when `tool_calls`
       is present. This is the documented OpenAI convention.

    2. llama-cpp-python's Pydantic validation requires `content` to
       be a string. Passing null raises a validation error and the
       server returns HTTP 500.

  To satisfy both, this module emits `"content": ""` for assistant
  messages with no text. An empty string is accepted by every
  OpenAI-compatible server we have tested: OpenAI, Ollama, llama-cpp,
  vLLM, LM Studio, Groq, and Together.

================================================================
  PROVIDER ROUTING
================================================================

  The `provider` argument selects which LLM endpoint to use:

      "auto" / "primary" / "ollama"
          The primary LLM. For most users this is Ollama.

      "fallback"
          The fallback LLM, if AGENT_LLM_FALLBACK_BASE_URL is set.

      "llama_cpp" / "llamacpp" / "local"
          The locally-managed llama-cpp-python server. The agent
          performs a pre-flight check and returns a friendly error
          if the server is not running, rather than retrying a
          doomed connection three times.

================================================================
  GUARD RAILS
================================================================

  MAX_TOOL_CALLS_PER_TURN
      After this many distinct tool calls in a single turn, the
      agent stops offering tools to the LLM and forces a final
      answer.

  Duplicate call suppression
      If the LLM requests the exact same (name, args) twice in one
      turn, the second call is replaced with a canned message.

  Stream stall timeout
      If no token arrives for AGENT_STREAM_STALL_TIMEOUT seconds
      mid-stream, the turn is aborted with a clear error.

  Ollama unload on switch
      When AGENT_OLLAMA_UNLOAD_ON_SWITCH is true, switching models
      unloads the previous model from Ollama's memory.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

import openai
from loguru import logger

from .config import settings
from .llm import (
    LLMClient,
    is_no_tools_error,
    is_retryable,
    mark_model_without_tools,
    should_use_tools,
)
from .mcp_client import MCPClient
from .state import state as app_state
from .storage import SessionStore
from .thinking import ThinkingSplitter


# ══════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════

_LLAMA_CPP_PROVIDERS = {"llama_cpp", "llamacpp", "local"}

MAX_TOOL_CALLS_PER_TURN = 6


# ══════════════════════════════════════════════════════════════════════
#  System prompt
# ══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to a small set of tools.\n"
    "\n"
    "═══════════════════════════════════════════════════════════════\n"
    "  TOOL USE RULES — READ CAREFULLY\n"
    "═══════════════════════════════════════════════════════════════\n"
    "\n"
    "1. Only call a tool when the user's request literally requires\n"
    "   that specific tool. If in doubt, DO NOT call a tool — answer\n"
    "   the user directly instead.\n"
    "\n"
    "2. NEVER call a tool for:\n"
    "     - Greetings: \"hello\", \"hi\", \"hey\", \"good morning\"\n"
    "     - Thanks: \"thanks\", \"thank you\"\n"
    "     - Small talk: \"how are you\", \"what's up\"\n"
    "     - Meta-questions: \"what can you do\", \"who are you\"\n"
    "     - General knowledge: \"what is a list\", \"explain recursion\"\n"
    "   These are conversational. Respond with words, not tools.\n"
    "\n"
    "3. The tools do exactly this and nothing more:\n"
    "     - add(a, b)          — arithmetic only\n"
    "     - multiply(a, b)     — arithmetic only\n"
    "     - sqrt(x)            — arithmetic only\n"
    "     - power(base, exp)   — arithmetic only\n"
    "     - current_time()     — returns the current UTC timestamp\n"
    "     - list_directory(p)  — lists files in a folder\n"
    "     - read_file(p)       — reads a text file\n"
    "     - word_count(text)   — counts words in text\n"
    "     - shout(text)        — uppercases text\n"
    "   Use them ONLY when the user explicitly asks for that action.\n"
    "\n"
    "4. After calling a tool, use the result to answer the user's\n"
    "   ORIGINAL question in plain language. Do NOT:\n"
    "     - Describe the tool call to the user\n"
    "     - Reference the tool function or its arguments\n"
    "     - Apologize for mistakes you did not make\n"
    "     - Fabricate explanations about earlier messages\n"
    "   Just answer. The user never sees the tool machinery.\n"
    "\n"
    "5. Never call the same tool with the same arguments more than once.\n"
    "\n"
    "6. If a tool returns an error, describe the problem and suggest\n"
    "   an alternative — do not retry the same call.\n"
    "\n"
    "═══════════════════════════════════════════════════════════════\n"
    "  STYLE\n"
    "═══════════════════════════════════════════════════════════════\n"
    "\n"
    "- Be concise. Answer the actual question and stop.\n"
    "- Match the user's language and level of detail.\n"
    "- If the user shows you content from an earlier message, treat\n"
    "  that content as theirs, not as something you wrote.\n"
    "- Never invent context that isn't in the conversation above.\n"
)


# ══════════════════════════════════════════════════════════════════════
#  Reasoning extraction helper
# ══════════════════════════════════════════════════════════════════════
#
#  Different OpenAI SDK versions and providers expose reasoning tokens
#  in different places on the streaming delta object. This helper
#  checks both places safely and returns None if neither is present.

def _extract_reasoning_content(delta: Any) -> str | None:
    """
    Return the reasoning_content of a streaming delta, or None.

    Reads from the direct attribute (some SDKs) or the `model_extra`
    dict (OpenAI SDK v1 with Pydantic v2). Never raises.
    """
    try:
        direct = getattr(delta, "reasoning_content", None)
        if isinstance(direct, str) and direct:
            return direct
    except Exception:
        pass

    try:
        extra = getattr(delta, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get("reasoning_content")
            if isinstance(value, str) and value:
                return value
    except Exception:
        pass

    return None


# ══════════════════════════════════════════════════════════════════════
#  Agent
# ══════════════════════════════════════════════════════════════════════

class Agent:
    """
    The linear perceive-decide-act loop.

    One Agent instance is created during the FastAPI lifespan and
    reused for every request. It is stateless apart from
    `_last_ollama_model`, which tracks the currently-loaded Ollama
    model so that switching models can unload the previous one.
    """

    def __init__(
        self,
        llm: LLMClient,
        mcp: MCPClient,
        store: SessionStore | None = None,
    ) -> None:
        self.llm = llm
        self.mcp = mcp
        self.store = store
        self._last_ollama_model: str | None = None

    # ══════════════════════════════════════════════════════════════════
    #  Ollama unload-on-switch
    # ══════════════════════════════════════════════════════════════════

    async def _maybe_unload_previous(
        self, new_model: str | None, provider: str,
    ) -> None:
        """
        If the user switched Ollama models and unloading is enabled,
        ask Ollama to unload the previous one.

        Only applies to the Ollama provider. llama-cpp manages its
        own subprocess lifecycle; the fallback endpoint is external.
        """
        if provider in _LLAMA_CPP_PROVIDERS:
            return
        if provider == "fallback":
            return

        effective = new_model or settings.llm_model

        if not settings.ollama_unload_on_switch:
            self._last_ollama_model = effective
            return

        if self._last_ollama_model is None or self._last_ollama_model == effective:
            self._last_ollama_model = effective
            return

        previous = self._last_ollama_model
        self._last_ollama_model = effective

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{settings.ollama_host}/api/generate",
                    json={"model": previous, "keep_alive": 0},
                )
            logger.info("Unloaded previous Ollama model: {}", previous)
        except Exception as exc:
            logger.debug(
                "Could not unload previous model {}: {}", previous, exc,
            )

    # ══════════════════════════════════════════════════════════════════
    #  llama-cpp pre-flight check
    # ══════════════════════════════════════════════════════════════════

    async def _preflight_llama_cpp(self) -> dict | None:
        """
        Verify the local llama-cpp server is running before sending
        a request.

        Returns a `final` event dict if the preflight fails, or None
        if it's safe to proceed.
        """
        mgr = app_state.get("llama_server")

        if mgr is None:
            return {
                "type": "final",
                "content": (
                    "The local llama-cpp server is not initialised. "
                    "Check the backend log — the manager failed to "
                    "start. Then retry from Settings → LLM Runtime."
                ),
                "messages": [],
            }

        if not mgr.is_running:
            return {
                "type": "final",
                "content": (
                    "The local llama-cpp server is not running.\n\n"
                    "Open Settings → LLM Runtime and click Load, "
                    "or use the 'Load GGUF' button in the chat header.\n\n"
                    "If llama-cpp fails to start, run this command in "
                    "a terminal to see the exact error:\n"
                    "    runtime\\python\\python.exe "
                    "scripts\\llama_cpp_runner.py"
                ),
                "messages": [],
            }

        try:
            if not await mgr.is_ready():
                return {
                    "type": "final",
                    "content": (
                        "The local llama-cpp server process is running "
                        "but not responding to /v1/models. It may still "
                        "be loading the model. Wait a moment and retry."
                    ),
                    "messages": [],
                }
        except Exception as exc:
            logger.warning("llama-cpp readiness check raised: {}", exc)
            return {
                "type": "final",
                "content": (
                    f"The local llama-cpp server readiness check failed: "
                    f"{exc}"
                ),
                "messages": [],
            }

        return None

    # ══════════════════════════════════════════════════════════════════
    #  Public entry point
    # ══════════════════════════════════════════════════════════════════

    async def run(
        self,
        user_message: str,
        history: list[dict],
        model: str | None = None,
        session_id: str | None = None,
        disable_tools: bool = False,
        provider: str = "auto",
    ) -> AsyncGenerator[dict, None]:
        """
        Run one agent turn, yielding events as they occur.

        See the module docstring for the event schema and the provider
        routing rules.
        """
        # ── llama_cpp pre-flight ─────────────────────────────────────
        if provider in _LLAMA_CPP_PROVIDERS:
            preflight = await self._preflight_llama_cpp()
            if preflight is not None:
                preflight["messages"] = list(history)
                yield preflight
                return

        # ── Ollama unload-on-switch ──────────────────────────────────
        await self._maybe_unload_previous(model, provider)

        # ── Build the message list ───────────────────────────────────
        messages: list[dict] = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + list(history)
            + [{"role": "user", "content": user_message}]
        )

        effective_model = model or settings.llm_model
        use_tools_globally = should_use_tools(
            effective_model, request_disable=disable_tools,
        )

        tools = (
            await self.mcp.list_tools_openai()
            if use_tools_globally else None
        )

        seen_calls: set[tuple[str, str]] = set()
        total_calls = 0

        for iteration in range(settings.max_iterations):
            logger.debug(
                "Agent iteration {}/{} model={} provider={} tools={}",
                iteration + 1,
                settings.max_iterations,
                effective_model,
                provider,
                "yes" if tools else "no",
            )

            turn_tools = (
                tools
                if (tools and total_calls < MAX_TOOL_CALLS_PER_TURN)
                else None
            )
            if turn_tools is None and total_calls >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning(
                    "Tool call cap reached ({}). Forcing final answer.",
                    MAX_TOOL_CALLS_PER_TURN,
                )

            # ── Run one LLM turn ─────────────────────────────────────
            content = ""
            tool_calls_buffer: dict[int, dict] = {}

            async for ev in self._run_llm_turn(
                messages, turn_tools, model, disable_tools, provider,
            ):
                if ev.get("__internal__") == "turn_failed":
                    final = {
                        "type": "final",
                        "content": ev["content"],
                        "messages": messages[1:],
                    }
                    if session_id and self.store:
                        await self.store.save(session_id, messages[1:])
                    yield final
                    return

                if ev.get("__internal__") == "turn_result":
                    content = ev["content"]
                    tool_calls_buffer = ev["tool_calls"]
                    break

                yield ev

            # ── Append the assistant message to the conversation ─────
            #
            # content is coerced to "" when empty, never None.
            #
            # Rationale: OpenAI accepts content: null on assistant
            # messages that carry tool_calls, but llama-cpp-python's
            # Pydantic validator rejects null and returns HTTP 500
            # with seven validation errors. An empty string is
            # accepted by every OpenAI-compatible server we have
            # tested.
            assistant_msg: dict = {
                "role": "assistant",
                "content": content or "",
            }
            if tool_calls_buffer:
                assistant_msg["tool_calls"] = [
                    {
                        "id": buf["id"],
                        "type": "function",
                        "function": {
                            "name": buf["name"],
                            "arguments": buf["arguments"],
                        },
                    }
                    for buf in tool_calls_buffer.values()
                ]
            messages.append(assistant_msg)

            # ── No tool calls → final answer ─────────────────────────
            if not tool_calls_buffer:
                final = {
                    "type": "final",
                    "content": content or "",
                    "messages": messages[1:],
                }
                if session_id and self.store:
                    await self.store.save(session_id, messages[1:])
                yield final
                return

            # ── Execute tool calls ───────────────────────────────────
            for buf in tool_calls_buffer.values():
                name = buf["name"]
                raw_args = buf["arguments"] or "{}"

                try:
                    parsed = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    parsed = {}

                key = (name, json.dumps(parsed, sort_keys=True))

                if key in seen_calls:
                    logger.warning(
                        "Skipping duplicate tool call: {}({})",
                        name, raw_args,
                    )
                    result = (
                        f"(skipped: {name} was already called with these "
                        f"exact arguments in this turn — produce a final "
                        f"answer now)"
                    )
                    yield {
                        "type": "tool_result",
                        "name": name,
                        "result": result,
                    }
                    messages.append({
                        "role": "tool",
                        "tool_call_id": buf["id"],
                        "content": result,
                    })
                    continue

                seen_calls.add(key)
                total_calls += 1

                yield {
                    "type": "tool_call",
                    "name": name,
                    "args": raw_args,
                }

                try:
                    result = await self.mcp.call_tool(name, parsed)
                except Exception as exc:
                    logger.exception("Tool '{}' raised", name)
                    result = f"Error calling tool '{name}': {exc}"

                yield {
                    "type": "tool_result",
                    "name": name,
                    "result": result,
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": buf["id"],
                    "content": result,
                })

        # ── Max iterations reached ───────────────────────────────────
        final = {
            "type": "final",
            "content": (
                f"Reached max iterations ({settings.max_iterations}) "
                "without a final answer. Try rephrasing or increasing "
                "AGENT_MAX_ITERATIONS."
            ),
            "messages": messages[1:],
        }
        if session_id and self.store:
            await self.store.save(session_id, messages[1:])
        yield final

    # ══════════════════════════════════════════════════════════════════
    #  Internal: one LLM turn
    # ══════════════════════════════════════════════════════════════════

    async def _run_llm_turn(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        model: str | None,
        disable_tools: bool,
        provider: str,
    ) -> AsyncGenerator[dict, None]:
        """
        Run one LLM turn with retries, fallback, and tool-support
        fallback.

        Yields token / thinking / retry / fallback events, then a
        sentinel dict with `__internal__` set to either "turn_result"
        or "turn_failed".
        """
        effective_model = model or settings.llm_model
        allow_tools = (
            tools
            if (tools and should_use_tools(effective_model, disable_tools))
            else None
        )

        primary_error: Exception | None = None
        fallback_error: Exception | None = None

        # ── Phase 1: primary provider ────────────────────────────────
        attempt = 0
        while attempt < settings.retry_attempts:
            try:
                state: dict[str, Any] = {"content": "", "tool_calls": {}}
                async for ev in self._consume_stream(
                    self.llm.chat_stream(
                        messages,
                        tools=allow_tools,
                        model=model,
                        provider=provider,
                        disable_tools=disable_tools,
                    ),
                    state,
                ):
                    yield ev

                yield {
                    "__internal__": "turn_result",
                    "content": state["content"],
                    "tool_calls": state["tool_calls"],
                }
                return

            except openai.BadRequestError as exc:
                if is_no_tools_error(exc) and allow_tools:
                    mark_model_without_tools(effective_model)
                    yield {
                        "type": "info",
                        "message": (
                            f"Model '{effective_model}' does not support "
                            f"tool use. Continuing without tools."
                        ),
                    }
                    allow_tools = None
                    continue
                primary_error = exc
                break

            except Exception as exc:
                if not is_retryable(exc):
                    primary_error = exc
                    break
                primary_error = exc
                attempt += 1
                if attempt < settings.retry_attempts:
                    wait = settings.retry_backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Primary LLM failed (attempt {}/{}): {}. "
                        "Retrying in {:.1f}s…",
                        attempt, settings.retry_attempts, exc, wait,
                    )
                    yield {
                        "type": "retry",
                        "message": (
                            f"LLM connection failed — retrying "
                            f"(attempt {attempt + 1}/"
                            f"{settings.retry_attempts})…"
                        ),
                    }
                    await asyncio.sleep(wait)

        # ── Phase 2: fallback ────────────────────────────────────────
        if (
            provider not in _LLAMA_CPP_PROVIDERS
            and settings.fallback_enabled
            and self.llm.fallback is not None
        ):
            fb_model = settings.llm_fallback_model or "fallback model"
            yield {
                "type": "fallback",
                "message": (
                    f"Primary LLM unavailable — switching to fallback "
                    f"({fb_model}). Reason: {primary_error}"
                ),
            }
            allow_tools = (
                tools
                if (tools and should_use_tools(fb_model, disable_tools))
                else None
            )

            attempt = 0
            while attempt < settings.retry_attempts:
                try:
                    state = {"content": "", "tool_calls": {}}
                    async for ev in self._consume_stream(
                        self.llm.chat_stream_fallback(
                            messages,
                            tools=allow_tools,
                            model=None,
                            disable_tools=disable_tools,
                        ),
                        state,
                    ):
                        yield ev

                    yield {
                        "__internal__": "turn_result",
                        "content": state["content"],
                        "tool_calls": state["tool_calls"],
                    }
                    return

                except openai.BadRequestError as exc:
                    if is_no_tools_error(exc) and allow_tools:
                        mark_model_without_tools(fb_model)
                        yield {
                            "type": "info",
                            "message": (
                                f"Fallback model '{fb_model}' does not "
                                f"support tools. Continuing without tools."
                            ),
                        }
                        allow_tools = None
                        continue
                    fallback_error = exc
                    break

                except Exception as exc:
                    if not is_retryable(exc):
                        fallback_error = exc
                        break
                    fallback_error = exc
                    attempt += 1
                    if attempt < settings.retry_attempts:
                        wait = settings.retry_backoff * (2 ** (attempt - 1))
                        yield {
                            "type": "retry",
                            "message": (
                                f"Fallback LLM failed — retrying "
                                f"(attempt {attempt + 1}/"
                                f"{settings.retry_attempts})…"
                            ),
                        }
                        await asyncio.sleep(wait)

        # ── Both failed ──────────────────────────────────────────────
        msg = f"Primary LLM error: {primary_error}"
        if fallback_error is not None:
            msg += f"\nFallback LLM error: {fallback_error}"
        elif (
            not settings.fallback_enabled
            and provider not in _LLAMA_CPP_PROVIDERS
        ):
            msg += (
                "\nNo fallback LLM configured. Set "
                "AGENT_LLM_FALLBACK_BASE_URL in .env to enable failover."
            )

        yield {"__internal__": "turn_failed", "content": msg}

    # ══════════════════════════════════════════════════════════════════
    #  Internal: consume one delta stream
    # ══════════════════════════════════════════════════════════════════

    async def _consume_stream(
        self,
        stream,
        state: dict[str, Any],
    ) -> AsyncGenerator[dict, None]:
        """
        Consume one streaming response, emitting thinking and token
        events as they arrive.

        Mutates `state` in place:

            state["content"]     — the final-answer text so far
            state["tool_calls"]  — {index: {id, name, arguments}}

        Reasoning tokens are NOT added to state["content"]. That
        means reasoning never leaks into the persisted conversation
        history.
        """
        show_thinking = settings.show_thinking
        splitter = ThinkingSplitter(enabled=show_thinking)

        tool_calls_buffer: dict[int, dict] = {}
        content_accum = ""
        last_event_at = time.monotonic()
        stall_timeout = settings.stream_stall_timeout
        got_any = False

        try:
            async for delta in stream:
                got_any = True
                last_event_at = time.monotonic()

                # ── DeepSeek-style separate reasoning field ──────────
                reasoning = _extract_reasoning_content(delta)
                if reasoning and show_thinking:
                    yield {"type": "thinking", "content": reasoning}

                # ── Standard content, possibly with inline markers ──
                if delta.content:
                    for kind, content in splitter.feed(delta.content):
                        if kind == "thinking":
                            if show_thinking:
                                yield {
                                    "type": "thinking",
                                    "content": content,
                                }
                        else:
                            content_accum += content
                            if settings.stream_tokens:
                                yield {
                                    "type": "token",
                                    "content": content,
                                }

                # ── Tool call deltas ─────────────────────────────────
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        buf = tool_calls_buffer[idx]
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                buf["name"] = tc.function.name
                            if tc.function.arguments:
                                buf["arguments"] += tc.function.arguments

                # ── Stall detection ──────────────────────────────────
                if time.monotonic() - last_event_at > stall_timeout:
                    raise TimeoutError(
                        f"No tokens received for {stall_timeout:.0f}s. "
                        f"Aborting turn."
                    )

        finally:
            try:
                await stream.aclose()
            except Exception:
                pass

        # ── Flush the splitter's tail ────────────────────────────────
        for kind, content in splitter.flush():
            if kind == "thinking":
                if show_thinking:
                    yield {"type": "thinking", "content": content}
            else:
                content_accum += content
                if settings.stream_tokens:
                    yield {"type": "token", "content": content}

        if not got_any:
            raise RuntimeError("LLM stream returned no chunks")

        state["content"] = content_accum
        state["tool_calls"] = tool_calls_buffer