scripts/serve.py — the three fixes
Problem	Fix	Where
Windows event loop breaks MCP stdio	Set SelectorEventLoopPolicy before importing uvicorn	Lines at module top
Reloader child cannot find backend	Set both sys.path and PYTHONPATH before importing uvicorn	Lines at module top
Access log floods the log sink	Disabled unless AGENT_UVICORN_ACCESS_LOG=1	In uvicorn.run(...) call
The order of operations matters: the loop policy must be set before import uvicorn, and sys.path/PYTHONPATH must be set before uvicorn spawns its reloader child. Both are done at module top, guarded so a double-import does not duplicate the entries.

scripts/download_vendor.py — the algorithm
text
for each file (litegraph.min.js, litegraph.css):
    if file exists and size >= MIN_SIZE:
        skip
    for each mirror URL:
        try download
        if downloaded size < MIN_SIZE:
            reject, try next mirror
        if REQUIRED_SUBSTRING not in content:
            reject, try next mirror
        write to disk
        return success
    return failure
Two validation gates protect against subtle failures:

Size floor — a 404 page is a few KB; real files are 85 KB (JS) or 4 KB (CSS). The floor rejects error pages that would otherwise be written as valid-looking files.

Substring check — a proxy or captive portal might return a generic 200 page of the right size. Requiring the string "LiteGraph" in the JS file (and "litegraph" in the CSS file) catches this.

Only after both gates pass is the file written to disk. This makes the operation atomic from the caller's perspective: either the file is fully valid or it does not exist.

scripts/download_vendor.sh and .ps1 — when to use them
These wrappers exist for three scenarios:

Python is unavailable. The user has a bare Linux container with curl but no Python yet — they want to vendor LiteGraph before installing Python.

Bootstrap scripts. A provisioning script for a CI runner might prefer bash scripts/download_vendor.sh over invoking Python.

Debugging. The shell versions have no Python overhead; the curl output is visible inline. Useful when diagnosing a network issue.

For normal use, prefer python scripts/download_vendor.py — it has better validation, mirror fallback, and cross-platform consistency.

sessions.db — the runtime SQLite file
sessions.db is created by backend/storage.py on the first run of the backend. It is not committed to git — see the .gitignore entry:

gitignore
*.db
*.sqlite
*.sqlite3
What is in the file
Two tables:

Table	Purpose	Populated by
sessions	Conversation history, one row per session_id	Chat routes when a session_id is provided
graph_runs	Graph execution history with full event streams	/api/graph/run and /ws/graph after each run
File lifecycle
text
First backend start:
    backend/storage.py::SessionStore.init()
        → opens sessions.db
        → CREATE TABLE IF NOT EXISTS sessions (...)
        → CREATE TABLE IF NOT EXISTS graph_runs (...)
        → CREATE INDEX IF NOT EXISTS ...
        → closes

First chat with session_id:
    POST /api/chat
        → agent.run(...)
        → agent.store.save(session_id, messages)
        → opens sessions.db
        → INSERT INTO sessions ... ON CONFLICT UPDATE
        → closes

First graph run:
    POST /api/graph/run
        → engine.execute(...)
        → routes._summarize_events(events)
        → store.save_run(payload, events, status, final_text)
        → opens sessions.db
        → INSERT INTO graph_runs ...
        → DELETE older runs beyond max_graph_runs
        → closes
Every method opens a fresh connection, executes, and closes. This is the "no connection pool" pattern — it is slightly less efficient but sidesteps every "database is locked" scenario. SQLite's single-writer semantics are respected automatically.

Where the file lives
The path is configured in .env:

env
AGENT_SESSION_DB=sessions.db
A bare filename resolves relative to the process working directory — which is the project root when the backend is launched via run.py, start.sh, or scripts/serve.py. An absolute path passes through unchanged.

Inspecting the file
To inspect the database manually with the portable SQLite CLI:

bash
# List tables
runtime/sqlite/sqlite3 sessions.db ".tables"

# Count sessions
runtime/sqlite/sqlite3 sessions.db "SELECT COUNT(*) FROM sessions;"

# Show the newest session
runtime/sqlite/sqlite3 sessions.db \
    "SELECT id, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 5;"

# Show recent graph runs
runtime/sqlite/sqlite3 sessions.db \
    "SELECT id, graph_name, status, finished_at FROM graph_runs ORDER BY finished_at DESC LIMIT 5;"
Or with the system sqlite3:

bash
sqlite3 sessions.db ".tables"
Resetting
To clear all conversation and graph-run history without touching the rest of the application:

bash
rm sessions.db
# The next backend start recreates it empty.
Or keep the schema and clear only the data:

bash
sqlite3 sessions.db "DELETE FROM sessions; DELETE FROM graph_runs;"
Size estimates
Data	Per row	1000 rows
Session (typical 20-message chat)	5–20 KB	5–20 MB
Graph run (typical 20-event execution)	2–15 KB	2–15 MB
The max_graph_runs config caps graph-runs growth at 100 rows by default. Session growth is unbounded — but a session is only one row no matter how many turns it has, so the file grows with the number of distinct sessions, not the number of messages