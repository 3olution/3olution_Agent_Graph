# FAQ — 3olution Agent Graph

Common questions grouped by topic, with short answers and
pointers to the relevant documentation.


---

## Installation

### What do I actually need to install?

On the host: **Python 3.10 or newer** (3.11+ recommended) and
**pip**. That's it.

Everything else — a portable Python 3.12, PHP 8.4 (CGI + CLI),
and SQLite 3.46 — is downloaded into the project's `runtime/`
folder by `bootstrap/setup.py`. Nothing is installed system-wide.

If you want an LLM, install [Ollama](https://ollama.com) too.

### Should I use the portable runtime or the developer workflow?

| If you… | Use |
|---|---|
| Just want the app to work | Portable (`bootstrap/setup.py`) |
| Are editing the Python source | Developer (`python run.py setup`) |
| Are on a shared machine with no admin rights | Portable |
| Are on a CI runner with a pinned Python already | Developer |

Both run the same backend code. The difference is which Python
interpreter executes it. The two coexist cleanly — you can have
both `.venv/` and `runtime/` without conflicts.

### How long does the first install take?

2–5 minutes on a fresh checkout. The bulk is downloading ~90 MB
of runtime archives. Re-runs take a couple of seconds — the
bootstrap is idempotent.

### Can I install it on a USB stick?

Yes. Run `setup.sh` / `setup.ps1` once to populate `runtime/`,
then copy the whole project folder to the stick. The portable
runtime is fully relocatable within the same OS family.

Do not copy `runtime/` across platforms — a Windows `runtime/`
will not work on Linux. Re-run the bootstrap on the target
platform.

### Where does the app store user data?

| Path | Contents |
|---|---|
| `sessions.db` | Chat sessions and graph run history |
| `php_app/data/app.db` | PHP notes app database |
| `models/*.gguf` | Downloaded model weights |
| `logs/` | Rotating daily log files |

None of these are committed to git. Back them up if the data
matters.

### How do I uninstall?

Delete the whole project folder. Nothing was installed outside it.

To remove only the portable runtime while keeping user data:

```bash
rm -rf runtime/                 # Unix
rmdir /s /q runtime             # Windows
```

The next `setup.sh` / `setup.ps1` recreates it.

### Do I need admin / root?

No. The install touches only the project folder. The only
operation that might prompt for elevation is `fix_php.bat` on
Windows, and only because it modifies the ACLs and Defender
exclusions for the freshly extracted PHP binaries.

### Does it work on Apple Silicon?

Yes. The portable Python and PHP are available as arm64 builds.
`PyQt6` and `PyQt6-WebEngine` have universal2 wheels for macOS.
The launchers detect the architecture automatically.

### Does it work on ARM Linux?

Yes, for the backend. The portable Python has an aarch64 build.
PHP and SQLite also build for aarch64. Desktop client support
depends on PyQt6's aarch64 wheel availability for your
distribution.


---

## LLMs

### Why is Ollama the default?

Because it is free, runs locally, requires no API key, and works
on Windows, macOS, and Linux. It also manages model downloads and
unloads models after an idle period — features the backend can
leverage without reimplementing them.

Ollama is not a dependency. Any OpenAI-compatible endpoint works.

### Can I use OpenAI instead?

Yes. Set three values in `.env`:

```env
AGENT_LLM_BASE_URL=https://api.openai.com/v1
AGENT_LLM_API_KEY=sk-...your-key...
AGENT_LLM_MODEL=gpt-4o-mini
```

Restart the backend. The chat and graph pages route to OpenAI on
every request.

### Can I use a local OpenAI-compatible server other than Ollama?

Yes. LM Studio, vLLM, llama.cpp's own `llama-server`, and
OpenRouter all work. Set `AGENT_LLM_BASE_URL` to the server's
`/v1` endpoint.

| Provider | Base URL | API key |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | any |
| LM Studio | `http://localhost:1234/v1` | any |
| vLLM | `http://localhost:8001/v1` | any |
| OpenRouter | `https://openrouter.ai/api/v1` | your key |
| Groq | `https://api.groq.com/openai/v1` | your key |
| Together | `https://api.together.xyz/v1` | your key |

### What is the fallback LLM?

An optional secondary OpenAI-compatible endpoint. When the
primary endpoint fails after all its retries, the agent switches
to the fallback and emits a `fallback` event that the UI renders
as a teal notice.

Set it in `.env`:

```env
AGENT_LLM_FALLBACK_BASE_URL=https://api.your-company.com/v1
AGENT_LLM_FALLBACK_API_KEY=your-key
AGENT_LLM_FALLBACK_MODEL=your-model
```

Leave `AGENT_LLM_FALLBACK_BASE_URL` empty to disable fallback.
The primary failure then produces a final error event directly.

### Why is the first response so slow?

The first request to a cold model spends 20–60 seconds loading
weights from disk into memory. The backend sends a one-token
"ping" at startup to move this cost off the first user message,
controlled by `AGENT_LLM_WARMUP=true` (the default).

If warmup is disabled, or if the process has been idle long
enough that Ollama unloaded the model, the first message after
that idleness pays the load cost.

### How do I switch models without restarting?

Use the **Model** dropdown in the chat header. The list is
populated from `GET /api/llama/all-models`, which returns both
Ollama models and local GGUF files.

If `AGENT_OLLAMA_UNLOAD_ON_SWITCH=true` (default), switching
models unloads the previous one from Ollama's VRAM.

### Can I use a reasoning model like DeepSeek-R1?

Yes. The backend parses reasoning output from three sources:

1. `<thinking>…</thinking>` blocks (Anthropic-style)
2. ` thinking…` blocks (DeepSeek-R1, Qwen 3)
3. The `reasoning_content` field that some OpenAI-compatible
   endpoints emit

Reasoning is streamed to the UI as `thinking` events and rendered
in a collapsible panel above the answer. It is never persisted to
the conversation history.

`AGENT_SHOW_THINKING=false` (or the settings toggle) disables the
panel and strips reasoning silently.

### Can the model call tools?

Yes, if it supports function calling. The backend passes the
tool schemas from the MCP server on every request. If a model
returns a "does not support tools" error, the backend
automatically strips the parameter and retries, and remembers
this for the lifetime of the process.

To suppress tool use entirely:

- Uncheck **No tools** in the chat header (per-session), or
- Set `AGENT_DISABLE_TOOLS=true` in `.env` (global), or
- Add the model to `AGENT_NO_TOOLS_MODELS` (per-model).

### Why does the agent sometimes answer the same question twice?

The agent loop is iterative. If the LLM emits a tool call, the
agent executes it and runs another LLM turn with the result
appended. Occasionally a small model will emit a tool call after
already having the information, and the second turn produces a
duplicate answer.

The duplicate tool call is caught by the guard rails (`HOW_TO_USE.md`
§7, `ERROR_HANDLING.md` §4.1), which replaces the second call
with an instruction to produce a final answer. If the model still
duplicates the text, that is a model behavior, not a bug in the
framework.


---

## Models

### Where do I download GGUF files?

Hugging Face. The `huggingface-cli` tool is the easiest way:

```bash
pip install huggingface-hub
cd models
huggingface-cli download bartowski/Llama-3.2-3B-Instruct-GGUF \
    Llama-3.2-3B-Instruct-Q4_K_M.gguf --local-dir .
```

See `models/README.md` for a table of popular repositories.

### Which quantization should I pick?

`Q4_K_M` for most cases. It is the balanced default — small
quality drop, roughly a quarter of the fp16 size.

`Q8_0` if you have RAM and care about quality. `Q3_K_S` or
`Q4_0` if you are tight on space. `Q2_K` only as a last resort.

Full explanation in `models/README.md`.

### Why is my GGUF file not showing up in the model picker?

Three things to check:

1. Is the extension lowercase `.gguf`? On Linux,
   `glob("*.gguf")` is case-sensitive.
2. Is the file at the top level of `models/`? The listing does
   not recurse.
3. Was the backend restarted? The list is fetched on page load;
   a cached page shows the old list.

Verify with:

```bash
curl http://127.0.0.1:8000/api/llama/runtime/models
```

### Can I use Ollama models and local GGUF files at the same time?

Yes. The model picker lists both, grouped by provider. Each
option's value is encoded as `provider::model` and routed
accordingly.

You can even route individual graph nodes to different providers.
The `llm.chat` node's model dropdown contains the same encoded
values.

### Why does my loaded model produce garbage output?

Almost always a chat format mismatch. The chat template the
model expects is different from the one the server is using.

Set `AGENT_LLAMA_CPP_CHAT_FORMAT` to match the model family:

| Family | Format |
|---|---|
| Llama 3.x | `llama-3` |
| Llama 2 | `llama-2` |
| Mistral | `mistral` |
| Qwen 2.x | `qwen` |
| ChatML models (Hermes, Phi) | `chatml` |

Then unload and reload the model.

### How do I free VRAM without stopping the backend?

Open **Settings → LLM Runtime** and click **Unload**, or:

```bash
curl -X POST http://127.0.0.1:8000/api/llama/runtime/unload
```

The model file is not deleted, only unloaded. The next load starts
the subprocess again.

### Can I run two models at once?

No, not with the built-in llama-cpp server. It manages a single
subprocess. To use a second model concurrently you would need a
second backend instance with a different `AGENT_LLAMA_CPP_PORT`.

Ollama, however, can hold multiple models in memory up to the
available RAM/VRAM. The chat page's busy pill shows which ones
are loaded.

### How do I switch the default model?

Edit `.env`:

```env
AGENT_LLM_MODEL=llama3.2
```

Restart the backend. The chat page uses this value when the
model picker is set to **(default)**.


---

## Performance

### Why is CPU inference so slow?

The model runs entirely on the CPU, one token at a time. A 7B
model at Q4_K_M on a modern CPU produces roughly 3–10 tokens per
second. The GPU is much faster when it is supported.

To check whether GPU offload is active:

```bash
curl http://127.0.0.1:8000/api/llama/install/status
```

The `gpu_offload` field is `true` for a GPU-enabled build.
`llama_cpp.backend=running` in the health endpoint means the
subprocess is up but does not indicate offload.

### How do I enable GPU offload?

Set `AGENT_LLAMA_CPP_N_GPU_LAYERS` in `.env`:

- `-1` — offload all layers (best case, needs enough VRAM)
- `0` — CPU only
- Positive N — offload the first N layers

You also need a llama-cpp build that supports the GPU. On
Windows, the Python module's wheels are CPU-only; use a native
`llama-server.exe` with CUDA support from the
[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
page.

### Why is the first request so slow even with warmup?

Because warmup only loads the model into RAM. The first request
still has to tokenize the system prompt, run a forward pass, and
start generating. Subsequent requests reuse the loaded model and
skip the load cost.

### Why does my agent take 30+ seconds to answer?

Three possible causes, in order of likelihood:

1. The model is too large for the machine, so it is swapping.
2. The model is thinking (reasoning models can spend 20–60 s on
   a hard question before producing the first answer token).
3. A tool call is slow (e.g. `read_file` on a very large file).

The chat UI shows a live `thinking` panel when the model
produces reasoning tokens, so you can tell which case applies.

### Can I run it on a Raspberry Pi?

Yes, for the backend. The portable Python has an aarch64 build.
`PyQt6` may not have an ARM wheel for your distro — use the web
UI or `desktop.native_chat` instead.

Model inference is very slow on a Pi (less than 1 token/s for a
7B model). Use a 1B or 3B model with a small quant.


---

## Security

### Is the backend secure by default?

The backend binds to `127.0.0.1` and has no authentication. On a
single-user machine it is as safe as any other local service —
nothing else can reach it.

It is **not** designed for public deployment. Anyone who can
reach the port can:

- Send messages to the LLM (which may cost money on a paid
  endpoint)
- Read and write files via the `filesystem_tools` MCP tools
- Read and write notes in the PHP app
- Load or unload models
- Stop the backend

### What if I want to expose it on a network?

Put a reverse proxy with authentication in front of it. The
backend serves everything over plain HTTP on a single port, so
any standard HTTP auth mechanism works.

See `STARTUP.md` §12 and §13.

### Are there any rate limits?

No. The backend does not track per-client usage. Add rate limits
at the reverse proxy if you need them.

### Can I run it in Docker?

Yes. The Dockerfile would need to include the portable runtime
folder (or run the bootstrap on first start). The PHP CGI and
llama-cpp subprocesses work in a container.

There is no official Dockerfile in the project yet — see
`FUTURE_EXTENSIONS.md` for the item on the roadmap.

### Is my conversation private?

Locally, yes. Sessions are stored in `sessions.db` in the project
folder. Nothing is sent anywhere except to the LLM endpoint you
configured (Ollama by default, which is also local).

If you use OpenAI, Groq, or another external provider, your
messages are sent to that provider. Read their privacy policy.

### What data does the backend phone home with?

None. There is no telemetry, no analytics, no update check. The
only outbound network calls are to the LLM endpoint, to Ollama's
registry on an explicit model pull, and to
`windows.php.net` / `dl.static-php.dev` / `sqlite.org` during the
bootstrap.


---

## Errors

### The backend crashes on startup. What do I do?

Read the last few lines of the terminal. Nine times out of ten
the error message names the exact problem. If the log is long:

```bash
curl http://127.0.0.1:8000/api/logs/recent?lines=500
```

(The backend will not have started, so this will fail. Read the
log file instead: `logs/backend-YYYY-MM-DD.log`.)

See `ERROR_HANDLING.md` §11 for startup errors.

### The MCP handshake fails on Windows.

Launch the backend via `scripts/serve.py` or one of the launchers.
The failure is the Proactor event loop bug.

See `ERROR_HANDLING.md` §5.2.

### `/graph` returns 404 or the editor is blank.

The LiteGraph vendor files are missing. Download them:

```bash
python scripts/download_vendor.py --force
```

The graph editor falls back to loading LiteGraph from jsDelivr
when the local files are missing, but a corporate proxy or
firewall can block the CDN too.

### `/php/` returns 500.

Check `GET /api/php/status`. If PHP is missing, run the PHP-only
bootstrap:

```bash
python bootstrap/setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor
```

On Windows, an "Access is denied" error means the PHP binary is
being blocked — run `fix_php.bat`.

See `ERROR_HANDLING.md` §7.

### The agent never calls tools.

Three possible causes:

1. The model does not support function calling. Small
   fine-tunes often do not. Use a tool-capable model
   (`llama3.2`, `gpt-4o-mini`, `qwen2.5`).
2. The model is in `AGENT_NO_TOOLS_MODELS` or was auto-cached
   after a previous 400 error.
3. `AGENT_DISABLE_TOOLS=true` in `.env`, or the No-tools toggle
   is on in the chat header.

See `ERROR_HANDLING.md` §3.

### Sessions are lost after a restart.

Check that the backend can write to `sessions.db`. A common
cause is a permissions issue or a read-only filesystem. The
log will show `Session store init failed`.

### The backend logs thousands of "GET /api/health 200 OK" lines.

This is uvicorn's access log. It is off by default.

Check `.env` for:

```env
AGENT_UVICORN_ACCESS_LOG=0
```

Restart the backend.

### The desktop client cannot find the backend.

The launcher probes `http://127.0.0.1:8000/api/health`. If the
backend is on a different port, set:

```bash
AGENT_PORT=9000 python -m desktop.app
```

If the backend is on a different machine:

```bash
AGENT_HOST=192.168.1.42 python -m desktop.app
```


---

## Development

### How do I add a custom tool?

Create a `.py` file in `mcp_server/tools/` with:

```python
from .._server import mcp

@mcp.tool()
def my_tool(x: int) -> int:
    """One-line description the LLM reads."""
    return x * 2
```

Restart the backend. The tool appears in the agent's tool list on
the next turn.

Full guide: `mcp_server/tools/README.md`.

### How do I add a custom graph node?

Create a `.py` file in `backend/graph/custom_nodes/` with:

```python
from ..model import NodeType, Param, Port
from ..registry import register

async def _my_node(ctx, inputs, params):
    return {"out": inputs.get("value", "")}

register(NodeType(
    name="custom.my_node",
    display="My Node",
    category="Custom",
    fn=_my_node,
))
```

Restart the backend. The node appears in the graph palette.

Full guide: `backend/graph/custom_nodes/README.md`.

### How do I run the test suite?

```bash
pytest tests/ -v
```

45+ tests, all passing, in under a second. Dependencies:
`pytest`, `pytest-asyncio`.

### How do I run the backend with auto-reload?

```bash
AGENT_RELOAD=1 python scripts/serve.py
```

Or:

```bash
./start.sh --reload
```

### How do I disable custom node loading when a custom node is broken?

```env
AGENT_LOAD_CUSTOM_NODES=false
```

Restart the backend. Fix the custom node, then set it back to
`true`.

### How do I run the MCP server standalone?

```bash
python -m mcp_server.server
```

It will sit silent until it receives a JSON-RPC frame on stdin.
Useful for debugging.

### How do I see what the LLM actually received?

Set `AGENT_LOG_FILE_LEVEL=DEBUG` in `.env` (this is the default)
and look at `logs/backend-*.log`. Every LLM request is logged
with its model, message count, and tool count:

```
DEBUG | LLM request (auto, batch): model=llama3.2 messages=3 tools=9
```


---

## Desktop client

### Do I need PyQt6 to use the app?

No. The browser UI works on any machine that can reach the
backend. PyQt6 is only required for the desktop client.

To skip the PyQt6 install (saves ~150 MB):

1. Comment out the two PyQt6 lines in `requirements.txt`.
2. Re-run the bootstrap.

Or install them separately after the fact:

```bash
runtime/python/python.exe -m pip install PyQt6 PyQt6-WebEngine
```

### Which desktop client should I use?

| Client | When |
|---|---|
| `desktop.app` (embedded Chromium) | Normal use — full web UI in a native window |
| `desktop.native_chat` (native widgets) | Minimal Linux, CI, or debugging SSE streams |

### Can I connect to a remote backend?

Yes. Set `AGENT_HOST` and `AGENT_PORT` (or
`AGENT_DESKTOP_BACKEND` for the native client):

```bash
AGENT_HOST=192.168.1.42 python -m desktop.app
```

The launcher detects the remote backend via `/api/health` and
attaches to it instead of spawning a local subprocess.

### What is the Stop All shortcut?

**Ctrl+Shift+Q**. It sends `POST /api/system/stop-all` and
force-kills the tree if the graceful shutdown does not complete
in 1.2 seconds.


---

## Configuration

### Do I need a `.env` file?

No. Every setting has a working default. The backend boots
against a local Ollama instance with the model `llama3.2`.

To customise anything, copy `.env.example` to `.env` and edit.

### Where do I change the port?

`AGENT_PORT` in `.env`:

```env
AGENT_PORT=9000
```

Or override at launch:

```bash
./start.sh --port 9000
```

### Can I change the default system prompt?

Yes. Edit the `SYSTEM_PROMPT` constant at the top of
`backend/agent.py`. Restart the backend. The prompt is prepended
on every turn and is never persisted to the session history, so
the change takes effect immediately — no migration needed.

### Can I add my own environment variables?

Yes, but they must start with `AGENT_`. Anything else is ignored
by pydantic-settings. To expose a new setting:

1. Add a field to `Settings` in `backend/config.py`.
2. Add it to `.env.example` with a comment.
3. Reference `settings.your_field` where you need it.

See `CONTRIBUTING.md` for the full checklist.

### Can I have different settings for different profiles?

Not built-in. The application reads a single `.env` at the
project root. For multiple configurations, swap `.env` files
before starting the backend, or run from different project
copies.


---

## Data & backups

### What should I back up?

| Path | Contents |
|---|---|
| `sessions.db` | Every chat session and graph run |
| `php_app/data/app.db` | Every note in the PHP app |
| `models/*.gguf` | Downloaded model weights (re-downloadable) |
| `.env` | Your configuration |

Everything else can be regenerated.

### How do I back up `sessions.db` safely?

A raw `cp` is unsafe under WAL mode. Use the SQLite CLI's
`.backup` command:

```bash
runtime/sqlite/sqlite3 sessions.db ".backup sessions-backup.db"
```

The result is a self-contained snapshot that does not depend on
the WAL sidecars.

### How do I reset the app completely?

1. Stop the backend.
2. Delete the data files:
   ```bash
   rm sessions.db
   rm php_app/data/app.db*
   ```
3. (Optional) Delete the runtime:
   ```bash
   rm -rf runtime/
   ```
4. Restart via `start.sh` / `start.bat`. The runtime is
   re-downloaded, and the databases are recreated empty.

### Can I move the project to another machine?

Yes, within the same OS family. Copy the whole folder except
`runtime/`, then re-run the bootstrap on the target machine.

If you copy `runtime/` too, make sure the machine uses the same
architecture (x86_64 → x86_64, arm64 → arm64).

### Can I migrate from SQLite to PostgreSQL for the PHP app?

Yes. Set `AGENT_PHP_DB_DRIVER=pgsql` and the connection details in
`.env`. The `notes` table is created in the new database on the
first request. Data does not migrate automatically — export from
SQLite and import into PostgreSQL manually if the notes matter.

See `php_app.html` in the knowledge base for the full
cross-driver matrix.


---

## Miscellaneous

### Is there an API?

Yes. FastAPI serves an OpenAPI schema at `/docs` (Swagger UI)
and `/openapi.json`. Every route is documented with request
and response shapes.

### Can I use it from Python?

Yes. The API is plain HTTP. Use `httpx` or `requests`:

```python
import httpx

r = httpx.post(
    "http://127.0.0.1:8000/api/chat",
    json={"message": "Hello", "history": []},
)
for event in r.json():
    print(event)
```

The non-streaming `/api/chat` endpoint collects every event into
an array for exactly this use case.

### Can I use it as a Slack bot?

Not out of the box. The backend exposes a WebSocket and an HTTP
API; a Slack bridge would be a small Python service that
translates between Slack's API and this one. See
`FUTURE_EXTENSIONS.md` for the roadmap entry.

### Is there a mobile app?

No, but the web UI is responsive and works on a phone's browser.
Install-to-homescreen (PWA) is on the roadmap.

### Why "3olution Agent Graph"?

The name comes from the visual graph editor, which is the most
distinctive part of the framework. Unlike a chat-only assistant,
this tool lets you build and visualize multi-step agent
workflows as DAGs. The "3olution" part is the vendor name.

### Who maintains it?

[3olution](https://3olution.com). MIT licensed. Contributions
welcome — see `CONTRIBUTING.md`.

### Where do I report a bug?

[github.com/3olution/agent-graph/issues](https://github.com/3olution/agent-graph/issues).
Include your OS, Python version, and the last 200 lines of
`logs/backend-YYYY-MM-DD.log`.

For security issues, email `security@3olution.com` instead of
opening a public issue.

### How do I get help?

1. Read the relevant `.md` file at the project root. This
   knowledge base covers most questions.
2. Check the backend's `Extensive_Documentation.md` files for
   deep coverage of a specific module.
3. Read `ERROR_HANDLING.md` for the specific failure.
4. Search the GitHub issues.
5. Open a new issue if nothing matches.

### Where do I go next?

| If you want to… | Read |
|---|---|
| Learn the interface | `HOW_TO_USE.md` |
| Understand the launch options | `STARTUP.md` |
| Recover from a failure | `ERROR_HANDLING.md` |
| Install from scratch | `INSTALLATION.md` |
| Contribute a patch | `CONTRIBUTING.md` |
| See what changed | `CHANGES.md` |
| Plan an extension | `FUTURE_EXTENSIONS.md` |
| Understand a specific folder | The corresponding HTML page in the knowledge base |