"""
Multi-provider LLM client.
https://3olution.com

Routes requests to one of three OpenAI-compatible endpoints:

    primary   -- the main endpoint (default: Ollama)
    fallback  -- an optional secondary endpoint, tried only after the
                 primary has exhausted its retries
    llama_cpp -- the locally-managed llama-server subprocess
                 (either the native llama-server.exe binary or the
                 Python llama_cpp.server module)

================================================================
  DESIGN NOTES
================================================================

  All three endpoints speak the OpenAI Chat Completions wire format,
  so a single `AsyncOpenAI` class serves all of them. The only thing
  that differs between them is the base URL, API key, default model,
  and (for llama-cpp) whether the server is currently running.

  The agent loop selects a provider per turn. The `provider` string
  is resolved by `_resolve()` into a (client, default_model) pair.
  Aliases ("local", "llamacpp") map to the canonical name so callers
  are free to spell it however the frontend happens to.

================================================================
  THE disable_tools PARAMETER
================================================================

  Both `chat()` and `chat_stream()` accept a `disable_tools` flag.
  When true, the `tools` parameter is stripped from the outgoing
  request regardless of what the caller passed in.

  This is a second layer of defence on top of `should_use_tools()`:
  the agent loop already computes `allow_tools` from the model name
  and the global config, but passing `disable_tools` through to the
  client guarantees that a stray tool schema never reaches a model
  that will reject it, even if a future caller forgets the check.

================================================================
  ERROR CLASSIFICATION
================================================================

  Not all exceptions are worth retrying. The retry helper in
  backend/retry.py accepts a `should_retry(exc) -> bool` predicate,
  and this module provides `is_retryable(exc)`:

    Retryable:
      - Any connection error (network blip, DNS, timeout)
      - HTTP 5xx, EXCEPT those whose body clearly indicates a
        permanent condition (missing model, unsupported template)

    Not retryable:
      - HTTP 400 (bad request -- the request itself is wrong)
      - HTTP 401 / 403 (auth -- retrying won't help)
      - HTTP 404 (model not found -- retrying won't help)
      - HTTP 5xx whose message matches a permanent-error marker

  The marker list is deliberately conservative. Retrying a permanent
  error costs three waits of 1s, 2s, 4s for nothing.

================================================================
  TOOL-SUPPORT CACHE
================================================================

  Some models reject the `tools` parameter outright with HTTP 400
  "model does not support tools". Retrying won't fix it -- the model
  simply cannot function-call.

  Rather than let that error reach the user, we cache the model name
  in a module-level set the first time it fails, and strip `tools`
  from every subsequent request to that model. The user sees a
  one-time info event ("this model does not support tools") and then
  the conversation continues normally.

  The cache is in-memory and per-process. It is reset on restart,
  which is fine -- the first request after a restart re-learns it.

  Users can pre-populate the cache via AGENT_NO_TOOLS_MODELS, which
  is useful when they already know a model's limitations.

================================================================
  THE llama_cpp PROVIDER AND THE ALIAS
================================================================

  The native llama-server.exe binary is spawned with
  `--alias <AGENT_LLAMA_CPP_ALIAS>`. The server advertises that
  alias as the model ID in /v1/models. Every chat and embedding
  request must send the same ID, or the server returns 404.

  `_resolve()` therefore returns `settings.llama_cpp_alias`
  (default "local") as the default_model for the llama_cpp
  provider. It does NOT resolve a file path, because the server
  has already been told what file to load and only knows the alias.

  This is the same strategy used for the primary and fallback
  endpoints: the client sends a stable ID, not an implementation
  detail like a file path.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Literal

import openai
from loguru import logger
from openai import AsyncOpenAI

from .config import settings
from .retry import retry_async


# ======================================================================
#  Types
# ======================================================================

Provider = Literal["auto", "primary", "ollama", "fallback", "llama_cpp"]


# ======================================================================
#  Error classification constants
# ======================================================================
#
# HTTP 5xx responses whose bodies contain any of these substrings are
# NOT retried, because the condition is permanent for this request.

_PERMANENT_500_MARKERS = (
    "unknown pre-tokenizer",
    "error loading model",
    "llama-server process has terminated",
    "failed to load model",
    "does not support tools",
    "model not found",
)


# ======================================================================
#  Error classification functions
# ======================================================================

def is_retryable(exc: BaseException) -> bool:
    """
    Return True if the given exception is worth retrying.

    Used by backend/retry.py's `should_retry` predicate.
    """
    # 4xx responses are almost always permanent -- auth, bad request,
    # missing model. Retrying costs time and logs noise.
    if isinstance(exc, openai.BadRequestError):
        return False
    if isinstance(exc, openai.AuthenticationError):
        return False
    if isinstance(exc, openai.PermissionDeniedError):
        return False
    if isinstance(exc, openai.NotFoundError):
        return False

    # 5xx: check the message for known-permanent markers.
    if isinstance(exc, openai.InternalServerError):
        text = str(exc).lower()
        for marker in _PERMANENT_500_MARKERS:
            if marker in text:
                return False
        return True

    # Everything else (connection errors, timeouts, rate limits) is
    # worth a retry.
    return True


def is_no_tools_error(exc: BaseException) -> bool:
    """
    Return True if the exception indicates the model rejects the
    `tools` parameter.

    Different providers phrase this differently:

        OpenAI:   "model does not support tools"
        Groq:     "tools are not supported by this model"
        Together: "invalid_request_error: tools not supported"

    We match any message containing both "tool" and a negation.
    """
    if not isinstance(exc, openai.BadRequestError):
        return False
    msg = str(exc).lower()
    return (
        "does not support tools" in msg
        or "tools are not supported" in msg
        or ("tool" in msg and "not supported" in msg)
    )


# ======================================================================
#  Runtime cache of models known to not support tools
# ======================================================================

_MODELS_WITHOUT_TOOLS: set[str] = set()


def mark_model_without_tools(model: str) -> None:
    """
    Record that a model does not support function calling.

    Called by the agent loop the first time a model returns the
    "does not support tools" error. Subsequent requests to that
    model skip the `tools` parameter entirely.
    """
    if model and model not in _MODELS_WITHOUT_TOOLS:
        _MODELS_WITHOUT_TOOLS.add(model)
        logger.warning(
            "Cached '{}' as a non-tool model. Subsequent requests will "
            "omit the tools parameter for this model.",
            model,
        )


def should_use_tools(model: str, request_disable: bool = False) -> bool:
    """
    Return True if tools should be sent to the given model.

    Checks four conditions in order:

      1. The caller explicitly disabled tools for this request
         (the "No tools" toggle in the chat UI).
      2. AGENT_DISABLE_TOOLS is true (global kill switch).
      3. The model is in AGENT_NO_TOOLS_MODELS (user-configured).
      4. The model has previously returned a "does not support tools"
         error (auto-cached during this process lifetime).
    """
    if request_disable:
        return False
    if settings.disable_tools:
        return False
    if model and model in settings.no_tools_model_list:
        return False
    if model and model in _MODELS_WITHOUT_TOOLS:
        return False
    return True


# ======================================================================
#  LLMClient
# ======================================================================

class LLMClient:
    """
    Wraps up to three OpenAI-compatible endpoints behind a single
    provider-routed interface.

    The three AsyncOpenAI instances are created at construction time.
    They do not open any sockets -- the SDK is lazy. Connections are
    established on the first request to each endpoint.
    """

    def __init__(self) -> None:
        # -- Primary endpoint -----------------------------------------
        self.primary = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "ollama",
            timeout=settings.llm_timeout,
        )

        # -- Fallback endpoint (optional) -----------------------------
        self.fallback: AsyncOpenAI | None = None
        if settings.fallback_enabled:
            self.fallback = AsyncOpenAI(
                base_url=settings.llm_fallback_base_url,
                api_key=settings.llm_fallback_api_key or "not-needed",
                timeout=settings.llm_fallback_timeout,
            )
            logger.info(
                "Fallback LLM configured: base_url={} model={}",
                settings.llm_fallback_base_url,
                settings.llm_fallback_model or "(default)",
            )

        # -- llama-cpp endpoint ---------------------------------------
        #
        # This client always exists. Whether the server is actually
        # running is checked by the agent's pre-flight before routing
        # to it. A request to a stopped llama-cpp server fails fast
        # with a connection error rather than hanging.
        #
        # The base URL is the same whether the backend is the native
        # llama-server.exe or the Python llama_cpp.server module --
        # both expose the OpenAI-compatible /v1 surface on the same
        # host and port.
        self.llama_cpp = AsyncOpenAI(
            base_url=settings.llama_cpp_base_url.rstrip("/") + "/v1",
            api_key=settings.llama_cpp_api_key or "not-needed",
            timeout=settings.llama_cpp_startup_timeout,
        )

    # ------------------------------------------------------------------
    #  Provider resolution
    # ------------------------------------------------------------------

    def _resolve(self, provider: str) -> tuple[AsyncOpenAI, str]:
        """
        Return (client, default_model) for a provider name.

        Raises RuntimeError if the requested provider is not
        available (e.g. "fallback" with no fallback configured).
        """
        p = (provider or "auto").lower()

        if p in ("llama_cpp", "llamacpp", "local"):
            # The native llama-server is spawned with --alias <name>,
            # so it advertises a short stable ID in /v1/models. The
            # backend must send that same ID in every request, or the
            # server returns 404 (or hangs) on every call.
            #
            # No path resolution is needed: the alias is a fixed
            # string in .env (AGENT_LLAMA_CPP_ALIAS, default "local").
            #
            # The same strategy applies when the backend is the
            # Python llama_cpp.server module -- that module is also
            # started with --model_alias, so both backends behave
            # identically here.
            default_model = settings.llama_cpp_alias or "local"

            logger.debug(
                "llama_cpp provider resolved: default_model='{}' (alias)",
                default_model,
            )

            return self.llama_cpp, default_model

        if p == "fallback":
            if self.fallback is None:
                raise RuntimeError(
                    "Fallback LLM requested but AGENT_LLM_FALLBACK_BASE_URL "
                    "is not set."
                )
            return self.fallback, settings.llm_fallback_model

        # "auto" / "primary" / "ollama" / "" all route to the primary.
        return self.primary, settings.llm_model

    # ------------------------------------------------------------------
    #  Warmup
    # ------------------------------------------------------------------

    async def warmup(self) -> bool:
        """
        Send a one-token "ping" to the primary endpoint.

        The first request to a cold Ollama model can take 20-40 s to
        load weights into memory. Doing this at startup moves the
        cost off the first user message.

        Returns True on success. Never raises -- failures are logged
        and the app continues (the model will load on first use
        instead).
        """
        logger.info(
            "Warming up primary LLM (model={}) -- this may take a while...",
            settings.llm_model,
        )
        try:
            await self.primary.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0.0,
            )
            logger.info(
                "Primary LLM warmup complete (model={})",
                settings.llm_model,
            )
            return True
        except Exception as exc:
            logger.warning("Primary LLM warmup failed: {}", exc)
            return False

    # ------------------------------------------------------------------
    #  Non-streaming chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        provider: str = "auto",
        disable_tools: bool = False,
        **kwargs: Any,
    ):
        """
        Send a non-streaming chat completion request.

        Wraps the SDK call in the retry helper with the
        `is_retryable` predicate so auth and 400-class errors fail
        fast.

        When `disable_tools` is True, the `tools` parameter is
        stripped before the request is sent.
        """
        client, default_model = self._resolve(provider)
        effective_model = model or default_model

        # Strip tools when the model cannot handle them.
        if tools and not should_use_tools(effective_model, disable_tools):
            tools = None

        params: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": kwargs.pop("temperature", settings.llm_temperature),
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        params.update(kwargs)

        logger.debug(
            "LLM request ({}, batch): model={} messages={} tools={}",
            provider,
            params["model"],
            len(messages),
            len(tools) if tools else 0,
        )

        async def _do_call():
            return await client.chat.completions.create(**params)

        return await retry_async(
            _do_call,
            attempts=settings.retry_attempts,
            backoff=settings.retry_backoff,
            should_retry=is_retryable,
            label=f"LLM {provider} batch call (model={params['model']})",
        )

    async def chat_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        disable_tools: bool = False,
        **kwargs: Any,
    ):
        """
        Non-streaming chat against the fallback endpoint.

        Convenience wrapper that hard-codes provider="fallback".
        """
        return await self.chat(
            messages,
            tools=tools,
            model=model,
            provider="fallback",
            disable_tools=disable_tools,
            **kwargs,
        )

    # ------------------------------------------------------------------
    #  Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        provider: str = "auto",
        disable_tools: bool = False,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """
        Stream chat completions from the selected provider.

        Yields the `delta` from each chunk. The caller (agent.py) is
        responsible for accumulating content and tool calls.

        `disable_tools=True` strips the `tools` parameter from the
        request regardless of what the caller passed in. The agent
        loop always passes its computed `disable_tools` through, so
        a stray tool schema never reaches a model that will reject
        it.
        """
        client, default_model = self._resolve(provider)
        effective_model = model or default_model

        if tools and not should_use_tools(effective_model, disable_tools):
            tools = None

        async for delta in self._stream_open(
            client,
            default_model=default_model,
            label=f"LLM {provider} stream",
            messages=messages,
            tools=tools,
            model=model,
            **kwargs,
        ):
            yield delta

    async def chat_stream_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        disable_tools: bool = False,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """Streaming chat against the fallback endpoint."""
        async for delta in self.chat_stream(
            messages,
            tools=tools,
            model=model,
            provider="fallback",
            disable_tools=disable_tools,
            **kwargs,
        ):
            yield delta

    async def _stream_open(
        self,
        client: AsyncOpenAI,
        default_model: str,
        label: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """
        Shared streaming implementation.

        Retries the *initial open* of the stream. Once the stream is
        open, mid-stream errors propagate to the caller, which
        typically retries the whole turn at a higher level.
        """
        params: dict[str, Any] = {
            "model": model or kwargs.pop("model", default_model),
            "messages": messages,
            "temperature": kwargs.pop("temperature", settings.llm_temperature),
            "stream": True,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        params.update(kwargs)

        logger.debug(
            "{}: model={} messages={} tools={}",
            label,
            params["model"],
            len(messages),
            len(tools) if tools else 0,
        )

        stream = await retry_async(
            lambda: client.chat.completions.create(**params),
            attempts=settings.retry_attempts,
            backoff=settings.retry_backoff,
            should_retry=is_retryable,
            label=f"{label} open (model={params['model']})",
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            yield chunk.choices[0].delta

    # ------------------------------------------------------------------
    #  Model listing
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """
        Return model IDs from the primary endpoint.

        Not all OpenAI-compatible endpoints implement /v1/models.
        A failure here is non-fatal -- the settings page falls back
        to the Ollama management API instead.
        """
        try:
            models = await self.primary.models.list()
            return [m.id for m in models.data]
        except Exception as exc:
            logger.warning("Could not list models from primary: {}", exc)
            return []