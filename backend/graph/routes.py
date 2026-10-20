"""
FastAPI routes for graph CRUD, execution, and history.
https://3olution.com

Endpoints:

    GET    /api/graph/types                     — generic schema
    GET    /api/graph/litegraph-types           — LiteGraph-shaped schema
    POST   /api/graph/run                       — execute synchronously
    GET    /api/graph/runs                      — list recent runs
    GET    /api/graph/runs/latest               — most recent run
    GET    /api/graph/runs/{run_id}             — one run's full record
    DELETE /api/graph/runs/{run_id}             — delete one run
    DELETE /api/graph/runs/all                  — delete every run
    WS     /ws/graph                            — stream execution events

================================================================
  ROUTE ORDERING
================================================================

  Starlette matches in registration order. Two rules matter:

      /api/graph/runs/latest  MUST come before /api/graph/runs/{run_id}
      /api/graph/runs/all     MUST come before /api/graph/runs/{run_id}

  If either is violated, the literal path is captured as a run_id and
  the endpoint becomes unreachable. The order below is correct.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger

from ..state import state
from . import registry
from .engine import execute
from .model import Graph

router = APIRouter(tags=["graph"])


# ══════════════════════════════════════════════════════════════════════
#  Node type schemas
# ══════════════════════════════════════════════════════════════════════

@router.get("/api/graph/types")
async def graph_types():
    """Return all node types in the generic schema."""
    return registry.serialize_all()


@router.get("/api/graph/litegraph-types")
async def litegraph_types():
    """Return node types shaped for LiteGraph.js registerNodeType()."""
    out = []
    for nt in registry.serialize_all():
        out.append({
            "name": nt["name"],
            "title": nt["display"],
            "category": nt["category"],
            "description": nt["description"],
            "color": nt["color"],
            "inputs": [
                {"name": p["name"], "type": p["type"]}
                for p in nt["inputs"]
            ],
            "outputs": [
                {"name": p["name"], "type": p["type"]}
                for p in nt["outputs"]
            ],
            "widgets": [
                {
                    "name": p["name"],
                    "type": _widget_type(p["type"]),
                    "default": p["default"],
                    "choices": p.get("choices"),
                    "dynamic": p.get("dynamic"),
                    "description": p.get("description", ""),
                }
                for p in nt["params"]
            ],
        })
    return out


def _widget_type(param_type: str) -> str:
    """Map backend param types to LiteGraph widget types."""
    return {
        "string": "text",
        "text": "text",
        "number": "number",
        "bool": "toggle",
        "select": "combo",
        "json": "text",
    }.get(param_type, "text")


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _extract_final_text(events: list[dict], outputs: dict) -> str | None:
    """
    Return the text from the first output.text node in the final
    outputs map, or None if no output.text node produced a result.
    """
    for node_id, outs in (outputs or {}).items():
        if isinstance(outs, dict) and "result" in outs:
            result = outs["result"]
            if result:
                return str(result)
    return None


def _summarize_status(events: list[dict]) -> str:
    """Return 'ok' or 'error' based on the event stream."""
    for ev in events:
        t = ev.get("type")
        if t in ("graph_error", "node_error"):
            return "error"
    return "ok"


# ══════════════════════════════════════════════════════════════════════
#  Execution
# ══════════════════════════════════════════════════════════════════════

@router.post("/api/graph/run")
async def graph_run(payload: dict):
    """
    Execute a graph synchronously and return all events.

    The run is saved to the graph_runs table before the response is
    returned. The response includes the run_id in a trailing event
    so the client can fetch the record later.
    """
    graph = Graph.from_dict(payload)
    events: list[dict] = []

    async for ev in execute(graph, state):
        events.append(ev)

    # Collect the final outputs to compute final_text and status.
    outputs = {}
    for ev in events:
        if ev.get("type") == "graph_done":
            outputs = ev.get("outputs", {})

    final_text = _extract_final_text(events, outputs)
    status = _summarize_status(events)

    # Persist the run.
    store = state.get("store")
    if store is not None:
        try:
            run_id = await store.save_run(payload, events, status, final_text)
            events.append({"type": "run_saved", "run_id": run_id})
        except Exception as exc:
            logger.warning("Could not save graph run: {}", exc)

    return JSONResponse(events)


@router.websocket("/ws/graph")
async def graph_ws(websocket: WebSocket):
    """Execute a graph, streaming progress events over WebSocket."""
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            graph = Graph.from_dict(payload)

            events: list[dict] = []
            async for ev in execute(graph, state):
                events.append(ev)
                await websocket.send_json(ev)

            # Persist after execution completes.
            outputs = {}
            for ev in events:
                if ev.get("type") == "graph_done":
                    outputs = ev.get("outputs", {})

            final_text = _extract_final_text(events, outputs)
            status = _summarize_status(events)

            store = state.get("store")
            if store is not None:
                try:
                    run_id = await store.save_run(
                        payload, events, status, final_text,
                    )
                    await websocket.send_json({
                        "type": "run_saved",
                        "run_id": run_id,
                    })
                except Exception as exc:
                    logger.warning("Could not save graph run: {}", exc)

    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("Graph WebSocket error")
        try:
            await websocket.send_json(
                {"type": "graph_error", "error": str(exc)}
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════
#  History — literal paths MUST come before the {run_id} catch-all
# ══════════════════════════════════════════════════════════════════════

@router.get("/api/graph/runs")
async def list_runs(limit: int = 50):
    """Return metadata for the most recent graph runs."""
    store = state.get("store")
    if store is None:
        return {"runs": [], "count": 0}
    runs = await store.list_runs(limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/api/graph/runs/latest")
async def latest_run():
    """
    Return the most recent run's full record, or 404 if none exist.

    Registered before /api/graph/runs/{run_id} so the literal path
    is not captured as a run_id.
    """
    store = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")

    recent = await store.list_runs(limit=1)
    if not recent:
        raise HTTPException(status_code=404, detail="No runs yet")

    full = await store.load_run(recent[0]["id"])
    if full is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return full


@router.delete("/api/graph/runs/all")
async def delete_all_runs():
    """
    Delete every stored run.

    Registered before /api/graph/runs/{run_id} so the literal path
    is not captured as a run_id.
    """
    store = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    removed = await store.delete_all_runs()
    return {"status": "deleted", "count": removed}


@router.get("/api/graph/runs/{run_id}")
async def get_run(run_id: str):
    """Return the full record for one run."""
    store = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    run = await store.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.delete("/api/graph/runs/{run_id}")
async def delete_run(run_id: str):
    """Delete one run."""
    store = state.get("store")
    if store is None:
        raise HTTPException(status_code=503, detail="Storage not available")
    removed = await store.delete_run(run_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "deleted", "run_id": run_id}