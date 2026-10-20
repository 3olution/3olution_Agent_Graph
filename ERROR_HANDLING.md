# Error Handling — 3olution Agent Graph

The error taxonomy, the recovery procedure for each class of
failure, and where the diagnostics live.

The backend is designed so that **no single subsystem failure
prevents the process from starting**. If the LLM is unreachable,
the MCP subprocess fails, the llama-cpp server cannot load a
model, or PHP is missing, the backend logs a specific message and
continues. `GET /api/health` reports the current state of every
subsystem so a client can see exactly what is degraded.


---

## 1. Error classes at a glance

| Class | Where it originates | Retried automatically? | Backend still starts? |
|---|---|---|---|
| LLM transport errors | `backend/llm.py` | ✅ (3 attempts, exponential backoff) | Yes |
| LLM 4xx errors | `backend/llm.py` | ❌ | Yes |
| Model rejects `tools` | `backend/agent.py` | Retried without tools | Yes |
| MCP connect failure | `backend/mcp_client.py` | Once with a fresh `AsyncExitStack` | Yes, without tools |
| MCP tool call failure | `backend/mcp_client.py` | ✅ (transport-level errors only) | Yes |
| llama-cpp subprocess death | `backend/llama_server.py` | ✅ at startup, no mid-request | Yes, `/api/llama/*` returns 503 |
| PHP execution failure | `backend/php_server.py` | ❌ | Yes, `/php/` returns 500 |
| PHP binary missing | `backend/php_server.py` | ❌ | Yes, `/php/` returns 500 with install hint |
| SQLite storage failure | `backend/storage.py` | ❌ | Yes, sessions become ephemeral |
| Bootstrap download failure | `bootstrap/setup.py` | Per-mirror fallback | Bootstrap fails for that component |
| Port in use | uvicorn | ❌ | No — uvicorn exits |
| Graph cycle | `backend/graph/engine.py` | ❌ | Yes; a `graph_error` event is emitted |


---

## 2. LLM error classification

Every LLM call funnels through `retry_async` with the
`is_retryable` predicate from `backend/llm.py`.

### Retryable

| Exception | Reason |
|---|---|
| `openai.APIConnectionError` | Transient network blip |
| `openai.APITimeoutError` | Transient slowness |
| `openai.RateLimitError` | Backoff usually helps |
| `openai.InternalServerError` (5xx) | Unless the body matches a permanent marker |

### Not retryable

| Exception | Reason |
|---|---|
| `openai.BadRequestError` (400) | The request itself is wrong |
| `openai.AuthenticationError` (401) | A bad API key will not fix itself |
| `openai.PermissionDeniedError` (403) | Forbidden requests stay forbidden |
| `openai.NotFoundError` (404) | The model does not exist |

### Permanent 5xx markers

A 5xx response is **not** retried if its body contains any of
these substrings:

```
unknown pre-tokenizer
error loading model
llama-server process has terminated
failed to load model
does not support tools
model not found
```

Retrying a permanent error costs three waits of 1 s, 2 s, 4 s and
floods the log. The classifier treats these as terminal.

### Backoff timing

The wait before attempt N is `AGENT_RETRY_BACKOFF * 2**(N-1)`.
With the defaults (`attempts=3`, `backoff=1.0`):

| Attempt | Wait before |
|---|---|
| 1st | immediate |
| 2nd | 1.0 s |
| 3rd | 2.0 s |

Two control-flow exceptions never pass through the retry loop:
`KeyboardInterrupt`, `SystemExit`, and `asyncio.CancelledError`
propagate immediately.

### Client-visible behavior

The chat UI renders retry attempts as yellow centered notices:

> ↻ LLM connection failed — retrying (attempt 2/3)…

and fallback switches as teal centered notices:

> ⚡ Primary LLM unavailable — switching to fallback (gpt-4o-mini).
> Reason: Connection refused


---

## 3. The "does not support tools" special case

Some models reject the `tools` parameter with a 400 error. This
is not treated as a fatal error.

### What happens

1. The 400 is caught in `_run_llm_turn`.
2. `is_no_tools_error(exc)` returns `True` — it matches any of:
   - `"does not support tools"`
   - `"tools are not supported"`
   - `"tool"` together with `"not supported"`
3. `mark_model_without_tools(model)` adds the model name to the
   runtime cache `_MODELS_WITHOUT_TOOLS`.
4. An `info` event is emitted:
   > ℹ Model 'llama3.2:1b' does not support tool use. Continuing
   > without tools.
5. The turn is retried **without** the `tools` parameter.

On every subsequent request, `should_use_tools()` returns `False`
for that model and the parameter is stripped before the SDK call.

### Four layers of tool stripping

| Layer | Trigger |
|---|---|
| 1 | `disable_tools=True` on the request (per-request toggle) |
| 2 | `AGENT_DISABLE_TOOLS=true` in `.env` (global) |
| 3 | Model name in `AGENT_NO_TOOLS_MODELS` (pre-configured) |
| 4 | Model name in the runtime cache (auto-learned) |

To add a model to layer 3 permanently, add it to `.env`:

```env
AGENT_NO_TOOLS_MODELS=llama3.2:1b,some-finetune:latest
```


---

## 4. Agent guard rails

Four independent guard rails keep small models from looping or
hanging. Each fires independently; a runaway model will hit each
one in turn and produce a coherent error.

### 4.1 Duplicate tool-call suppression

**Trigger:** The same `(tool_name, canonical_args)` pair is
requested twice in one turn.

**Behavior:** The second call is replaced with a canned message:

> (skipped: `multiply` was already called with these exact
> arguments in this turn — produce a final answer now)

**Why:** Small models often loop on the same call when a tool
result is confusing. Replacing the second call with an
instruction nudges them to stop.

### 4.2 Tool-call cap

**Trigger:** `MAX_TOOL_CALLS_PER_TURN` (6) distinct calls in one
turn.

**Behavior:** `tools` is stripped from subsequent LLM calls. The
model is forced to answer with text.

**Why:** A tool-calling loop that never reaches an answer is worse
than a short "I can't complete this" reply.

### 4.3 Stream stall timeout

**Trigger:** No token arrives for `AGENT_STREAM_STALL_TIMEOUT`
seconds (default 360) mid-stream.

**Behavior:** A `TimeoutError` is raised with the timeout
duration. The turn aborts. The client sees a final event.

**Why:** Catches the class of hang where a model accepts the
connection, starts streaming, then goes silent. Without this, the
turn blocks until the HTTP client's own timeout — often unlimited
for streaming.

### 4.4 Max iterations

**Trigger:** `AGENT_MAX_ITERATIONS` (10) turns without a final
answer.

**Behavior:** A final event explains:

> Reached max iterations (10) without a final answer. Try
> rephrasing or increasing `AGENT_MAX_ITERATIONS`.

**Why:** Prevents an infinite ping-pong of tool calls between
agent and model.

### Tuning the guards

```env
AGENT_MAX_ITERATIONS=10
AGENT_STREAM_STALL_TIMEOUT=360.0
```

`MAX_TOOL_CALLS_PER_TURN` is a module-level constant in
`backend/agent.py`, not configurable via `.env`. Change it in
code if you need to.


---

## 5. MCP errors

### 5.1 Subprocess will not start

**Symptom in the backend log:**

```
ERROR | MCP connection failed at startup: <exception>
WARNING | The app will start WITHOUT tools. Every tool call will
        return 'Error: MCP session not connected.'
To diagnose, run the MCP server standalone:
    python -m mcp_server.server
```

**Recovery:**

1. Run the MCP server manually to see the real error:
   ```bash
   python -m mcp_server.server
   ```
2. If it prints a Rich banner with box-drawing characters
   before sitting silent, the SDK version is old enough that
   `mcp.run(show_banner=False)` raised `TypeError` and the
   fallback called plain `mcp.run()`. Upgrade:
   ```bash
   pip install 'mcp>=2.0.0'
   ```
3. If it prints a traceback, read the traceback.
4. If it sits silent with no banner and no error, the problem is
   on the backend side — see §5.2.

### 5.2 "Connection closed" on Windows

**Symptom:**

```
WARNING | MCP connect attempt 1/2 failed: Connection closed
WARNING | MCP connect attempt 2/2 failed: Connection closed
ERROR   | MCP connection failed after 2 attempt(s).
```

**Cause:** The Windows Proactor event loop is incompatible with
the MCP SDK's stdio pipe reads. The handshake fails ~20% of the
time.

**Recovery:** Launch the backend via `scripts/serve.py` (or any
of the launchers, which call it) instead of importing uvicorn
directly. `serve.py` sets `WindowsSelectorEventLoopPolicy` before
importing uvicorn.

### 5.3 Tools fail at runtime

**Symptom:** A tool call returns an error string instead of a
result.

**Behavior:** The error string is appended to the conversation as
the tool result. The model sees it and can react.

**Two classes:**

| Error message | Cause | Recovery |
|---|---|---|
| `Error: MCP session not connected.` | The subprocess is not running | Restart the backend, or see §5.1 |
| `Error: tool 'X' timed out after 60s` | The tool exceeded `AGENT_TOOL_TIMEOUT` even after retries | Raise `AGENT_TOOL_TIMEOUT` or fix the tool |
| `Error calling tool 'X': <exception>` | The tool itself raised | Read the exception, fix the tool |

### 5.4 A new tool does not appear

**Checklist:**

1. Backend log for `Failed to load MCP tool module: …` or
   `MCP tool loader: N loaded, M failed`.
2. File name starts with `_`? Underscore-prefixed files are
   skipped by design. Rename.
3. Import from `.._server` (not `..server`).
4. File is in `mcp_server/tools/`, not `mcp_server/`.
5. Backend was restarted. There is no hot reload.

To test a module in isolation:

```bash
python -c "import mcp_server.tools.my_tools"
```


---

## 6. llama-cpp errors

### 6.1 Model file not found

**Symptom:**

```
ERROR | llama-cpp: model file not found: Bonsai-8B.gguf
  Looked in: /path/models
  Fix: drop a .gguf file in the models folder, set
       AGENT_LLAMA_CPP_MODEL to its filename, or set
       AGENT_LLAMA_CPP_ENABLED=false to skip llama-cpp.
```

**Recovery:** Download a GGUF file into `models/`, or set
`AGENT_LLAMA_CPP_ENABLED=false` to disable the local server
entirely.

### 6.2 Subprocess crashes at startup

**Symptom in the backend log:**

```
ERROR | llama-server exited with code <N> - last 30 lines:
    <subprocess output>
```

**Common causes:**

| Exit code | Cause | Recovery |
|---|---|---|
| `0xC000001D` | CPU lacks AVX-512; the Python module's wheel requires it | Download a native `llama-server.exe` from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) and extract into `runtime/llama-server/` |
| `1` | Model file is corrupted or an incompatible format | Re-download the file |
| `-1073741819` (Windows) | Access violation — usually a missing DLL | Verify the sidecar DLLs are next to the binary |

The manager logs the last 30 lines of subprocess output when the
process dies. Read them before doing anything else.

### 6.3 No backend available

**Symptom:**

```
ERROR | llama-cpp: no backend available.
  Either:
    1. Download a native llama-server binary from
       https://github.com/ggml-org/llama.cpp/releases
       and extract into runtime/llama-server/
    2. Install llama-cpp-python:
       pip install llama-cpp-python --prefer-binary \
         --extra-index-url https://jllllll.github.io/llama-cpp-python-cuBLAS-wheels/AVX2/cpu
```

**Recovery:**

- **Preferred:** Download a native binary (see §6.2).
- **Alternative:** Install the Python module with the AVX2 wheel
  index shown in the message. The default PyPI build requires
  AVX-512 on Windows.

### 6.4 Load times out

**Symptom:**

```
ERROR | llama-server did not become ready within 360s.
```

**Causes and recovery:**

| Cause | Recovery |
|---|---|
| Model too large for available RAM | Use a smaller quant (`Q4_K_M` instead of `Q8_0`) or a smaller model |
| Disk is very slow | Raise `AGENT_LLAMA_CPP_STARTUP_TIMEOUT` |
| Wrong chat format causes an infinite template loop | Set `AGENT_LLAMA_CPP_CHAT_FORMAT` to match the model family |

### 6.5 Garbage output from a loaded model

**Symptom:** The model responds, but the text is incoherent
characters or immediately terminates with an EOS token.

**Cause:** The chat template does not match the model's expected
format. The model reads `<|im_start|>` markers as ordinary
tokens.

**Recovery:** Set `AGENT_LLAMA_CPP_CHAT_FORMAT` in `.env`:

| Model family | Chat format |
|---|---|
| Llama 3.x | `llama-3` |
| Llama 2 | `llama-2` |
| Mistral | `mistral` |
| Qwen 2.x | `qwen` |
| ChatML-based (Hermes, Phi) | `chatml` |

Then unload and reload the model. There is no need to restart the
backend; the settings page has an Unload button and the model
picker reloads it on the next message.

### 6.6 `/api/llama/*` returns 503

**Symptom:** The model picker is empty, or the Load GGUF button
fails with a 503.

**Causes:**

| Message | Cause |
|---|---|
| `llama-cpp is disabled. Set AGENT_LLAMA_CPP_ENABLED=true …` | The feature is disabled in `.env` |
| `llama-cpp manager is not present in app state` | The manager failed to initialise during startup |
| `llama-cpp server is not running` | The subprocess died |

**Recovery:** Check the backend log for the startup message. If
the manager failed to initialise, the log shows the traceback.


---

## 7. PHP errors

### 7.1 "PHP binary not found"

**Symptom:** `/php/` returns 500 with:
> PHP binary not found.
> See /api/php/status for details, or install PHP and set
> AGENT_PHP_BINARY in .env.

**Recovery:**

1. Check `GET /api/php/status` — the response names the binary
   path it tried.
2. If the binary is missing, run the PHP-only bootstrap:
   ```bash
   python bootstrap/setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor
   ```
3. If you have a system PHP, set `AGENT_PHP_BINARY=/path/to/php-cgi`.

### 7.2 "Access is denied" on Windows

**Symptom:** The backend logs `WinError 5: Access is denied`
when trying to spawn `php.exe`.

**Cause:** One of three things:

1. Mark of the Web on the freshly extracted binaries.
2. Windows Defender blocking the process.
3. A third-party antivirus blocking it.

**Recovery:** Run `fix_php.bat` (or `fix_php.ps1` directly). It
attempts all three fixes and prints vendor-specific exclusion
instructions for known AV products.

Manual alternatives:

```powershell
# Fix 1: remove Mark of the Web
Get-ChildItem runtime\php -Recurse | Unblock-File

# Fix 2: Defender exclusion (as Administrator)
Add-MpPreference -ExclusionPath "$PWD\runtime\php"
Add-MpPreference -ExclusionPath "$PWD\php_app"

# Fix 3: disable PHP entirely
# (edit .env) AGENT_PHP_ENABLED=false
```

### 7.3 "PDO driver 'sqlite' is not installed"

**Symptom:** `/php/` renders a "Database error" page with the
message.

**Cause:** The `pdo_sqlite` extension is not enabled in PHP.

**Recovery:**

1. Check `runtime/php/php.ini` exists and contains
   `extension=pdo_sqlite`.
2. Re-run the PHP-only bootstrap to regenerate `php.ini`.
3. Verify with:
   ```bash
   runtime/php/php.exe -m | grep pdo
   ```

### 7.4 "could not find driver"

**Cause:** Same as §7.3, but with a less helpful message from
PHP before the preflight was added. Verify `pdo_sqlite` is listed
in `php -m`.

### 7.5 Timeouts

**Symptom:** `/php/` returns 504.

**Cause:** The PHP script took longer than `AGENT_PHP_TIMEOUT`
(default 60 s).

**Recovery:**

- Raise `AGENT_PHP_TIMEOUT` in `.env`.
- Or investigate why the script is slow (missing `php.ini`,
  corrupted SQLite file, network-backed file system).

### 7.6 Path traversal rejected

**Symptom:** `/php/../../etc/passwd` returns 400.

**Cause:** This is correct behavior. The route rejects paths that
resolve outside `php_app/`.

The warning is logged at WARNING level:
> Rejected path traversal attempt: ../../etc/passwd


---

## 8. Storage errors

### 8.1 Session store init failed

**Symptom:**

```
ERROR | Session store init failed: <exception>
```

**Consequence:** `state["store"]` becomes `None`. The agent
continues to run but does not save session histories. Chat still
works within a page load; history is lost on reload.

**Causes:**

| Cause | Recovery |
|---|---|
| `sessions.db` is corrupted | Delete it. The next start recreates it empty |
| The file is locked by another process | Stop the other backend instance |
| The disk is full | Free space |

### 8.2 "database is locked"

**Cause:** SQLite's single-writer semantics. Two backend
instances are running, or a `sqlite3` shell has the file open.

**Recovery:**

1. Ensure only one backend is running.
2. Close any `sqlite3 sessions.db` shells.
3. If the error persists, check `sessions.db-wal` and
   `sessions.db-shm` are not held open by an editor or antivirus.

### 8.3 Graph run not saved

**Symptom:** The graph executes successfully, but the run does
not appear in the History panel.

**Cause:** The `graph_runs` insert failed. The backend logs:

```
WARNING | Could not save graph run: <exception>
```

**Recovery:** Same as §8.1.


---

## 9. Bootstrap errors

### 9.1 Download fails with HTTP 403 or 404

**Cause:** The pinned version was retired upstream.

**Recovery:** Check the version candidate lists at the top of
`bootstrap/setup.py` and add a newer version. For PHP Windows:

```
https://windows.php.net/downloads/releases/php-8.4.15-Win32-vs17-x64.zip
```

Browse [windows.php.net/download/](https://windows.php.net/download/)
to confirm which versions are currently available.

### 9.2 "Archive is too small after download"

**Cause:** A captive portal or proxy returned an HTML error page
instead of the actual file.

**Recovery:** Verify with `curl -I <url>`. If `Content-Type` is
`text/html`, configure the proxy or use a different network.

### 9.3 Antivirus quarantines php-cgi.exe

**Cause:** False positive on freshly-extracted PHP binaries.

**Recovery:**

1. Add `<project>/runtime/php/` to the AV exclusion list.
2. Or use Windows Defender, which does not false-positive.
3. Or install PHP system-wide and set `AGENT_PHP_BINARY`.

### 9.4 pip install fails on a specific package

**Cause:** The package needs a C compiler.

**Recovery:**

- **Windows:** Install
  [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).
- **Linux:** `sudo apt install build-essential python3-dev`.
- **macOS:** `xcode-select --install`.

Or use `--only-binary :all:` to see which packages lack wheels.


---

## 10. Graph engine errors

### 10.1 Graph contains a cycle

**Symptom:**

```json
{"type": "graph_error", "error": "Graph contains a cycle. Only DAGs are supported in v1. Use control.foreach for iteration."}
```

**Cause:** The graph is not a DAG. This includes self-loops
(`a → a`), two-node cycles (`a → b → a`), and longer cycles.

**Recovery:** Rework the graph to remove the cycle. Use
`control.foreach` if you need iteration.

### 10.2 Unknown node type

**Symptom:**

```json
{"type": "node_error", "node": "n1", "error": "Unknown node type: some.type"}
```

**Cause:** The graph references a node type that is not
registered. Common causes:

1. A custom node that failed to load (check the log for a
   traceback).
2. A node type that was renamed.
3. A hand-edited JSON file with a typo.

**Recovery:** Open the graph in the editor and re-add the node
from the palette, or fix the JSON.

### 10.3 Node raised an exception

**Symptom:**

```json
{"type": "node_error", "node": "n3", "error": "<exception message>"}
```

**Cause:** The node's Python function raised. The full traceback
is logged at ERROR level:

```
ERROR | Node 'n3' (llm.chat) failed
<traceback>
```

**Recovery:** Read the traceback. Common causes:

- LLM client not available (`ctx.app.get("llm")` returned None)
- Missing model file
- Bad `params` value


---

## 11. Startup errors

### 11.1 Port in use

See `STARTUP.md` §11.

### 11.2 "Address already in use" on the llama-cpp port

**Symptom:** The llama-server subprocess fails to start because
port 8080 or 8081 is held.

**Recovery:** Change `AGENT_LLAMA_CPP_PORT` and
`AGENT_LLAMA_CPP_BASE_URL` in `.env`.

### 11.3 ModuleNotFoundError: No module named 'backend'

**Cause:** Running uvicorn with `--reload` without the `serve.py`
fix. The reloader's child process does not inherit `sys.path`
mutations on Windows.

**Recovery:** Launch via `scripts/serve.py` or any of the
launchers, which set `sys.path` and `PYTHONPATH` before importing
uvicorn.

### 11.4 ImportError: No module named 'mcp'

**Cause:** The MCP SDK is not installed.

**Recovery:**

```bash
runtime/python/python.exe -m pip install "mcp>=2.2.0"
```

Or re-run the bootstrap.

### 11.5 ModuleNotFoundError: No module named 'PyQt6'

**Cause:** The desktop client's optional dependencies are
missing.

**Recovery:**

```bash
runtime/python/python.exe -m pip install PyQt6 PyQt6-WebEngine
```

Or use the browser UI instead — PyQt6 is not required for the
backend.


---

## 12. Reading the logs

Logs are written to `logs/`:

- `backend-YYYY-MM-DD.log` — the backend, at DEBUG level
- `desktop-YYYY-MM-DD.log` — the desktop client, at DEBUG level

Files rotate at midnight UTC and are pruned after
`AGENT_LOG_RETENTION_DAYS` (default 14).

### Three ways to read them

**From the desktop client:** View menu → **View Logs…** (Ctrl+L).

**From the browser:**

```
GET /api/logs/recent?lines=500&source=all
GET /api/logs/recent?lines=1000&source=backend
GET /api/logs/files
```

**From the terminal:**

```bash
# Unix
tail -f logs/backend-*.log

# Windows PowerShell
Get-Content logs\backend-*.log -Wait -Tail 50

# Last 100 ERROR lines
grep ERROR logs/backend-*.log | tail -100
```

### Log line format

```
2025-01-15 12:34:56.789 | INFO     | backend.llm:142 — LLM request (auto, batch): model=llama3.2 messages=3 tools=9
```

| Field | Meaning |
|---|---|
| Timestamp | Millisecond precision, UTC |
| Level | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| Module:line | Where the log call originated |
| Message | The actual text |

### What to look for

| Symptom | Search for |
|---|---|
| LLM issues | `LLM` |
| MCP issues | `MCP`, `tool` |
| llama-cpp issues | `llama-server`, `llama-cpp` |
| PHP issues | `PHP`, `php-cgi` |
| Subprocess output | `[llama-server]` (llama-cpp stdout at DEBUG) |
| Every retry | `Retrying in` |
| Every failure | `failed` |


---

## 13. The health endpoint as a diagnostic

`GET /api/health` is the single fastest way to see what is
degraded.

```json
{
  "status": "ok",
  "version": "2.0.0",
  "primary_llm": { "base_url": "...", "model": "llama3.2" },
  "fallback_llm": { "enabled": false },
  "llama_cpp": {
    "enabled": true,
    "running": true,
    "ready": true,
    "loaded_model": "Bonsai-8B.gguf",
    "base_url": "http://127.0.0.1:8080"
  },
  "mcp_connected": true,
  "php": { "enabled": true, "running": false, "driver": "sqlite" }
}
```

### What each field means for troubleshooting

| Field | False/empty → |
|---|---|
| `mcp_connected` | Tool calls will return "session not connected". Restart the backend and read the log. |
| `llama_cpp.running` | The local server subprocess is not up. Check the backend log. |
| `llama_cpp.ready` | The subprocess exists but does not answer `/v1/models`. It may still be loading the model. Wait and retry. |
| `llama_cpp.loaded_model` | `null` means no model is loaded. Load one from the settings page. |
| `fallback_llm.enabled` | `false` means the primary failure will not fail over. Set `AGENT_LLM_FALLBACK_BASE_URL` in `.env`. |


---

## 14. Recovery checklist

When something is wrong, work through this list in order:

1. **`GET /api/health`** — which subsystem is not green?
2. **`GET /api/logs/recent?lines=500`** — what did the last 500
   lines say?
3. **Terminal running the backend** — is it still alive? Are
   there new error lines on stderr?
4. **`GET /api/system/status`** — full state including PIDs.
5. **Subprocess output** — for llama-cpp failures, the last 30
   lines of the crashed subprocess are already in the log.
6. **The specific subsystem's section above** — LLM, MCP,
   llama-cpp, PHP, storage, bootstrap.

In most cases, the log line names the exact problem. The rest of
this document is a reference for what those names mean.


---

## 15. Where to go next

- `HOW_TO_USE.md` — a tour of the running application.
- `STARTUP.md` — launch options and their differences.
- `FAQ.md` — common questions.
- `INSTALLATION.md` — the install paths and their troubleshooting
  sections.
- The `Extensive_Documentation.md` in each subfolder — deep
  coverage of that subsystem.