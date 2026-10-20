# Startup — 3olution Agent Graph

Every way to launch the backend, what each one does differently,
and how to pick the right one for the situation.

The application has exactly one entry point — the FastAPI app at
`backend.main:app`, served by uvicorn on `127.0.0.1:8000`. Every
launcher below ultimately calls `uvicorn.run("backend.main:app",
...)`. They differ in what happens *before* uvicorn starts.


---

## 1. The five launch paths

| # | Command | Who it is for | Portable runtime? |
|---|---|---|---|
| 1 | `start.sh` / `start.bat` | End users with the portable runtime | ✅ |
| 2 | `python run.py backend` | Developers with `.venv/` | ❌ |
| 3 | `python scripts/serve.py` | Bypassing all wrappers | Either |
| 4 | `uvicorn backend.main:app` | Advanced users who know what they are doing | Either |
| 5 | `dist/3olutionAgentGraph/...` | Packaged desktop build | Bundled inside |

Each is covered in detail below.

---

## 2. `start.sh` / `start.bat` — the portable launcher

The end-user entry point. Uses the portable Python interpreter
downloaded into `runtime/python/` by `bootstrap/setup.py`.

### What happens on start

1. `cd` to the project root.
2. Print the banner and the URL list.
3. Check for `runtime/python/python.exe` (or `bin/python3`).
4. If the runtime is missing, run `setup.sh` / `setup.ps1`
   automatically and wait.
5. Prepend `runtime/python/bin` and `runtime/php` to `PATH` so
   any subprocess the backend spawns resolves `python` and `php`
   to the portable binaries rather than the system ones.
6. Export `AGENT_HOST`, `AGENT_PORT`, `AGENT_RELOAD`,
   `PYTHONIOENCODING`, and `PYTHONUTF8`.
7. `exec` / `call` `scripts/serve.py` with the portable Python.

### Banner

```
$ ./start.sh
╔══════════════════════════════════════════════════════════════════╗
║  3olution Agent Graph — Starting Backend                         ║
║  https://3olution.com                                            ║
╚══════════════════════════════════════════════════════════════════╝

• Backend will start at http://127.0.0.1:8000

  Chat UI:       http://127.0.0.1:8000/
  Graph editor:  http://127.0.0.1:8000/graph
  Settings:      http://127.0.0.1:8000/settings
  PHP notes:     http://127.0.0.1:8000/php/

  Press Ctrl+C to stop.

2025-01-15 12:00:00 | INFO | Starting 3olution Agent Graph v2.0.0
...
```

### Flags

| Flag | Effect |
|---|---|
| `--host HOST` | Bind address (default `127.0.0.1`) |
| `--port PORT` | Bind port (default `8000`) |
| `--reload` | Enable uvicorn auto-reload (dev workflow) |
| `--help` | Show usage |

### Environment overrides

| Variable | Effect |
|---|---|
| `AGENT_HOST` | Default bind address |
| `AGENT_PORT` | Default bind port |
| `AGENT_LOG_LEVEL` | Console log level (default INFO) |

### When to use

- First run after `setup.sh` / `setup.ps1`.
- Any run where you do not want to think about which Python
  interpreter is being used.
- Copying the project to a USB stick and running it on a machine
  that has never had Python installed.

### When not to use

- You are working on the Python source and want a faster
  iteration loop. Use `python run.py backend` instead — it does
  not have to `exec` through the portable runtime.

---

## 3. `python run.py backend` — the developer launcher

The developer entry point. Assumes a `.venv/` at the project root
has been created with `python run.py setup`.

### What happens on start

1. Verify Python version is 3.10+.
2. Create `.venv/` if missing, install `requirements.txt`.
3. Ensure the LiteGraph vendor files exist; download if missing.
4. Create `models/` and `logs/` if missing.
5. `subprocess.check_call` to run `scripts/serve.py` with the
   venv's Python.

### Banner

```
$ python run.py backend
──────────────────────────────────────────────────────────────────
  Starting 3olution Agent Graph
──────────────────────────────────────────────────────────────────
• Using venv: /path/to/.venv/bin/python  (Python 3.12.11)
• Starting backend on 127.0.0.1:8000
...
```

### Advantages over `start.sh`

- Uses your own Python, so you get autocomplete, debuggers, and
  `pip install -e .` in the same interpreter.
- Faster — no `exec` through the portable launcher's checks.
- Integrates with your `.venv` activation state.

### Advantages of `start.sh`

- Does not require a Python 3.10+ install on the host.
- Is fully self-contained — the runtime lives under `runtime/`.

---

## 4. `python scripts/serve.py` — direct entry

The lowest common denominator. Used by both launchers above.

### What happens on start

`serve.py` applies three fixes before uvicorn starts, in this
order:

1. **`sys.path` and `PYTHONPATH`.** The project root is prepended
   to both, so the reloader's child process can import the
   `backend` package.
2. **Windows event loop policy.** `WindowsSelectorEventLoopPolicy`
   is set before importing uvicorn. Without this, the MCP stdio
   handshake fails roughly one in five times on Windows.
3. **`loop="asyncio"`.** uvicorn is told to use the plain asyncio
   loop, not uvloop, because uvloop's pipe handling does not
   always work with the MCP SDK's stdio transport.

### Environment variables read

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_HOST` | `127.0.0.1` | Bind address |
| `AGENT_PORT` | `8000` | Bind port |
| `AGENT_RELOAD` | `0` | `1` to enable `--reload` |
| `AGENT_LOG_LEVEL` | `info` | uvicorn log level |
| `AGENT_UVICORN_ACCESS_LOG` | `0` | `1` to enable the per-request access log |

### Why not `uvicorn backend.main:app` directly?

Because the three fixes above would not be applied. The most
common symptom of skipping `serve.py` is:

```
WARNING | MCP connect attempt 1/2 failed: Connection closed
WARNING | MCP connect attempt 2/2 failed: Connection closed
ERROR   | MCP connection failed after 2 attempt(s).
```

That is the Proactor event loop bug. Launch via `serve.py` and
it disappears.

### When to use

- When you want to see what the launchers do — this is the script
  they call.
- When you are debugging one of the three fixes.
- When your launcher does not exist yet (e.g. inside a custom CI
  script).

---

## 5. `uvicorn backend.main:app` — bypassing everything

The most direct path. Loses all three fixes.

### Command

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### What you lose

| Fix | Consequence of losing it |
|---|---|
| `sys.path` / `PYTHONPATH` | The `--reload` child process fails with `ModuleNotFoundError: No module named 'backend'`. |
| Windows event loop policy | The MCP stdio handshake fails ~20% of the time on Windows with "Connection closed". |
| `loop="asyncio"` | uvicorn's auto-detection sometimes picks uvloop, which can break the MCP SDK's pipe handling. |

### When to use

- Debugging the MCP SDK itself.
- Running on a machine where none of the three bugs apply
  (Linux, no `--reload`, no MCP).

For everything else, use `serve.py`.

---

## 6. The packaged desktop build

`pyinstaller.spec` produces a single-folder distribution:

```
dist/3olutionAgentGraph/
    ├── 3olutionAgentGraph          (or .exe)
    ├── _internal/
    │   ├── PyQt6/
    │   ├── backend/
    │   ├── mcp_server/
    │   ├── web/
    │   └── ...
    └── .env.example
```

The packaged binary is a GUI application. It launches the
desktop client, which spawns the backend as a subprocess
automatically. There is no terminal window.

### Caveats

| Platform | Caveat |
|---|---|
| Windows | UPX is disabled because it corrupts PyQt6 DLLs |
| macOS | The `.app` bundle is unsigned; Gatekeeper blocks it until you run `xattr -dr com.apple.quarantine` |
| Linux | The bundle must be run on a machine with the same or newer glibc as the build machine |
| All | The backend must still be running somewhere reachable; the bundled desktop client talks to it over HTTP |


---

## 7. Choosing the right launcher

| Situation | Recommended |
|---|---|
| First-ever install, want everything to just work | `./setup.sh && ./start.sh` |
| Development on the Python code | `python run.py backend` |
| Debugging the MCP stdio handshake | `python scripts/serve.py` with `AGENT_UVICORN_ACCESS_LOG=1` |
| Debugging uvicorn itself | `uvicorn backend.main:app --log-level debug` |
| Headless server, launched by systemd | `python scripts/serve.py` |
| Copying to a USB stick | `setup.sh` / `setup.ps1` then `start.sh` / `start.bat` |
| Running from a packaged binary | Just run the binary |


---

## 8. Startup sequence

Once uvicorn is up, the FastAPI lifespan runs in this order.
Every step is best-effort — nothing short of a Python import
error prevents the backend from starting.

| # | Step | Failure mode |
|---|---|---|
| 1 | Load logging | Non-fatal; falls back to console-only |
| 2 | Check `.env` presence | Logs a warning; defaults apply |
| 3 | Ensure LiteGraph vendor files | Logs a warning; the graph editor falls back to a CDN |
| 4 | Check web files | Logs a warning per missing file |
| 5 | Ensure PHP directories | Logs a warning per directory |
| 6 | Ensure `models/` folder | Logs a warning |
| 7 | Log the effective configuration | — |
| 8 | Construct `LLMClient` | Lazy — no sockets opened yet |
| 9 | Open `SessionStore` and create tables | Non-fatal; store becomes `None` |
| 10 | Spawn the llama-server subprocess | Non-fatal; `/api/llama/*` returns 503 |
| 11 | Connect the MCP stdio session | Non-fatal; tool calls return "session not connected" |
| 12 | Instantiate the `Agent` | — |
| 13 | Warm up the primary LLM (one-token ping) | Non-fatal; the model loads on first use |
| 14 | Register routers | The router sanity check asserts each router registered its minimum routes |
| 15 | Mount `web/` at `/` (last, so explicit HTML routes win) | — |

### Expected log output

```
INFO | Starting 3olution Agent Graph v2.0.0
INFO | Configuration file: /path/.env
INFO | LiteGraph vendor files present
INFO | Effective configuration:
INFO |   Primary LLM base URL:  http://localhost:11434/v1
INFO |   Primary LLM model:     llama3.2
INFO |   Primary LLM API key:   (placeholder)
INFO |   Fallback LLM:          (disabled)
INFO |   Ollama host:           http://127.0.0.1:11434
INFO |   llama-cpp enabled:     true
INFO |   llama-cpp base URL:    http://127.0.0.1:8080
INFO |   llama-cpp model:       Bonsai-8B.gguf
INFO |   PHP enabled:           true
INFO |   PHP SQL driver:        sqlite
INFO |   MCP command:           /path/python -m mcp_server.server
INFO |   Session DB:            sessions.db
INFO |   Models dir:            /path/models
INFO |   Logs dir:              /path/logs
INFO |   Stream tokens:         true
INFO |   Show thinking:         true
INFO | Storage initialised at sessions.db (max_graph_runs=100)
INFO | Spawning llama-server (native, model size 2.00 GB, port 8080, alias 'local')
INFO | llama-server ready at http://127.0.0.1:8080 (model=Bonsai-8B.gguf, backend=native, alias=local)
INFO | Spawning MCP server: /path/python -m mcp_server.server
INFO | MCP session initialised
INFO | Warming up primary LLM (model=llama3.2) -- this may take a while...
INFO | Primary LLM warmup complete (model=llama3.2)
INFO | Backend ready — primary LLM=llama3.2 fallback=(none) llama_cpp=running php=enabled
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

If any of these lines is replaced by a `WARNING` or `ERROR`, the
corresponding subsystem is degraded but the backend is running.
`GET /api/health` reports the current state of each.

---

## 9. Verifying the backend is up

### Browser

Open `http://127.0.0.1:8000/`. The chat page loads. The status
pill in the header reads **connected** within a second or two.

### Terminal

```bash
curl http://127.0.0.1:8000/api/health
```

Expected: a JSON object with `"status": "ok"`.

### Script

```bash
curl -s http://127.0.0.1:8000/api/health | python -m json.tool
```

The `llama_cpp.running` and `mcp_connected` fields tell you
whether the two subprocesses came up. If either is `false`, see
`ERROR_HANDLING.md`.

---

## 10. Stopping the backend cleanly

### From the terminal

Press **Ctrl+C** in the terminal running the backend. uvicorn
catches the interrupt, runs the FastAPI lifespan's `finally`
block (which closes the MCP session and stops the llama-cpp
subprocess), and exits.

### From any UI

**⏹ Stop** in the chat header, or **⏹ Stop All** in the graph
header, or **Backend → Stop All Processes…** in the desktop
client.

All three send `POST /api/system/stop-all`, which triggers a
coordinated shutdown:

1. llama-cpp subprocess stopped (`taskkill /F /T` on Windows,
   SIGTERM then SIGKILL on Unix).
2. MCP session closed.
3. Backend process exits (`os._exit(0)` on Windows, SIGTERM on
   Unix).

### Killing without cleanup

If the backend is unresponsive to Ctrl+C:

- **Windows:** `taskkill /F /IM python.exe`
- **Unix:** `pkill -9 -f "scripts/serve.py"`

This leaves the llama-cpp and MCP subprocesses orphaned. Stop
them explicitly:

- **Windows:** `taskkill /F /IM llama-server.exe` and
  `taskkill /F /IM python.exe`
- **Unix:** `pkill -9 llama-server`

---

## 11. Port conflicts

### Symptom

```
ERROR:    [Errno 98] Address already in use
ERROR:    [Errno 10048] error while attempting to bind on address
```

or (uvicorn's friendlier version):

```
ERROR:    [Errno 48] Address already in use
```

### Cause

Another process is bound to `8000`. Common culprits:

- A previous backend instance that did not shut down.
- The `llama_cpp.server` module on its default port 8000 (see
  `scripts/llama_cpp_runner.py` for the workaround).
- An unrelated service.

### Diagnosis

- **Windows:** `netstat -ano | findstr :8000`
- **Unix:** `lsof -i :8000` or `ss -ltnp | grep 8000`

### Fix

**Option A — kill the process.**

- **Windows:** `taskkill /F /PID <pid>`
- **Unix:** `kill <pid>`

**Option B — bind a different port.**

```bash
./start.sh --port 9000
# or
AGENT_PORT=9000 python scripts/serve.py
```

**Option C — move the llama-cpp server.** If the conflict is with
the llama-cpp server module, set:

```env
AGENT_LLAMA_CPP_PORT=8081
AGENT_LLAMA_CPP_BASE_URL=http://127.0.0.1:8081
```

The default `.env` already uses 8081 on the tuned Windows
configuration to avoid exactly this class of collision.

---

## 12. Binding on a LAN address

By default the backend binds to `127.0.0.1` — reachable only from
the local machine.

To accept connections from other machines on the LAN:

```bash
./start.sh --host 0.0.0.0
```

Then access it from another machine at
`http://<your-lan-ip>:8000/`.

> **Do not expose the backend to the internet.** There is no
> authentication. Anyone who can reach the URL can use the chat,
> read and write notes, load models, and stop the backend. Put a
> reverse proxy with authentication in front of it if you need
> remote access.

---

## 13. Running behind a reverse proxy

The backend serves the web UI at the same origin as the API, so
most reverse proxies work without any path rewriting.

### Nginx

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
}
```

The `Upgrade` and `Connection` headers are required for the
WebSocket endpoints. `proxy_read_timeout` should be longer than
`AGENT_STREAM_STALL_TIMEOUT` (default 360 s).

### Caddy

```
:80 {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy handles WebSocket upgrades automatically.

### Traefik

Standard HTTP router; set the `traefik.http.services.<name>.loadbalancer.server.port=8000`
label. No special WebSocket configuration needed.

### The `/php/` route

The PHP app is mounted under `/php/`. It uses
`HTTP_X_FORWARDED_PREFIX` to build its own URLs, so a proxy that
adds that header will make links work correctly even if the app
is served under a subpath.

---

## 14. Environment variables that affect startup

| Variable | Default | Affects |
|---|---|---|
| `AGENT_HOST` | `127.0.0.1` | Bind address |
| `AGENT_PORT` | `8000` | Bind port |
| `AGENT_RELOAD` | `0` | `1` enables `--reload` |
| `AGENT_LOG_LEVEL` | `INFO` | Console log level |
| `AGENT_LOG_FILE_LEVEL` | `DEBUG` | File sink level |
| `AGENT_UVICORN_ACCESS_LOG` | `0` | `1` enables the per-request access log |
| `AGENT_LLM_BASE_URL` | Ollama default | Primary LLM endpoint |
| `AGENT_LLM_MODEL` | `llama3.2` | Primary LLM model |
| `AGENT_LLM_WARMUP` | `true` | Send a one-token ping at startup |
| `AGENT_LLAMA_CPP_ENABLED` | `true` | Spawn the llama-server subprocess |
| `AGENT_PHP_ENABLED` | `true` | Mount the `/php/` routes |
| `AGENT_MCP_SERVER_COMMAND` | `sys.executable` | Interpreter for the MCP subprocess |
| `AGENT_MCP_SERVER_ARGS` | `-m mcp_server.server` | Arguments for the MCP subprocess |

Full reference: the Environment section of the backend's
`Extensive_Documentation.md`.


---

## 15. Where to go next

- `HOW_TO_USE.md` — a tour of the running application.
- `ERROR_HANDLING.md` — what to do when something goes wrong.
- `FAQ.md` — common questions.
- `INSTALLATION.md` — the install paths.
- `bootstrap.html` in the knowledge base — the setup pipeline in
  full.