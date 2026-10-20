"""
SQLite-backed persistence layer.
https://3olution.com

Two tables are managed:

    sessions     — chat conversation history, one row per session_id
    graph_runs   — graph execution history, one row per run

Uses the stdlib sqlite3 module via asyncio.to_thread() so no extra
dependency is required. The database file is created on first use.

================================================================
  WHY ONE CONNECTION PER CALL
================================================================

  Every public method opens a fresh sqlite3 connection, executes,
  and closes. This is the "no connection pool" pattern — it is
  slightly less efficient than keeping one connection alive, but it
  sidesteps every "database is locked" scenario and works correctly
  across asyncio.to_thread() boundaries (a sqlite3.Connection cannot
  be used from multiple threads).

  SQLite's single-writer semantics are respected automatically.

================================================================
  GRAPH RUNS
================================================================

  Each row in graph_runs stores the full execution record:

    id              — UUID assigned at save time
    graph_name      — the name from the submitted payload
    payload         — the original node/edge JSON
    events          — the full event stream as a JSON array
    status          — "ok" | "error"
    final_text      — the text from the first output.text node
    node_count      — number of nodes in the graph
    event_count     — number of events emitted
    finished_at     — ISO 8601 timestamp

  Rows are pruned beyond AGENT_MAX_GRAPH_RUNS (default 100).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger


# ══════════════════════════════════════════════════════════════════════
#  SessionStore
# ══════════════════════════════════════════════════════════════════════

class SessionStore:
    """Persist conversation histories and graph runs in SQLite."""

    def __init__(self, db_path: str, max_graph_runs: int = 100) -> None:
        self.db_path = db_path
        self.max_graph_runs = max_graph_runs

    # ══════════════════════════════════════════════════════════════════
    #  Schema initialisation
    # ══════════════════════════════════════════════════════════════════

    async def init(self) -> None:
        """Create the tables if they do not exist."""

        def _init() -> None:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        id          TEXT PRIMARY KEY,
                        created_at  TEXT NOT NULL,
                        updated_at  TEXT NOT NULL,
                        history     TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_runs (
                        id            TEXT PRIMARY KEY,
                        graph_name    TEXT NOT NULL,
                        payload       TEXT NOT NULL,
                        events        TEXT NOT NULL,
                        status        TEXT NOT NULL,
                        final_text    TEXT,
                        node_count    INTEGER NOT NULL DEFAULT 0,
                        event_count   INTEGER NOT NULL DEFAULT 0,
                        finished_at   TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_graph_runs_finished
                    ON graph_runs (finished_at DESC)
                    """
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_init)
        logger.info(
            "Storage initialised at {} (max_graph_runs={})",
            self.db_path,
            self.max_graph_runs,
        )

    # ══════════════════════════════════════════════════════════════════
    #  Sessions
    # ══════════════════════════════════════════════════════════════════

    async def save(self, session_id: str, history: list[dict]) -> None:
        """Upsert a session's history."""

        def _save() -> None:
            conn = sqlite3.connect(self.db_path)
            try:
                now = datetime.now(timezone.utc).isoformat()
                payload = json.dumps(history, default=str)
                conn.execute(
                    """
                    INSERT INTO sessions (id, created_at, updated_at, history)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        history    = excluded.history
                    """,
                    (session_id, now, now, payload),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_save)

    async def load(self, session_id: str) -> list[dict] | None:
        """Load a session's history, or None if not found."""

        def _load() -> list[dict] | None:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    "SELECT history FROM sessions WHERE id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return json.loads(row[0])
            finally:
                conn.close()

        return await asyncio.to_thread(_load)

    async def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if a row was removed."""

        def _delete() -> bool:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE id = ?",
                    (session_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_delete)

    async def list_sessions(self) -> list[dict]:
        """Return metadata for all sessions, newest first."""

        def _list() -> list[dict]:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT id, created_at, updated_at
                    FROM sessions
                    ORDER BY updated_at DESC
                    """
                )
                return [
                    {"id": r[0], "created_at": r[1], "updated_at": r[2]}
                    for r in cursor.fetchall()
                ]
            finally:
                conn.close()

        return await asyncio.to_thread(_list)

    # ══════════════════════════════════════════════════════════════════
    #  Graph runs
    # ══════════════════════════════════════════════════════════════════

    async def save_run(
        self,
        payload: dict,
        events: list[dict],
        status: str,
        final_text: str | None,
    ) -> str:
        """
        Persist one graph execution.

        Returns the generated run ID. Automatically prunes the oldest
        rows beyond self.max_graph_runs.
        """
        run_id = str(uuid.uuid4())
        graph_name = str(payload.get("name") or "Untitled")
        node_count = len(payload.get("nodes", []))
        event_count = len(events)
        finished_at = datetime.now(timezone.utc).isoformat()

        def _save() -> None:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO graph_runs (
                        id, graph_name, payload, events, status,
                        final_text, node_count, event_count, finished_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        graph_name,
                        json.dumps(payload, default=str),
                        json.dumps(events, default=str),
                        status,
                        final_text,
                        node_count,
                        event_count,
                        finished_at,
                    ),
                )
                # Prune oldest rows beyond the retention cap.
                conn.execute(
                    """
                    DELETE FROM graph_runs
                    WHERE id NOT IN (
                        SELECT id FROM graph_runs
                        ORDER BY finished_at DESC
                        LIMIT ?
                    )
                    """,
                    (self.max_graph_runs,),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_save)
        return run_id

    async def load_run(self, run_id: str) -> dict | None:
        """Load the full record for one run, or None if not found."""

        def _load() -> dict | None:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT id, graph_name, payload, events, status,
                           final_text, node_count, event_count, finished_at
                    FROM graph_runs
                    WHERE id = ?
                    """,
                    (run_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return {
                    "id": row[0],
                    "graph_name": row[1],
                    "payload": json.loads(row[2]),
                    "events": json.loads(row[3]),
                    "status": row[4],
                    "final_text": row[5],
                    "node_count": row[6],
                    "event_count": row[7],
                    "finished_at": row[8],
                }
            finally:
                conn.close()

        return await asyncio.to_thread(_load)

    async def list_runs(self, limit: int = 50) -> list[dict]:
        """
        Return metadata for the most recent runs, newest first.

        Includes a truncated preview of the final text for display
        in the history list.
        """
        limit = max(1, min(int(limit), 500))

        def _list() -> list[dict]:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT id, graph_name, status, final_text,
                           node_count, event_count, finished_at
                    FROM graph_runs
                    ORDER BY finished_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                out: list[dict] = []
                for r in cursor.fetchall():
                    final = r[3] or ""
                    if len(final) > 120:
                        final = final[:120] + "…"
                    out.append({
                        "id": r[0],
                        "graph_name": r[1],
                        "status": r[2],
                        "final_text_preview": final,
                        "node_count": r[4],
                        "event_count": r[5],
                        "finished_at": r[6],
                    })
                return out
            finally:
                conn.close()

        return await asyncio.to_thread(_list)

    async def delete_run(self, run_id: str) -> bool:
        """Delete one run. Returns True if a row was removed."""

        def _delete() -> bool:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM graph_runs WHERE id = ?",
                    (run_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_delete)

    async def delete_all_runs(self) -> int:
        """Delete every stored run. Returns the number of rows removed."""

        def _delete() -> int:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute("DELETE FROM graph_runs")
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

        return await asyncio.to_thread(_delete)