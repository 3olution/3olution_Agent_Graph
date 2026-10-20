"""
Async client for the Ollama native HTTP API.
https://3olution.com

Used by the settings page to list, pull, and delete models, and to
inspect which models are currently loaded into memory.

================================================================
  NATIVE vs OPENAI-COMPATIBLE
================================================================

  Ollama exposes two HTTP APIs on the same port:

    /api/*    — the native API (this file)
    /v1/*     — the OpenAI-compatible API (used by backend/llm.py)

  The native API is richer: it can pull models with progress
  streaming, report which models are loaded, unload them explicitly,
  and describe model architecture. The OpenAI-compatible API cannot
  do any of that — it is strictly for chat completions.

  So the agent talks to /v1/, and the settings page talks to /api/.
  Same server, two surfaces, two clients.

================================================================
  A NOTE ON httpx.delete()
================================================================

  httpx deliberately does not accept a `json=` argument on
  `.delete()`. The HTTP spec says DELETE requests have no defined
  body semantics, and httpx chose to enforce that.

  Ollama's /api/delete endpoint nonetheless requires a JSON body:

      DELETE /api/delete
      Content-Type: application/json

      {"name": "llama3.2"}

  We work around this by using the generic `.request("DELETE", ...)`
  method, which does accept json=. This is the only non-obvious bit
  in this file.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx
from loguru import logger

from .config import settings


class OllamaClient:
    """
    Thin async wrapper around Ollama's native REST API.

    Every method creates a short-lived AsyncClient. This is slightly
    less efficient than sharing one client, but it avoids the
    "client used outside its context manager" class of bugs and
    makes error handling simpler. Ollama's API is low-traffic; the
    overhead is negligible.
    """

    def __init__(self, host: str | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")

    # ══════════════════════════════════════════════════════════════════
    #  Health
    # ══════════════════════════════════════════════════════════════════

    async def is_available(self) -> bool:
        """
        Return True if the Ollama server responds to /api/version.

        Uses a short timeout (3s) so the health check never blocks
        the settings page for long when Ollama is down.
        """
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.host}/api/version")
                return r.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed: {}", exc)
            return False

    async def version(self) -> str | None:
        """Return the Ollama server version string, or None on failure."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/api/version")
                r.raise_for_status()
                return r.json().get("version")
        except Exception as exc:
            logger.warning("Ollama version fetch failed: {}", exc)
            return None

    # ══════════════════════════════════════════════════════════════════
    #  Listing
    # ══════════════════════════════════════════════════════════════════

    async def list_models(self) -> list[dict]:
        """
        Return the list of locally installed models.

        Each entry is the raw dict from Ollama's /api/tags,
        including name, size, digest, modified_at, and details
        (family, parameter_size, quantization_level, etc.).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self.host}/api/tags")
            r.raise_for_status()
            return r.json().get("models", [])

    async def show_model(self, name: str) -> dict:
        """
        Return detailed information about one model.

        Includes the modelfile, template, system prompt, and the
        full parameter list.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{self.host}/api/show",
                json={"name": name},
            )
            r.raise_for_status()
            return r.json()

    # ══════════════════════════════════════════════════════════════════
    #  Loaded models (/api/ps)
    # ══════════════════════════════════════════════════════════════════

    async def list_loaded(self) -> list[dict]:
        """
        Return models currently loaded into memory.

        Used as a busy indicator: the frontend polls this to know
        whether a model is warm and how much VRAM it is using.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/api/ps")
                r.raise_for_status()
                return r.json().get("models", [])
        except Exception as exc:
            logger.debug("Ollama ps failed: {}", exc)
            return []

    # ══════════════════════════════════════════════════════════════════
    #  Pull (streaming)
    # ══════════════════════════════════════════════════════════════════

    async def pull_model(self, name: str) -> AsyncGenerator[dict, None]:
        """
        Stream pull progress events from Ollama.

        Ollama emits NDJSON events like:

            {"status": "pulling manifest"}
            {"status": "downloading", "digest": "...", "total": 123, "completed": 45}
            {"status": "verifying sha256 digest"}
            {"status": "writing manifest"}
            {"status": "success"}

        Yields each parsed event as a dict. Malformed lines are
        skipped — a partially corrupted progress line should not
        abort an otherwise healthy download.
        """
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.host}/api/pull",
                json={"name": name, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Skipping non-JSON pull line: {}", line)
                        continue

    # ══════════════════════════════════════════════════════════════════
    #  Delete
    # ══════════════════════════════════════════════════════════════════

    async def delete_model(self, name: str) -> bool:
        """
        Delete a model from disk.

        Uses `client.request("DELETE", ...)` instead of `.delete()`
        because httpx's `.delete()` does not accept a `json=` kwarg,
        and Ollama's /api/delete requires a JSON body. See the module
        docstring for details.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.request(
                "DELETE",
                f"{self.host}/api/delete",
                json={"name": name},
            )
            r.raise_for_status()
            return True

    # ══════════════════════════════════════════════════════════════════
    #  Unload
    # ══════════════════════════════════════════════════════════════════

    async def unload_model(self, name: str) -> bool:
        """
        Unload a model from Ollama's memory.

        Implemented via /api/generate with `keep_alive: 0` — that is
        the documented way to force an unload. Ollama has no
        dedicated unload endpoint.

        Returns True on success. Never raises.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(
                    f"{self.host}/api/generate",
                    json={"model": name, "keep_alive": 0},
                )
            logger.info("Unloaded Ollama model: {}", name)
            return True
        except Exception as exc:
            logger.debug("Could not unload model {}: {}", name, exc)
            return False