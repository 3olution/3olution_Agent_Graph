# Changelog — 3olution Agent Graph

All notable changes to this project are documented here.

The format is based on [Keep a Changelog][kac] and this project
adheres to [Semantic Versioning][semver].

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html

Version numbers follow `MAJOR.MINOR.PATCH`:

- **MAJOR** — breaking changes to the public API, config schema, or
  on-disk data format
- **MINOR** — backward-compatible new features
- **PATCH** — backward-compatible bug fixes


## [Unreleased]

### Added

- Nothing yet.

### Changed

- Nothing yet.

### Fixed

- Nothing yet.


## [2.0.0] — 2026-09-15

The first release with a stable backend API. Consolidates roughly a
day of critical hotfixes on top of `1.0.0`, plus the multi-provider
LLM router, the fallback endpoint, and the auto-download of LiteGraph
vendor files.

### Fixed — Critical

- **MCP subprocess "Connection closed" on Windows.**
  `backend/config.py` now defaults `AGENT_MCP_SERVER_COMMAND` to
  `sys.executable`, so the MCP subprocess always uses the same
  interpreter as the backend. `backend/mcp_client.py` resolves
  bare interpreter names (`python`, `python3`, `py`, and their
  `.exe` variants) to `sys.executable` via `_resolve_command()`.
  Connect is retried once with a fresh `AsyncExitStack`. Windows
  `.ps1` / `.cmd` shims (e.g. `npx`) are wrapped in `cmd /c`.
  Closing an already-closed pipe on Windows is suppressed.

- **Silent 401 errors when `.env` is missing.**
  Defaults in `backend/config.py` now point at a local Ollama
  instance — base URL `http://localhost:11434/v1`, API key
  `ollama`, model `llama3.2`. A missing `.env` no longer
  produces "authentication failed" against the OpenAI API.

- **`/graph` and `/settings` returned 404.**
  Explicit HTML routes are now registered *before* the static
  mount in `backend/main.py`. Each route serves the corresponding
  `web/*.html` file if present, and returns a helpful HTML 404
  page naming the expected file path if it is missing.

- **LiteGraph vendor files not auto-downloaded.**
  Three layers of automatic download, each falling through to the
  next if the previous fails:
  1. `run.py setup` calls `scripts/download_vendor.py`.
  2. `run.py backend` calls it before launching uvicorn.
  3. The FastAPI lifespan in `backend/main.py` calls it on
     startup if the files are still missing.

  `web/graph.html` additionally falls back to jsDelivr at runtime
  if the local files are absent (CSS via `onerror`, JS via
  `document.write`).

- **MCP server crash on older `mcp` SDK.**
  The SDK's startup banner prints Rich-rendered box-drawing
  characters to stdout, which are not valid JSON and corrupt the
  first JSON-RPC frame. `_server.py`'s `run()` now calls
  `mcp.run(show_banner=False)` with a `try/except TypeError`
  fallback for SDK versions that do not accept the kwarg.

- **`/favicon.ico` 404 log noise.**
  The backend now serves an inline SVG favicon at both
  `/favicon.ico` and `/favicon.svg`, eliminating the repeated
  404 warnings that most browsers generate on every page load.

### Added

- **Fallback LLM.** The primary endpoint (Ollama by default) can
  now be backed by an optional external endpoint. New `.env`
  variables: `AGENT_LLM_FALLBACK_BASE_URL`,
  `AGENT_LLM_FALLBACK_API_KEY`, `AGENT_LLM_FALLBACK_MODEL`,
  `AGENT_LLM_FALLBACK_TIMEOUT`. The agent loop emits a
  `fallback` event when switching endpoints, and the chat UI
  renders it as a teal centered notice. Fallback is only tried
  when the primary exhausts its retries *and* the failure was
  retryable — auth errors and 400s do not trigger a switch.

- **Auto-download of LiteGraph vendor files.** `scripts/download_vendor.py`
  fetches `litegraph.min.js` and `litegraph.css` from jsDelivr,
  falling back to `raw.githubusercontent.com`. Two validation
  gates protect against silent failures:
  - A size floor (50 KB for JS, 1 KB for CSS) rejects 404 pages.
  - A required substring (`LiteGraph` in JS, `litegraph` in CSS)
    rejects generic 200 pages served by a captive portal.

  Only after both gates pass is the file written to disk.

- **Startup diagnostics.** The FastAPI lifespan now logs the
  effective LLM configuration, `.env` presence, vendor file
  status, and web file presence. `/api/health` returns the same
  information in structured form, including per-subsystem status
  for the LLM, MCP, llama-cpp, PHP, and storage layers.

- **`CONTRIBUTING.md`.** Guidelines for bug reports, feature
  requests, and pull requests.

- **`CHANGES.md`.** This file.

### Changed

- **`backend/config.py`**
  Ollama-first defaults; four new fallback LLM variables;
  `sys.executable` as the default MCP command; `shlex.split()`
  with `posix=(sys.platform != "win32")` for parsing
  `AGENT_MCP_SERVER_ARGS`, so backslashes in Windows paths
  survive parsing.

- **`backend/llm.py`**
  Two-client design: `self.primary` (always present) and
  `self.fallback` (present only when configured). New
  `chat_stream_fallback()` and `chat_fallback()` methods. The
  streaming implementation is shared via a private
  `_stream_open()` helper. `_resolve()` maps provider aliases
  (`"local"`, `"llamacpp"`) to the canonical `"llama_cpp"`.

- **`backend/mcp_client.py`**
  New `_resolve_command()` helper. Connect retries once with a
  fresh `AsyncExitStack`. Absolute paths are checked for
  existence before spawn. Failure diagnostics now include the
  exact command line to reproduce the error standalone.

- **`backend/agent.py`**
  Structured retry → fallback → retry flow. Emits `fallback`
  events to the client. The tool-call guard rails (duplicate
  suppression, six-call cap per turn, stream stall timeout, max
  iterations) all apply across the fallback path as well.

- **`backend/main.py`**
  Auto-download of vendor files at startup. Explicit `/graph`,
  `/settings`, `/favicon.ico`, and `/favicon.svg` routes
  registered *before* the static mount. Effective-configuration
  logging during the lifespan. Router sanity check
  (`_check_router()`) asserts each router registered the
  expected minimum number of routes.

- **`backend/graph/nodes.py`**
  The `llm.chat` node now exposes a dynamic `model` select
  widget populated from `/api/llama/all-models`. Options are
  encoded as `provider::model`; the node splits the value back
  into separate `model` and `provider` params before calling the
  LLM client. This lets individual graph nodes route to Ollama,
  llama-cpp, or the fallback endpoint independently.

- **`web/graph.js`**
  Fetches the unified model list at boot and caches it for the
  dynamic widget. Maps `(default)` to an empty string in the
  serialised graph. Handles the `llm.chat` node's
  `provider::model` encoding on both serialise and deserialise.

- **`web/app.js`**
  Handles the new `fallback` event from the agent loop. The
  unified model picker now populates from
  `/api/llama/all-models` and groups options by provider with
  `<optgroup>` elements.

- **`desktop/app.py`**
  Streams agent events over SSE and renders the same
  `fallback` notice as the web UI.

- **`__version__`** in `backend/__init__.py` bumped from `1.0.0`
  to `2.0.0`.


## [1.0.0] — 2026-09-14

Initial release.

### Added

- FastAPI backend with a lifespan-managed application state.
- Single-provider LLM client pointing at a local Ollama instance.
- Linear agent loop with basic tool calling.
- SQLite session store (`sessions.db`) with a `sessions` table.
- MCP client with stdio transport.
- Minimal MCP server with hardcoded tools.
- Static chat UI (`web/index.html`).
- Prototype graph editor built on LiteGraph.
- PHP notes app backed by SQLite.
- Portable runtime bootstrap (`bootstrap/setup.py`).


---

## Version history at a glance

| Version | Date       | Highlights |
|---------|------------|------------|
| 2.0.0   | 2026-09-15 | MCP subprocess fix, fallback LLM, vendor auto-download, startup diagnostics |
| 1.0.0   | 2026-09-14 | Initial release |


## Upgrade notes

### From 1.0.0 to 2.0.0

No automated migration is required. The `2.0.0` release is a
drop-in replacement for `1.0.0` except for `.env` handling:

1. **Back up `sessions.db`** if you have chat history you want to
   keep. The `sessions` table schema is unchanged; existing rows
   load without modification.

2. **Review `.env`.** The fallback LLM variables are new and
   optional — if you do not set them, behaviour is identical to
   `1.0.0`. The primary LLM defaults have changed (they now point
   at Ollama), but any values you set explicitly in `.env` still
   take effect.

3. **Delete `web/vendor/` if it is empty or incomplete.** The
   next backend startup will download the LiteGraph files
   automatically. If you previously vendored them by hand,
   leave them in place — the downloader skips files that are
   already at least the minimum size.

4. **Restart the backend.** The vendor download and the startup
   diagnostics both run during the FastAPI lifespan. Existing
   `sessions.db` and `graph_runs` data are preserved.

There are no breaking changes to the HTTP API, the WebSocket
protocol, the graph node schema, or the MCP tool interface.