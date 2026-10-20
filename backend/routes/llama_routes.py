"""
llama-cpp-python installation and runtime management.
https://3olution.com

Endpoints:

    GET    /api/llama/install/status          — install state + recommendation
    POST   /api/llama/install/install         — pip install (SSE streaming)
    POST   /api/llama/install/uninstall       — pip uninstall (SSE streaming)
    GET    /api/llama/runtime/models          — GGUF files in models/
    GET    /api/llama/runtime/status          — subprocess state
    POST   /api/llama/runtime/load            — swap the loaded model
    POST   /api/llama/runtime/retry           — restart the subprocess
    POST   /api/llama/runtime/unload          — stop the subprocess
    GET    /api/llama/all-models              — unified Ollama + GGUF list

================================================================
  DESIGN NOTES
================================================================

  The runtime endpoints deliberately keep the manager in state even
  when startup fails. This lets the user fix the problem (install
  the wheel, download a GGUF) and retry from the UI without
  restarting the backend.

  The install and uninstall endpoints stream pip's output as
  Server-Sent Events. The frontend renders each line in a live log
  panel. A final `[DONE]` frame terminates the stream, and the
  UI uses that to re-enable its controls.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from .. import llama_installer
from ..config import settings
from ..models_setup import list_gguf_files, resolve_model_path
from ..state import state


router = APIRouter(prefix="/api/llama", tags=["llama"])


# ══════════════════════════════════════════════════════════════════════
#  Request models
# ══════════════════════════════════════════════════════════════════════

class InstallRequest(BaseModel):
    """Payload for POST /api/llama/install/install."""
    wheel_index: str = Field(
        default="cpu",
        description="One of: cpu, cu118, cu121, cu124.",
    )
    upgrade: bool = Field(
        default=False,
        description="Pass --upgrade to pip to fetch the latest version.",
    )


class LoadRequest(BaseModel):
    """Payload for POST /api/llama/runtime/load."""
    filename: str = Field(
        ...,
        description="GGUF filename (resolved against models/) or absolute path.",
        min_length=1,
    )


# ══════════════════════════════════════════════════════════════════════
#  Manager resolution
# ══════════════════════════════════════════════════════════════════════

def _get_manager(require_running: bool = False):
    """
    Fetch the LlamaServerManager from shared state.

    Raises HTTPException with a specific status code for each
    failure mode, so the frontend can render a targeted message
    rather than a generic 500.
    """
    mgr = state.get("llama_server")

    if not settings.llama_cpp_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "llama-cpp is disabled. Set "
                "AGENT_LLAMA_CPP_ENABLED=true in .env and restart "
                "the backend."
            ),
        )

    if mgr is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "llama-cpp manager is not present in app state. "
                "Check the backend log for a traceback."
            ),
        )

    if require_running and not mgr.is_running:
        raise HTTPException(
            status_code=503,
            detail=(
                "llama-cpp server is not running. Use "
                "POST /api/llama/runtime/load with a GGUF filename "
                "to start it."
            ),
        )

    return mgr


# ══════════════════════════════════════════════════════════════════════
#  Installer — status
# ══════════════════════════════════════════════════════════════════════

@router.get("/install/status")
async def install_status():
    """
    Return the current install state and a wheel recommendation.

    The recommendation is based on detecting the local CUDA version
    via nvidia-smi. On machines without an NVIDIA GPU, it returns
    "cpu".
    """
    state_dict = llama_installer.detect_llama_cpp()
    index, reason = llama_installer.recommend_wheel_index()
    cuda = llama_installer.detect_nvidia_cuda()

    return {
        "installed": state_dict["installed"],
        "version": state_dict["version"],
        "gpu_offload": state_dict["gpu_offload"],
        "error": state_dict["error"],
        "cuda_version": cuda,
        "recommended_wheel": index,
        "recommended_reason": reason,
        "in_venv": llama_installer.in_venv(),
        "venv_path": (
            str(llama_installer.venv_path())
            if llama_installer.in_venv() else None
        ),
        "python": llama_installer.sys.executable,
        "valid_wheels": sorted(llama_installer.VALID_WHEEL_INDICES),
        "wheel_labels": llama_installer.WHEEL_INDEX_LABELS,
    }


# ══════════════════════════════════════════════════════════════════════
#  Installer — install
# ══════════════════════════════════════════════════════════════════════

@router.post("/install/install")
async def install_llama_cpp(req: InstallRequest):
    """
    Install llama-cpp-python[server] with the specified wheel index.

    Streams pip's stdout+stderr as SSE. Each line becomes one
    `data: {"type": "line", "text": "..."}` frame. A success frame
    reports the installed version and whether GPU offload is
    supported. The stream ends with `data: [DONE]`.

    The installation runs in a subprocess. If the user closes the
    browser mid-install, the pip process continues to completion —
    which is what you want, since killing pip mid-install can leave
    a broken package.
    """
    if req.wheel_index not in llama_installer.VALID_WHEEL_INDICES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid wheel index. Use one of: "
                f"{sorted(llama_installer.VALID_WHEEL_INDICES)}"
            ),
        )

    async def event_stream():
        try:
            async for ev in llama_installer.stream_install(
                req.wheel_index,
                upgrade=req.upgrade,
            ):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:
            logger.exception("Install stream failed")
            err = json.dumps({"type": "error", "message": str(exc)})
            yield f"data: {err}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ══════════════════════════════════════════════════════════════════════
#  Installer — uninstall
# ══════════════════════════════════════════════════════════════════════

@router.post("/install/uninstall")
async def uninstall_llama_cpp():
    """
    Uninstall llama-cpp-python.

    Used before switching wheel variants. On Windows, the backend
    must be stopped and restarted between uninstall and install,
    because the pip process cannot delete DLLs still mapped into
    the running Python.
    """
    async def event_stream():
        try:
            async for ev in llama_installer.stream_uninstall():
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:
            logger.exception("Uninstall stream failed")
            err = json.dumps({"type": "error", "message": str(exc)})
            yield f"data: {err}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ══════════════════════════════════════════════════════════════════════
#  Runtime — list models
# ══════════════════════════════════════════════════════════════════════

@router.get("/runtime/models")
async def runtime_list_models():
    """
    List GGUF files available in the models folder.

    Each entry is marked with `loaded: true` only if the server is
    actually running with that file. This is deliberately strict —
    the UI needs to know whether a swap is required to use a file.
    """
    files = list_gguf_files()
    mgr = state.get("llama_server")

    running = bool(mgr and mgr.is_running)
    loaded = mgr.current_model if running else None

    for f in files:
        f["loaded"] = bool(running and f["name"] == loaded)

    return {
        "models": files,
        "count": len(files),
        "loaded": loaded,
        "models_dir": str(settings.models_path),
        "runtime_swap_enabled": settings.llama_cpp_allow_runtime_swap,
        "manager_present": mgr is not None,
        "server_running": running,
    }


# ══════════════════════════════════════════════════════════════════════
#  Runtime — status
# ══════════════════════════════════════════════════════════════════════

@router.get("/runtime/status")
async def runtime_status():
    """
    Return the subprocess lifecycle state.

    Distinguishes four cases the UI needs to render differently:

      enabled=false           llama-cpp is disabled in .env
      manager=false           startup failed, no manager in state
      running=false           manager exists but subprocess is dead
      running=true & ready    fully operational
    """
    mgr = state.get("llama_server")

    if not settings.llama_cpp_enabled:
        return {
            "enabled": False,
            "manager": False,
            "running": False,
            "ready": False,
            "loaded_model": None,
            "base_url": settings.llama_cpp_base_url,
            "models_dir": str(settings.models_path),
            "last_error": "llama-cpp is disabled in .env",
        }

    if mgr is None:
        return {
            "enabled": True,
            "manager": False,
            "running": False,
            "ready": False,
            "loaded_model": None,
            "base_url": settings.llama_cpp_base_url,
            "models_dir": str(settings.models_path),
            "last_error": (
                "manager is missing from state — the backend likely "
                "failed to initialise it. Check the backend log."
            ),
        }

    running = mgr.is_running
    ready = False
    if running:
        try:
            ready = await mgr.is_ready()
        except Exception:
            ready = False

    last_error = None
    if not running:
        last_error = (
            "subprocess is not running. If you just installed "
            "llama-cpp-python, use POST /api/llama/runtime/load with "
            "a GGUF filename to start it."
        )

    return {
        "enabled": True,
        "manager": True,
        "running": running,
        "ready": ready,
        "loaded_model": mgr.current_model if running else None,
        "base_url": mgr.base_url,
        "models_dir": str(settings.models_path),
        "last_error": last_error,
    }


# ══════════════════════════════════════════════════════════════════════
#  Runtime — load a model
# ══════════════════════════════════════════════════════════════════════

@router.post("/runtime/load")
async def runtime_load(req: LoadRequest):
    """
    Swap the loaded model to the given GGUF file.

    If the server is not running, this starts it. If it is running
    with a different model, this stops it and starts it with the new
    one. Either way the operation is atomic from the caller's
    perspective.
    """
    mgr = _get_manager(require_running=False)

    if not settings.llama_cpp_allow_runtime_swap:
        raise HTTPException(
            status_code=403,
            detail=(
                "Runtime model swapping is disabled. Set "
                "AGENT_LLAMA_CPP_ALLOW_RUNTIME_SWAP=true to enable."
            ),
        )

    result = await mgr.load_model(req.filename)
    if not result["ok"]:
        raise HTTPException(
            status_code=500,
            detail=result["error"] or "Load failed",
        )
    return result


# ══════════════════════════════════════════════════════════════════════
#  Runtime — retry start
# ══════════════════════════════════════════════════════════════════════

@router.post("/runtime/retry")
async def runtime_retry():
    """
    Attempt to start the llama-cpp server with the configured model.

    Use case: the user installed llama-cpp-python after the backend
    started, so the initial startup attempt failed. They click
    "Retry" in the settings page instead of restarting the backend.
    """
    mgr = _get_manager(require_running=False)

    if mgr.is_running and await mgr.is_ready():
        return {
            "status": "already_running",
            "model": mgr.current_model,
        }

    ok = await mgr.start()
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=(
                "llama-cpp server failed to start. Check the backend "
                "log for the subprocess output."
            ),
        )
    return {
        "status": "started",
        "model": mgr.current_model,
        "base_url": mgr.base_url,
    }


# ══════════════════════════════════════════════════════════════════════
#  Runtime — unload
# ══════════════════════════════════════════════════════════════════════

@router.post("/runtime/unload")
async def runtime_unload():
    """
    Stop the llama-cpp subprocess.

    Frees the memory the GGUF was using. The model file is not
    deleted — only unloaded from memory. The next /runtime/load
    request will start the subprocess again.
    """
    mgr = _get_manager(require_running=False)
    await mgr.unload()
    return {"status": "unloaded"}


# ══════════════════════════════════════════════════════════════════════
#  Unified model list
# ══════════════════════════════════════════════════════════════════════

@router.get("/all-models")
async def all_models():
    """
    Return a unified list of every model available to the agent.

    Combines two sources:

      Ollama models            — from GET /api/ollama/models
      Local GGUF files         — from the models/ folder

    Each entry has a stable `id` that encodes the provider and model
    name: `provider::model`. The frontend model picker uses this ID
    as the option value, and parses it on submit to route the
    request to the correct backend.

    The GGUF entries are marked `loaded: true` only if the llama-cpp
    subprocess is actually running with that file. This is a live
    query — it reflects the current subprocess state, not the
    configured default.
    """
    from ..ollama import OllamaClient

    out: list[dict] = []

    # ── Ollama models ────────────────────────────────────────────────
    try:
        oc = OllamaClient()
        if await oc.is_available():
            models = await oc.list_models()
            for m in models:
                name = m.get("name")
                if not name:
                    continue
                out.append({
                    "id": f"ollama::{name}",
                    "provider": "ollama",
                    "model": name,
                    "display": f"Ollama · {name}",
                    "size_gb": round(m.get("size", 0) / 1e9, 2),
                    "loaded": None,  # Ollama's loaded state is separate
                })
    except Exception as exc:
        logger.debug("Ollama list failed: {}", exc)

    # ── Local GGUF files ─────────────────────────────────────────────
    mgr = state.get("llama_server")
    running = bool(mgr and mgr.is_running)
    loaded_name = mgr.current_model if running else None

    for f in list_gguf_files():
        is_loaded = bool(running and f["name"] == loaded_name)
        suffix = " (loaded)" if is_loaded else ""
        out.append({
            "id": f"llama_cpp::{f['name']}",
            "provider": "llama_cpp",
            "model": f["name"],
            "display": f"Local GGUF · {f['name']}{suffix}",
            "size_gb": f["size_gb"],
            "loaded": is_loaded,
        })

    return {"models": out, "count": len(out)}