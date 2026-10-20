"""
Ollama management API.
https://3olution.com

Endpoints:

    GET    /api/ollama/status                 — connectivity check
    GET    /api/ollama/models                 — list installed models
    GET    /api/ollama/ps                     — list loaded models
    POST   /api/ollama/unload                 — unload a model from memory
    GET    /api/ollama/models/{name}          — show one model's details
    POST   /api/ollama/models/pull            — pull a model (SSE streaming)
    DELETE /api/ollama/models/{name}          — delete a model from disk
    GET    /api/ollama/settings               — runtime flags
    POST   /api/ollama/settings               — update runtime flags

================================================================
  PROXYING NOTES
================================================================

  Every endpoint here forwards to Ollama's native REST API
  (/api/*) rather than the OpenAI-compatible one (/v1/*). The
  native API is the only surface that exposes pull streaming,
  loaded-model state, and delete.

  The routes do not cache anything — each request creates a fresh
  OllamaClient and issues a fresh upstream call. Ollama is local
  and fast; caching would add complexity without benefit.

  Error handling: when Ollama is unreachable, the client raises
  httpx.HTTPError. Routes translate that into HTTP 502 with the
  original message in the `detail` field, so the settings page can
  display a meaningful error.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..config import settings
from ..ollama import OllamaClient


router = APIRouter(prefix="/api/ollama", tags=["ollama"])


# ══════════════════════════════════════════════════════════════════════
#  Request models
# ══════════════════════════════════════════════════════════════════════

class PullRequest(BaseModel):
    """Payload for POST /api/ollama/models/pull."""
    name: str = Field(
        ...,
        description="Model name and optional tag, e.g. 'llama3.2' or 'mistral:7b'.",
        min_length=1,
    )


class UnloadRequest(BaseModel):
    """Payload for POST /api/ollama/unload."""
    name: str | None = Field(
        default=None,
        description=(
            "Model to unload. When omitted, every loaded model is unloaded."
        ),
    )


class ThinkingSetting(BaseModel):
    """Payload for POST /api/ollama/settings."""
    show_thinking: bool = Field(
        ...,
        description="Whether reasoning tokens are streamed to the UI.",
    )


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _client() -> OllamaClient:
    """Create a fresh client bound to the configured host."""
    return OllamaClient()


# ══════════════════════════════════════════════════════════════════════
#  Status
# ══════════════════════════════════════════════════════════════════════

@router.get("/status")
async def ollama_status():
    """
    Report whether the Ollama server is reachable and its version.

    Used by the settings page to render the connection indicator.
    """
    client = _client()
    available = await client.is_available()
    version = await client.version() if available else None
    return {
        "available": available,
        "host": client.host,
        "version": version,
    }


# ══════════════════════════════════════════════════════════════════════
#  List models
# ══════════════════════════════════════════════════════════════════════

@router.get("/models")
async def list_models():
    """
    Return every model installed on the Ollama server.

    Enriches each entry with `size_gb` for the frontend and sorts
    by name. The raw `details` object is passed through unchanged.
    """
    client = _client()
    try:
        models = await client.list_models()
    except Exception as exc:
        logger.warning("Ollama list_models failed: {}", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Ollama unreachable: {exc}",
        )

    out = []
    for m in models:
        out.append({
            "name": m.get("name"),
            "model": m.get("model"),
            "size": m.get("size", 0),
            "size_gb": round(m.get("size", 0) / 1e9, 2),
            "digest": m.get("digest"),
            "modified_at": m.get("modified_at"),
            "details": m.get("details", {}),
        })
    out.sort(key=lambda x: x["name"] or "")
    return {"models": out, "count": len(out)}


# ══════════════════════════════════════════════════════════════════════
#  Loaded models (/api/ps)
# ══════════════════════════════════════════════════════════════════════

@router.get("/ps")
async def list_running():
    """
    Return models currently loaded into Ollama's memory.

    Unlike /models, this returns only what is warm and ready to
    answer immediately. The frontend uses this as a busy indicator:
    the presence of an entry means the model is loaded.

    If Ollama is unreachable, returns an empty list rather than an
    error — the busy indicator degrades gracefully to "idle".
    """
    client = _client()
    try:
        models = await client.list_loaded()
    except Exception as exc:
        logger.debug("Ollama ps failed: {}", exc)
        return {"models": [], "count": 0}

    out = []
    for m in models:
        out.append({
            "name": m.get("name"),
            "size": m.get("size", 0),
            "size_vram": m.get("size_vram", 0),
            "expires_at": m.get("expires_at"),
            "details": m.get("details", {}),
        })
    return {"models": out, "count": len(out)}


# ══════════════════════════════════════════════════════════════════════
#  Unload
# ══════════════════════════════════════════════════════════════════════

@router.post("/unload")
async def unload_model(req: UnloadRequest):
    """
    Unload one model, or all loaded models, from Ollama's memory.

    Ollama auto-unloads models after an idle period (5 min by
    default). This endpoint is for the case where the user wants
    to free VRAM right now — for example, before loading a larger
    model that will not fit alongside the current one.
    """
    client = _client()

    # Build the list of names to unload.
    names: list[str] = []
    if req.name:
        names = [req.name]
    else:
        try:
            loaded = await client.list_loaded()
            names = [m["name"] for m in loaded if m.get("name")]
        except Exception:
            names = []

    unloaded: list[str] = []
    errors: list[dict] = []

    for name in names:
        ok = await client.unload_model(name)
        if ok:
            unloaded.append(name)
        else:
            errors.append({"name": name, "error": "unload failed"})

    return {"unloaded": unloaded, "errors": errors}


# ══════════════════════════════════════════════════════════════════════
#  Show one model
# ══════════════════════════════════════════════════════════════════════

@router.get("/models/{name:path}")
async def show_model(name: str):
    """
    Return detailed information about one model.

    The response is Ollama's raw /api/show payload: modelfile,
    template, system prompt, and parameter list.
    """
    client = _client()
    try:
        return await client.show_model(name)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama error: {exc}",
        )


# ══════════════════════════════════════════════════════════════════════
#  Pull (SSE streaming)
# ══════════════════════════════════════════════════════════════════════

@router.post("/models/pull")
async def pull_model(req: PullRequest):
    """
    Pull a model from the Ollama registry.

    Streams Ollama's NDJSON progress events as Server-Sent Events.
    Each frame is a `data: {...}` line. The stream ends with
    `data: [DONE]`.

    On error, emits a single frame with an `error` field, then the
    [DONE] terminator. The client can detect the error without
    needing to distinguish error frames from progress frames — it
    checks for the `error` key.
    """
    client = _client()

    async def event_stream():
        try:
            async for event in client.pull_model(req.name):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.exception("Pull failed for {}", req.name)
            err = json.dumps({"error": str(exc)})
            yield f"data: {err}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ══════════════════════════════════════════════════════════════════════
#  Delete
# ══════════════════════════════════════════════════════════════════════

@router.delete("/models/{name:path}")
async def delete_model(name: str):
    """
    Delete a model from disk.

    The `:path` converter allows names like `library/llama3.2` and
    `namespace/model:tag` to be passed without URL encoding.
    """
    client = _client()
    try:
        await client.delete_model(name)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama error: {exc}",
        )
    return {"status": "deleted", "name": name}


# ══════════════════════════════════════════════════════════════════════
#  Runtime settings
# ══════════════════════════════════════════════════════════════════════

@router.get("/settings")
async def get_settings():
    """
    Return runtime-adjustable flags.

    These flags take effect immediately and are stored in-process.
    They are NOT persisted to .env — a backend restart reverts them
    to the values in .env.
    """
    return {
        "show_thinking": settings.show_thinking,
        "unload_on_switch": settings.ollama_unload_on_switch,
    }


@router.post("/settings")
async def update_settings(req: ThinkingSetting):
    """
    Update one runtime flag.

    Only `show_thinking` is exposed for now. The endpoint exists as
    a stable path so the frontend can add more flags later without a
    new route.
    """
    settings.show_thinking = req.show_thinking
    logger.info("Runtime setting changed: show_thinking={}", req.show_thinking)
    return {
        "show_thinking": settings.show_thinking,
        "unload_on_switch": settings.ollama_unload_on_switch,
    }