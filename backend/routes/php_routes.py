"""
PHP execution and static asset routes.
https://3olution.com

Endpoints:

    GET    /api/php/status         — binary availability and version
    GET    /api/php/drivers        — installed PDO drivers
    ANY    /php                    — execute /php/index.php
    ANY    /php/{path}             — execute a PHP script
    GET    /php-static/{asset}     — serve a static file from php_app/static/

================================================================
  ROUTE ORDERING
================================================================

  Starlette matches routes in registration order. There are two
  potential collisions to avoid:

    1. /php-static/{asset} vs /php/{path}
       These do NOT collide because Starlette's path converter
       requires a literal slash after "/php". A path starting with
       "/php-static" has a hyphen where the slash would be, so it
       does not match "/php/{path}".

    2. /api/php/status vs /php/{path}
       These also do not collide — the /api prefix is registered
       before the catch-all.

  Nevertheless, the /php-static route is registered FIRST in this
  module, as defensive practice.

================================================================
  PATH SAFETY
================================================================

  Every user-supplied path is resolved under the PHP app directory.
  A path like "../../etc/passwd" is rejected with HTTP 400 before
  the file is even opened.

  The check is done by resolving the candidate to its canonical
  form and verifying that the result is a subpath of the app
  directory. This is the standard defence against directory
  traversal.

================================================================
  EVENT LOOP SAFETY
================================================================

  `execute_php()` uses `subprocess.run`, which is synchronous and
  blocks the calling thread for the duration of the PHP request.
  Calling it directly from an async route handler would freeze
  the event loop — no other requests, no WebSocket traffic, no
  health checks.

  Every route below therefore wraps the call in
  `asyncio.to_thread()`, which runs it in a worker thread and
  awaits the result. This is the standard pattern for running
  synchronous I/O from async code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from loguru import logger

from ..php_server import (
    PHP_APP_DIR,
    execute_php,
    php_status,
)


router = APIRouter(tags=["php"])


# ══════════════════════════════════════════════════════════════════════
#  Paths
# ══════════════════════════════════════════════════════════════════════

PHP_STATIC_DIR = PHP_APP_DIR / "static"


# ══════════════════════════════════════════════════════════════════════
#  Path safety helper
# ══════════════════════════════════════════════════════════════════════

def _resolve_safe(rel_path: str) -> Path | None:
    """
    Resolve a request path to an absolute file under PHP_APP_DIR.

    Returns None if:
        - the resolved path escapes PHP_APP_DIR
        - the resolved path does not end in ".php"

    Empty rel_path resolves to "index.php".
    """
    if not rel_path:
        rel_path = "index.php"

    if not rel_path.endswith(".php"):
        rel_path += ".php"

    base = PHP_APP_DIR.resolve()
    candidate = (base / rel_path).resolve()

    try:
        candidate.relative_to(base)
    except ValueError:
        logger.warning("Rejected path traversal attempt: {}", rel_path)
        return None

    return candidate


def _resolve_static_safe(asset: str) -> Path | None:
    """
    Resolve a static asset path under PHP_STATIC_DIR.

    Returns None if the path escapes the static directory or does
    not exist as a regular file.
    """
    base = PHP_STATIC_DIR.resolve()
    candidate = (base / asset).resolve()

    try:
        candidate.relative_to(base)
    except ValueError:
        logger.warning("Rejected static path traversal attempt: {}", asset)
        return None

    if not candidate.is_file():
        return None

    return candidate


# ══════════════════════════════════════════════════════════════════════
#  Static assets — registered FIRST for clarity
# ══════════════════════════════════════════════════════════════════════

@router.get("/php-static/{asset:path}", include_in_schema=False)
async def php_static(asset: str):
    """
    Serve a static file from php_app/static/.

    This route exists because PHP's index.php references
    /php-static/style.css. Before this route was added, the CSS was
    a 404 and the page rendered unstyled on every platform.
    """
    path = _resolve_static_safe(asset)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Not found: {asset}")
    return FileResponse(path)


# ══════════════════════════════════════════════════════════════════════
#  Status
# ══════════════════════════════════════════════════════════════════════

@router.get("/api/php/status")
async def php_server_status():
    """
    Return PHP availability, version, and binary path.

    Used by the settings page and by the backend startup check.
    """
    return php_status()


@router.get("/api/php/drivers")
async def php_pdo_drivers():
    """
    Return the PDO drivers compiled into the detected PHP binary.

    The PHP notes app requires the `sqlite` driver by default. On
    some distributions, `pdo_sqlite` is a separate package and must
    be installed explicitly. This endpoint surfaces that.
    """
    from ..php_server import find_php_cgi
    import subprocess

    php = find_php_cgi()
    if php is None:
        return {"available": False, "drivers": []}

    try:
        # This subprocess call is short (PHP -r executes one line and
        # exits), but it is still blocking. Run it in a worker thread
        # so the event loop is not held.
        result = await asyncio.to_thread(
            subprocess.run,
            [php, "-r", "echo implode(',', PDO::getAvailableDrivers());"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        drivers = [
            d.strip() for d in result.stdout.strip().split(",") if d.strip()
        ]
        return {"available": True, "drivers": sorted(drivers)}
    except Exception as exc:
        logger.debug("PDO driver detection failed: {}", exc)
        return {"available": False, "drivers": [], "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════
#  PHP execution (catch-all)
# ══════════════════════════════════════════════════════════════════════

@router.api_route(
    "/php",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
@router.api_route(
    "/php/{php_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def php_handler(request: Request, php_path: str = ""):
    """
    Execute a PHP script and return its output.

    Request method, headers, query string, and body are forwarded
    to the CGI environment. PHP sees them as standard $_SERVER,
    $_GET, and $_POST values.

    If the requested script does not exist, falls back to
    index.php so the app's routing works for pretty URLs.
    """
    # ── Resolve the script path safely ───────────────────────────────
    script_path = _resolve_safe(php_path)
    if script_path is None:
        raise HTTPException(status_code=400, detail="Invalid path.")

    # ── If missing, fall back to index.php (routing) ─────────────────
    if not script_path.exists():
        fallback = (PHP_APP_DIR / "index.php").resolve()
        if fallback.exists():
            script_path = fallback
        else:
            raise HTTPException(
                status_code=404,
                detail=f"PHP script not found: {php_path}",
            )

    # ── Gather request info for the CGI environment ──────────────────
    query_string = request.url.query or ""
    body = await request.body()
    headers = dict(request.headers)

    if php_path:
        script_name = f"/php/{php_path}"
        if not script_name.endswith(".php"):
            script_name += ".php"
    else:
        script_name = "/php/index.php"

    # ── Execute in a worker thread ───────────────────────────────────
    #
    # execute_php is synchronous (subprocess.run). Calling it directly
    # would block the event loop for the full duration of the PHP
    # request, freezing every other connection. asyncio.to_thread
    # moves it to a worker thread and awaits the result.
    result = await asyncio.to_thread(
        execute_php,
        script_path=script_path,
        script_name=script_name,
        request_method=request.method,
        query_string=query_string,
        headers=headers,
        body=body,
    )

    if result.get("stderr"):
        logger.debug("PHP stderr: {}", result["stderr"])

    # ── Build the HTTP response ──────────────────────────────────────
    #
    # Hop-by-hop headers must not be forwarded. Everything else can be.
    response_headers = dict(result["headers"])
    for h in ("content-length", "transfer-encoding", "connection"):
        response_headers.pop(h, None)

    return Response(
        content=result["body"],
        status_code=result["status"],
        headers=response_headers,
        media_type=result["headers"].get(
            "Content-Type", "text/html; charset=utf-8"
        ),
    )