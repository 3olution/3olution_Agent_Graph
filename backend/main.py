"""
3olution Agent Graph — FastAPI application.
https://3olution.com

Serves:
  - /api/health, /api/version      — health & version
  - /api/chat, /ws                 — chat (sync + streaming)
  - /api/chat/stream               — SSE token streaming
  - /api/sessions/*                — session CRUD
  - /api/graph/*, /ws/graph        — graph CRUD, execution, history
  - /api/ollama/*                  — Ollama model management
  - /api/llama/*                   — llama-cpp install and runtime
  - /api/system/*, /api/logs/*     — stop-all, log access
  - /php/*, /php-static/*          — proxied PHP notes app
  - /graph, /settings              — explicit HTML routes
  - /favicon.ico, /favicon.png     — project logo as favicon
  - /                              — static web files

================================================================
  STARTUP SEQUENCE
================================================================

  1. Load logging.
  2. Check .env presence, vendor files, web files.
  3. Create LLMClient, SessionStore, MCPClient.
  4. Initialise the session store (creates tables).
  5. Create and start the LlamaServerManager (best-effort).
  6. Connect the MCP stdio session (best-effort).
  7. Warm up the primary LLM (best-effort).
  8. Register all routers.
  9. Mount static files last.

  Every failure is logged and the app continues. Nothing short of a
  Python import error prevents the backend from starting.

================================================================
  ROUTER SANITY CHECK
================================================================

  Each router registers a minimum expected number of routes. If a
  router comes up short — because a decorator was accidentally
  removed, or a route was commented out, or an import silently
  failed — the startup log will say so at ERROR level rather than
  letting the missing endpoint fail silently at request time.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from . import __version__
from . import llama_server as _llama_module
from .agent import Agent
from .config import settings
from .graph.routes import router as graph_router
from .llama_server import LlamaServerManager
from .llm import LLMClient
from .logging_config import setup_logging
from .mcp_client import MCPClient
from .models_setup import ensure_models_dir
from .routes.llama_routes import router as llama_router
from .routes.ollama_routes import router as ollama_router
from .routes.php_routes import router as php_router
from .routes.system_routes import router as system_router
from .state import state
from .storage import SessionStore

setup_logging()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
VENDOR_DIR = WEB_DIR / "vendor"
ENV_FILE = PROJECT_ROOT / ".env"
VENDOR_JS = VENDOR_DIR / "litegraph.min.js"
VENDOR_CSS = VENDOR_DIR / "litegraph.css"
DOWNLOAD_SCRIPT = PROJECT_ROOT / "scripts" / "download_vendor.py"
PHP_APP_DIR = PROJECT_ROOT / "php_app"


# ══════════════════════════════════════════════════════════════════════
#  Startup helpers
# ══════════════════════════════════════════════════════════════════════

def _vendor_files_ok() -> bool:
    for p in (VENDOR_JS, VENDOR_CSS):
        if not p.exists() or p.stat().st_size < 100:
            return False
    return True


def _ensure_vendor_files() -> bool:
    """Download LiteGraph vendor files if missing. Never raises."""
    if _vendor_files_ok():
        logger.info("LiteGraph vendor files present")
        return True

    logger.info("LiteGraph vendor files missing — attempting auto-download…")

    if not DOWNLOAD_SCRIPT.exists():
        logger.warning(
            "Download script not found: {}. "
            "The graph editor will fall back to a CDN.",
            DOWNLOAD_SCRIPT,
        )
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(DOWNLOAD_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode == 0 and _vendor_files_ok():
            logger.info("LiteGraph vendor files downloaded successfully")
            return True
        logger.warning(
            "Vendor download returned code {}. stderr: {}",
            result.returncode,
            (result.stderr or "").strip()[:500],
        )
    except subprocess.TimeoutExpired:
        logger.warning("Vendor download timed out after 90s")
    except Exception as exc:
        logger.warning("Vendor download failed: {}", exc)

    logger.warning(
        "Continuing without local vendor files. "
        "The graph editor will load LiteGraph from jsDelivr. "
        "To fix permanently, run: python run.py vendor"
    )
    return False


def _check_env_file() -> None:
    if ENV_FILE.exists():
        logger.info("Configuration file: {}", ENV_FILE)
    else:
        logger.warning(
            "No .env file found at {}. Using built-in defaults: "
            "Ollama at http://localhost:11434/v1, model llama3.2. "
            "Create .env (copy .env.example) to customise.",
            ENV_FILE,
        )


def _check_web_files() -> None:
    for name in ("graph.html", "settings.html", "logo.png"):
        p = WEB_DIR / name
        if not p.exists():
            logger.warning(
                "Web file missing: {} — the /{} route will return a "
                "helpful 404 page.",
                p, name.replace(".html", "").replace(".png", ""),
            )


def _ensure_php_dirs() -> None:
    """
    Create the PHP app's runtime directories.

    php_app/data/   — where the SQLite database lives
    php_app/static/ — where static assets (CSS) are served from
    """
    if not settings.php_enabled:
        return
    for sub in ("data", "static"):
        d = PHP_APP_DIR / sub
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Could not create {}: {}", d, exc)


def _log_effective_configuration() -> None:
    logger.info("Effective configuration:")
    logger.info("  Primary LLM base URL:  {}", settings.llm_base_url)
    logger.info("  Primary LLM model:     {}", settings.llm_model)
    logger.info(
        "  Primary LLM API key:   {}",
        "(set)" if settings.llm_api_key not in ("", "not-needed")
        else "(placeholder)",
    )
    if settings.fallback_enabled:
        logger.info("  Fallback LLM base URL: {}", settings.llm_fallback_base_url)
        logger.info("  Fallback LLM model:    {}",
                    settings.llm_fallback_model or "(default)")
    else:
        logger.info("  Fallback LLM:          (disabled)")
    logger.info("  Ollama host:           {}", settings.ollama_host)
    logger.info("  llama-cpp enabled:     {}", settings.llama_cpp_enabled)
    logger.info("  llama-cpp base URL:    {}", settings.llama_cpp_base_url)
    logger.info("  llama-cpp model:       {}", settings.llama_cpp_model)
    logger.info("  PHP enabled:           {}", settings.php_enabled)
    logger.info("  PHP SQL driver:        {}", settings.php_db_driver)
    logger.info("  MCP command:           {} {}",
                settings.mcp_server_command, " ".join(settings.mcp_args_list))
    logger.info("  Session DB:            {}", settings.session_db)
    logger.info("  Models dir:            {}", settings.models_path)
    logger.info("  Logs dir:              {}", settings.logs_path)
    logger.info("  Stream tokens:         {}", settings.stream_tokens)
    logger.info("  Show thinking:         {}", settings.show_thinking)


def _check_router(router, expected_min: int, name: str) -> int:
    """
    Log the route count for one router and warn if it is below the
    minimum expected. Returns the actual count.

    A router that comes up short means an endpoint will 404 at
    request time. Catching it here turns a subtle runtime bug into
    a loud startup warning.
    """
    count = len(router.routes)
    if count < expected_min:
        logger.error(
            "Router '{}' registered only {} routes (expected at least {}). "
            "Some endpoints may be missing — check for a commented-out "
            "route or a failed import.",
            name, count, expected_min,
        )
    else:
        logger.debug("Router '{}': {} routes", name, count)
    return count


# ══════════════════════════════════════════════════════════════════════
#  Lifespan
# ══════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting 3olution Agent Graph v{}", __version__)

    _check_env_file()
    _ensure_vendor_files()
    _check_web_files()
    _ensure_php_dirs()
    ensure_models_dir()
    _log_effective_configuration()

    # ── LLM client ───────────────────────────────────────────────────
    llm = LLMClient()

    # ── Session store ────────────────────────────────────────────────
    store = SessionStore(
        settings.session_db,
        max_graph_runs=settings.max_graph_runs,
    )
    try:
        await store.init()
    except Exception as exc:
        logger.error("Session store init failed: {}", exc)
        store = None  # type: ignore[assignment]

    # ── llama-cpp server ─────────────────────────────────────────────
    llama_server: LlamaServerManager | None = None
    if settings.llama_cpp_enabled:
        try:
            from .llama_server import manager as _manager_singleton
            llama_server = LlamaServerManager()
            # Register as the module-level singleton so `get_manager()`
            # works from routes and the agent.
            import backend.llama_server as _llama_module
            _llama_module.manager = llama_server

            # Best-effort start. If the model file is missing or the
            # native library fails to load, the manager logs a clear
            # error and the app continues without llama-cpp.
            started = await llama_server.start()
            if started:
                logger.info(
                    "llama-cpp server started with model '{}'",
                    llama_server.current_model,
                )
            else:
                logger.warning(
                    "llama-cpp server did not start. The /api/llama/* "
                    "endpoints will return 503 until a model is loaded "
                    "from the settings page."
                )
        except Exception as exc:
            logger.exception("llama-cpp manager init failed: {}", exc)
            llama_server = None
    else:
        logger.info("llama-cpp server disabled via config")

    # ── MCP client ───────────────────────────────────────────────────
    mcp = MCPClient()
    try:
        await mcp.connect(settings.mcp_server_command, settings.mcp_args_list)
    except Exception as exc:
        logger.error("MCP connection failed at startup: {}", exc)
        logger.warning(
            "The app will start WITHOUT tools. Every tool call will "
            "return 'Error: MCP session not connected.'\n"
            "To diagnose, run the MCP server standalone:\n"
            "    python -m mcp_server.server"
        )

    # ── Shared state ─────────────────────────────────────────────────
    state["llm"] = llm
    state["mcp"] = mcp
    state["store"] = store
    state["llama_server"] = llama_server
    state["agent"] = Agent(llm, mcp, store=store)

    # ── LLM warmup ───────────────────────────────────────────────────
    if settings.llm_warmup:
        try:
            await llm.warmup()
        except Exception as exc:
            logger.warning("LLM warmup failed: {}", exc)

    logger.info(
        "Backend ready — primary LLM={} fallback={} llama_cpp={} php={}",
        settings.llm_model,
        settings.llm_fallback_model if settings.fallback_enabled else "(none)",
        "running" if (llama_server and llama_server.is_running) else "idle",
        "enabled" if settings.php_enabled else "disabled",
    )

    try:
        yield
    finally:
        logger.info("Shutting down…")
        try:
            await mcp.close()
        except Exception as exc:
            logger.debug("MCP close error: {}", exc)

        if llama_server is not None:
            try:
                await llama_server.stop()
            except Exception as exc:
                logger.debug("llama-cpp stop error: {}", exc)


# ══════════════════════════════════════════════════════════════════════
#  App
# ══════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="3olution Agent Graph",
    description="Dynamic operating graph for AI agents — https://3olution.com",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════
#  Request models
# ══════════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    model: str | None = None
    provider: str = "auto"
    session_id: str | None = None
    disable_tools: bool = False


class SessionCreateRequest(BaseModel):
    session_id: str | None = None


# ══════════════════════════════════════════════════════════════════════
#  Health / version
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    mcp = state.get("mcp")
    mcp_ok = mcp is not None and getattr(mcp, "_connected", False)

    php = state.get("php_server")
    php_running = bool(php and php.is_running()) if php else False

    mgr = state.get("llama_server")
    llama_running = bool(mgr and mgr.is_running)
    llama_ready = False
    if llama_running:
        try:
            llama_ready = await mgr.is_ready()
        except Exception:
            llama_ready = False

    return {
        "status": "ok",
        "version": __version__,
        "brand": "3olution",
        "url": "https://3olution.com",
        "primary_llm": {
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
        },
        "fallback_llm": {
            "enabled": settings.fallback_enabled,
            "base_url": settings.llm_fallback_base_url or None,
            "model": settings.llm_fallback_model or None,
        },
        "ollama_host": settings.ollama_host,
        "llama_cpp": {
            "enabled": settings.llama_cpp_enabled,
            "running": llama_running,
            "ready": llama_ready,
            "loaded_model": mgr.current_model if llama_running else None,
            "base_url": settings.llama_cpp_base_url,
        },
        "mcp_connected": mcp_ok,
        "stream_tokens": settings.stream_tokens,
        "show_thinking": settings.show_thinking,
        "disable_tools": settings.disable_tools,
        "session_db": settings.session_db,
        "models_dir": str(settings.models_path),
        "logs_dir": str(settings.logs_path),
        "env_file": ENV_FILE.exists(),
        "vendor_files": {
            "litegraph_js": VENDOR_JS.exists(),
            "litegraph_css": VENDOR_CSS.exists(),
        },
        "web_files": {
            "graph_html": (WEB_DIR / "graph.html").exists(),
            "settings_html": (WEB_DIR / "settings.html").exists(),
        },
        "branding": {
            "logo_web": (WEB_DIR / "logo.png").exists(),
            "favicon_ico": (WEB_DIR / "favicon.ico").exists(),
        },
        "php": {
            "enabled": settings.php_enabled,
            "running": php_running,
            "driver": settings.php_db_driver,
        },
    }


@app.get("/api/version")
async def version():
    return {
        "version": __version__,
        "brand": "3olution",
        "url": "https://3olution.com",
    }


# ══════════════════════════════════════════════════════════════════════
#  Chat — sync, SSE, WebSocket
# ══════════════════════════════════════════════════════════════════════

async def _resolve_history(req: ChatRequest) -> list[dict]:
    """Load session history if a session_id is given, else use req.history."""
    history = req.history
    if req.session_id and state.get("store"):
        stored = await state["store"].load(req.session_id)
        if stored is not None:
            history = stored
    return history


@app.post("/api/chat")
async def chat(req: ChatRequest):
    agent: Agent = state.get("agent")
    if agent is None:
        return JSONResponse(
            {"error": "Agent not initialised — check MCP connection."},
            status_code=503,
        )

    history = await _resolve_history(req)

    events: list[dict] = []
    async for ev in agent.run(
        req.message,
        history,
        model=req.model,
        session_id=req.session_id,
        disable_tools=req.disable_tools,
        provider=req.provider,
    ):
        events.append(ev)
    return JSONResponse(events)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream chat events as Server-Sent Events (SSE)."""
    agent: Agent = state.get("agent")
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialised")

    history = await _resolve_history(req)

    async def event_stream():
        import json as _json
        try:
            async for ev in agent.run(
                req.message,
                history,
                model=req.model,
                session_id=req.session_id,
                disable_tools=req.disable_tools,
                provider=req.provider,
            ):
                yield f"data: {_json.dumps(ev, default=str)}\n\n"
        except Exception as exc:
            logger.exception("SSE chat failed")
            yield (
                f"data: {_json.dumps({'type': 'final', 'content': f'Error: {exc}', 'messages': []})}\n\n"
            )
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    agent: Agent = state.get("agent")
    if agent is None:
        await websocket.send_json({
            "type": "final",
            "content": "Agent not ready.",
            "messages": [],
        })
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "")
            model = data.get("model") or None
            provider = data.get("provider") or "auto"
            session_id = data.get("session_id") or None
            disable_tools = bool(data.get("disable_tools", False))

            history = data.get("history", [])
            if session_id and state.get("store"):
                stored = await state["store"].load(session_id)
                if stored is not None:
                    history = stored

            async for ev in agent.run(
                message,
                history,
                model=model,
                session_id=session_id,
                disable_tools=disable_tools,
                provider=provider,
            ):
                await websocket.send_json(ev)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("Chat WebSocket error")
        try:
            await websocket.send_json({
                "type": "final",
                "content": f"Server error: {exc}",
                "messages": [],
            })
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
#  Sessions
# ══════════════════════════════════════════════════════════════════════

@app.get("/api/sessions")
async def list_sessions():
    store: SessionStore | None = state.get("store")
    if store is None:
        return {"sessions": [], "count": 0}
    sessions = await store.list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


@app.post("/api/sessions")
async def create_session(req: SessionCreateRequest):
    import uuid
    store: SessionStore | None = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    session_id = req.session_id or str(uuid.uuid4())
    await store.save(session_id, [])
    return {"session_id": session_id}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    store: SessionStore | None = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    history = await store.load(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "history": history}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    store: SessionStore | None = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    removed = await store.delete(session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


# ══════════════════════════════════════════════════════════════════════
#  Routers — all five MUST be included, then sanity-checked
# ══════════════════════════════════════════════════════════════════════

app.include_router(graph_router)
_check_router(graph_router, expected_min=8, name="graph")

app.include_router(ollama_router)
_check_router(ollama_router, expected_min=4, name="ollama")

app.include_router(llama_router)
_check_router(llama_router, expected_min=6, name="llama")

app.include_router(system_router)
_check_router(system_router, expected_min=2, name="system")

app.include_router(php_router)
_check_router(php_router, expected_min=1, name="php")


# ══════════════════════════════════════════════════════════════════════
#  Explicit HTML routes (registered BEFORE the static mount)
# ══════════════════════════════════════════════════════════════════════

def _serve_or_404(file_path: Path, page_name: str, hint: str) -> Response:
    if file_path.exists():
        return FileResponse(file_path, media_type="text/html")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>404 — {page_name}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f1116;
           color: #e6e6e6; max-width: 720px; margin: 80px auto;
           padding: 24px; line-height: 1.6; }}
    h1 {{ color: #ff6c6c; margin-bottom: 0.4em; }}
    code {{ background: #1a1d24; padding: 2px 6px; border-radius: 4px;
            font-family: ui-monospace, monospace; word-break: break-all; }}
    a {{ color: #4a9eff; }}
    .box {{ background: #14161b; border: 1px solid #222;
            border-radius: 8px; padding: 16px; margin: 20px 0; }}
  </style>
</head>
<body>
  <h1>404 — {page_name} not found</h1>
  <p>{hint}</p>
  <div class="box">
    <p><strong>Expected file:</strong></p>
    <p><code>{file_path}</code></p>
  </div>
  <p><a href="/">← Back to chat</a></p>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=404)


@app.get("/graph", include_in_schema=False)
async def graph_page():
    return _serve_or_404(
        WEB_DIR / "graph.html",
        "Graph Editor",
        "The graph editor HTML file is missing. Copy "
        "<code>web/graph.html</code> into the project's <code>web/</code> directory.",
    )


@app.get("/settings", include_in_schema=False)
async def settings_page():
    return _serve_or_404(
        WEB_DIR / "settings.html",
        "Settings",
        "The settings HTML file is missing. Copy "
        "<code>web/settings.html</code> into the project's <code>web/</code> directory.",
    )


# ══════════════════════════════════════════════════════════════════════
#  Favicon
# ══════════════════════════════════════════════════════════════════════
#
#  Serves the project logo as the favicon for every page.
#
#  Search order: web/favicon.ico, then web/logo.png, then
#  web/favicon.png, then web/logo.ico. The first one that exists
#  wins. If none exist, serves a 1×1 transparent PNG so the browser
#  console stays quiet — a missing favicon otherwise produces a 404
#  on every page load.
#
#  The same handler answers /favicon.ico, /favicon.png, /favicon.svg,
#  and the two Apple touch-icon aliases. Browsers auto-request these
#  paths; a single handler keeps them all working.

_FAVICON_CANDIDATES = (
    "favicon.ico",
    "logo.png",
    "favicon.png",
    "logo.ico",
    "favicon.svg",
    "logo.svg",
)

_FAVICON_MEDIA_TYPES = {
    ".ico":  "image/x-icon",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# 1×1 fully-transparent PNG. Used as the last-resort fallback so the
# route never 404s.
_TINY_TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "0000000100000001"
    "08060000001f15c489"
    "0000000d49444154"
    "789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def favicon():
    """
    Serve the project logo as the favicon.

    The first candidate in `web/` that exists is returned. If none
    exist, a 1×1 transparent PNG is served so the route never 404s.
    """
    for name in _FAVICON_CANDIDATES:
        p = WEB_DIR / name
        if p.is_file():
            media = _FAVICON_MEDIA_TYPES.get(
                p.suffix.lower(), "application/octet-stream",
            )
            return FileResponse(
                p,
                media_type=media,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    logger.debug(
        "No favicon file found in {}. Serving a transparent PNG. "
        "Drop a logo.png into web/ to customise.",
        WEB_DIR,
    )
    return Response(
        content=_TINY_TRANSPARENT_PNG,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


# ══════════════════════════════════════════════════════════════════════
#  Static files (mounted LAST so explicit routes win)
# ══════════════════════════════════════════════════════════════════════

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    logger.error(
        "Web directory not found: {}. Create it with index.html, "
        "styles.css, app.js, graph.html, settings.html.",
        WEB_DIR,
    )