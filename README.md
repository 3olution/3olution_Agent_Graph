
<div align="center">

# 3olution Agent Graph

**A dynamic operating graph for AI agents — build, visualize, and execute agent workflows as node graphs.**  
*Like ComfyUI, but for any LLM + tool stack.*

Built by [3olution](https://3olution.com) | MIT Licensed

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI 0.118+](https://img.shields.io/badge/FastAPI-0.118%2B-009688.svg)](https://fastapi.tiangolo.com)
[![MCP 2.x](https://img.shields.io/badge/MCP-2.x-purple.svg)](https://modelcontextprotocol.io)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)]()

</div>

> [!WARNING]
> **SECURITY AND ENVIRONMENT NOTICE**
> 
> This framework is designed to execute arbitrary code, manage local file systems, and interact with external LLM endpoints. **It must strictly be deployed and operated within an isolated sandbox or secure environment.**
> 
> **3olution** assumes no liability or responsibility for any data loss, system compromise, or damage that may occur if this software is executed outside of a properly secured and isolated environment. Proceed with caution and ensure appropriate security measures are in place prior to execution.

---

## Table of Contents

- [What is this?](#what-is-this)
- [Feature Matrix](#feature-matrix)
- [Quick Start](#quick-start)
- [Installation Workflows](#two-installation-workflows)
- [Architecture](#architecture)
- [LLM Configuration](#llm-configuration)
- [Built-in Node Types](#built-in-node-types)
- [Built-in MCP Tools](#built-in-mcp-tools)
- [Web Interfaces](#the-three-web-interfaces)
- [Desktop Client](#desktop-client)
- [PHP Notes App](#php-notes-app)
- [Portable Runtime](#portable-runtime)
- [API Reference](#api-reference)
- [Event Schema](#event-schema)
- [Configuration Reference](#configuration-reference)
- [Cross-Platform Notes](#cross-platform-notes)
- [Testing](#testing)
- [Packaging](#packaging)
- [Troubleshooting](#troubleshooting)
- [Extending](#extending)
- [Future Extensions](#future-extensions)
- [Documentation Index](#documentation-index)
- [Contributing](#contributing)
- [License & Branding](#license--branding)

---

## What is this?

**3olution Agent Graph** is a full-stack AI agent framework that consolidates four distinct operational layers into a single cohesive project:

1. **FastAPI Backend:** Executes the agent loop, routes LLM requests across multiple providers, and processes visual workflows.
2. **MCP Tool Server:** Provides deterministic capabilities (math, filesystem, time, text) via the [Model Context Protocol](https://modelcontextprotocol.io/).
3. **LiteGraph.js Visual Editor:** Enables the construction of agent workflows as node graphs—drag nodes, connect ports, execute, and stream results live.
4. **Portable Runtime:** A self-contained environment ensuring execution on any machine with a Python 3.8+ interpreter, requiring no system-wide Python, PHP, or SQLite installations.

Additionally, it includes a PyQt6 desktop client, a server-rendered PHP notes application, a SQLite session store, and a comprehensive test suite. The graph editor utilizes the original [`jagenjo/litegraph.js`](https://github.com/jagenjo/litegraph.js) and is entirely independent of ComfyUI.

---

## Feature Matrix

| Layer | Functionality | Directory |
| :--- | :--- | :--- |
| **Python Backend** | Agent loop, multi-provider LLM client, MCP client, graph engine, SQLite persistence | `backend/` |
| **MCP Tool Server** | Deterministic tools over stdio: math, filesystem, time, text | `mcp_server/` |
| **Web Chat UI** | Browser chat with streaming tokens, thinking blocks, and tool events | `web/index.html` |
| **Web Graph Editor** | LiteGraph.js node editor featuring run history and result bar | `web/graph.html` |
| **Web Settings** | Manage `llama-cpp-python`, Ollama models, and system configurations | `web/settings.html` |
| **PyQt6 Desktop** | Native desktop client with embedded Chromium or native widgets | `desktop/` |
| **PHP Notes App** | Server-rendered CRUD demo served through PHP-CGI | `php_app/` |
| **Portable Runtime** | Python 3.12, PHP 8.4 (CGI + CLI), SQLite 3.46 in a single folder | `bootstrap/`, `runtime/` |
| **REST + WebSocket API** | Drive the agent from any language or client | `backend/main.py` |
| **SQLite Session Store**| Chat history and graph run archive | `backend/storage.py` |
| **Test Suite** | 45+ `pytest` tests covering pure-logic components | `tests/` |

---

## Quick Start

### Prerequisites
- **Python 3.8+** on `PATH` (required for the bootstrap installer).
- An **OpenAI-compatible LLM endpoint** (e.g., [Ollama](https://ollama.com), which is the default local provider).

### One-Command Setup

```bash
git clone https://github.com/3olution/agent-graph.git
cd agent-graph

# Portable runtime (Recommended for first-time users)
python bootstrap/setup.py

# OR the developer workflow (Creates a standard .venv/)
python run.py setup
```
*Note: The portable bootstrap downloads a self-contained Python 3.12, PHP 8.4, and SQLite 3.46 into `runtime/`. Nothing is installed system-wide.*

### Start the Backend

```bash
# Portable launcher (uses runtime/python)
./start.sh           # Linux / macOS
start.bat            # Windows

# OR the developer launcher
python run.py backend
```
*The backend listens on `http://127.0.0.1:8000` by default.*

### Access the Interfaces

| URL | Interface |
| :--- | :--- |
| `http://127.0.0.1:8000/` | Chat |
| `http://127.0.0.1:8000/graph` | Graph Editor |
| `http://127.0.0.1:8000/settings` | Settings |
| `http://127.0.0.1:8000/php/` | PHP Notes App |
| `http://127.0.0.1:8000/docs` | Auto-generated API Browser |

### Start the Desktop Client

```bash
python run.py desktop    # Embedded Chromium (Recommended)
python run.py native     # Native widgets only
```

### Verify the Installation

```bash
curl http://127.0.0.1:8000/api/health
```
*Expected: A JSON object with `"status": "ok"` and subsystem fields for LLM, MCP, llama-cpp, PHP, and storage.*

```bash
pytest tests/ -v
```
*Expected: 45 tests, all passing, in under a second.*

---

## Two Installation Workflows

The project provides two independent launcher paths. Both execute the same backend code; they differ only in the Python interpreter utilized.

### Portable (End-User)
```bash
./setup.sh && ./start.sh        # Linux / macOS
setup.ps1 ; start.bat           # Windows
```
- **Pros:** Zero system dependencies, portable (USB compatible), identical across platforms, includes PHP and SQLite.
- **Cons:** 2–5 minute initial download (~250 MB), utilizes a private Python environment (system-level packages are unavailable).

### Developer
```bash
python run.py setup
python run.py backend
```
- **Pros:** Familiar virtualenv semantics, rapid iteration, utilizes system Python's `site-packages`.
- **Cons:** Requires Python 3.10+ on `PATH`, does not install PHP or the SQLite CLI.

| Scenario | Recommendation |
| :--- | :--- |
| First-time install, seamless setup | **Portable** |
| Development on Python source | **Developer** |
| Shared machine, no admin rights | **Portable** |
| Air-gapped environment (pre-cached) | **Portable** |
| CI runner with pinned Python | **Developer** |
| PHP notes app required out-of-the-box | **Portable** |

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│  Frontends                                                   │
│  ─────────                                                   │
│  Browser UI          PyQt6 Desktop Client                    │
│  / (chat)            desktop.app (embedded Chromium)         │
│  /graph              desktop.native_chat (QtWidgets only)    │
│  /settings                                                   │
│  /php/                                                       │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP + WebSocket
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  Backend (FastAPI + uvicorn)                                 │
│  ────────────────────────────────                            │
│  /api/chat           /api/chat/stream   /ws                  │
│  /api/graph/*        /ws/graph                               │
│  /api/ollama/*       /api/llama/*                            │
│  /api/system/*       /api/logs/*                             │
│  /php/*, /php-static/*                                       │
└────┬──────────────┬──────────────┬──────────────┬────────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
┌─────────┐  ┌────────────┐  ┌──────────┐  ┌──────────────┐
│ Ollama  │  │ llama-cpp  │  │ MCP      │  │ PHP-CGI      │
│ primary │  │ subprocess │  │ stdio    │  │ subprocess   │
│ :11434  │  │ :8080      │  │ tools    │  │ php_app/     │
└─────────┘  └────────────┘  └──────────┘  └──────────────┘
     │              │              │              │
     └──────────────┴──────┬───────┴──────────────┘
                           ▼
                  ┌────────────────┐
                  │  SQLite        │
                  │  sessions.db   │
                  └────────────────┘
```

---

## LLM Configuration

The framework routes requests to one of three endpoints, selected per request:

| Provider | Purpose |
| :--- | :--- |
| **primary** | The main endpoint (Ollama by default). |
| **fallback** | An optional secondary endpoint, attempted if the primary exhausts retries. |
| **llama_cpp** | The locally-managed `llama-server` subprocess. |

### Default Configuration
The backend boots against a local Ollama instance (`llama3.2`) requiring zero configuration.

| Setting | Default |
| :--- | :--- |
| `AGENT_LLM_BASE_URL` | `http://localhost:11434/v1` |
| `AGENT_LLM_API_KEY` | `ollama` |
| `AGENT_LLM_MODEL` | `llama3.2` |

### Provider Examples

| Provider | `AGENT_LLM_BASE_URL` | `AGENT_LLM_API_KEY` | `AGENT_LLM_MODEL` |
| :--- | :--- | :--- | :--- |
| Ollama | `http://localhost:11434/v1` | `ollama` | `llama3.2` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` | `gpt-4o-mini` |
| LM Studio | `http://localhost:1234/v1` | `lm-studio` | *loaded model* |
| Groq | `https://api.groq.com/openai/v1`| `gsk_...` | `llama-3.3-70b-versatile` |
| vLLM | `http://localhost:8001/v1` | `vllm` | *model name* |

### Fallback Endpoint
Enable automatic failover by configuring the fallback variables:

```env
AGENT_LLM_FALLBACK_BASE_URL=https://api.your-company.com/v1
AGENT_LLM_FALLBACK_API_KEY=your-key
AGENT_LLM_FALLBACK_MODEL=your-model
```

### Model Routing and Reasoning
- **Per-request selection:** The UI allows picking from a unified list of Ollama models and local GGUF files, encoded as `provider::model`.
- **Automatic behaviors:** Retries with exponential backoff, automatic fallback switching, tool parameter stripping for incompatible models, and parsing of `<thinking>` blocks for reasoning models.

---

## Built-in Node Types

The graph editor includes 16 node types across seven categories:

| Category | Nodes |
| :--- | :--- |
| **Input** | `input.text`, `input.number`, `input.json` |
| **Output** | `output.text` |
| **LLM** | `llm.prompt`, `llm.chat` |
| **Tools** | `tool.list`, `tool.call` |
| **Text** | `text.template`, `text.join` |
| **Control** | `control.branch`, `control.merge`, `control.foreach` |
| **Utility** | `util.constant`, `util.log`, `util.delay` |

### Adding a Custom Node
Drop a `.py` file in `backend/graph/custom_nodes/`:

```python
from ..model import NodeType, Param, Port
from ..registry import register

async def _my_node(ctx, inputs, params):
    value = inputs.get("value") or params.get("value", "")
    return {"out": str(value).upper()}

register(NodeType(
    name="custom.upper",
    display="Uppercase",
    category="Custom",
    inputs=[Port("value", "any", optional=True)],
    outputs=[Port("out", "any")],
    params=[Param("value", "text", "")],
    fn=_my_node,
))
```

---

## Built-in MCP Tools

The MCP server exposes nine tools. The SDK generates the JSON schema from signatures, type hints, and docstrings.

| Tool | Signature | Description |
| :--- | :--- | :--- |
| `add` | `(a: float, b: float) → float` | Exact IEEE 754 addition |
| `multiply` | `(a: float, b: float) → float` | Exact IEEE 754 multiplication |
| `sqrt` | `(x: float) → float` | Square root (rejects negatives) |
| `power` | `(base: float, exponent: float) → float`| Raise to power |
| `current_time` | `() → str` | Current UTC time, ISO 8601 |
| `list_directory`| `(path: str = ".") → str` | List directory entries |
| `read_file` | `(path: str, max_bytes: int = 4096) → str`| Read a text file (UTF-8) |
| `word_count` | `(text: str) → dict` | Count words, chars, unique words |
| `shout` | `(text: str) → str` | Uppercase text |

### Adding a Tool
Drop a `.py` file in `mcp_server/tools/`:

```python
from .._server import mcp

@mcp.tool()
def km_to_miles(km: float) -> float:
    """Convert kilometers to miles (1 km ≈ 0.621371 mi)."""
    return km * 0.621371
```

---

## The Three Web Interfaces

### Chat Page (`/`)
- WebSocket streaming with token-by-token display.
- Collapsible thinking panel for reasoning models.
- Tool call/result bubbles, retry/fallback notices.
- Unified model picker, session persistence, and Markdown export.

### Graph Editor (`/graph`)
- LiteGraph.js canvas with full node palette.
- Live execution streaming with per-node status indicators.
- Run history panel with replay, auto-save to `localStorage`.

### Settings Page (`/settings`)
- **LLM Runtime:** Install/uninstall `llama-cpp-python` with CUDA detection.
- **Ollama Connection:** Status, host, version, and model management (pull/delete).

---

## Desktop Client

Two PyQt6 frontends communicate via the same HTTP/WebSocket API.

### `desktop.app` (Embedded Chromium)
```bash
python run.py desktop
```
**Shortcuts:** `Ctrl+1/2/3` (Chat/Graph/Settings), `Ctrl+L` (Logs), `Ctrl+Q` (Quit), `F5` (Reload).

### `desktop.native_chat` (QtWidgets Only)
```bash
python run.py native
```
A pure-QtWidgets client for headless CI or minimal environments, surfacing all events as text.

---

## PHP Notes App

A server-rendered PHP application served at `/php/` via PHP's CGI SAPI. The backend constructs the CGI environment and spawns `php-cgi` directly (no Apache/nginx required).

**Driver-Agnostic:** Switch between SQLite, MySQL, or PostgreSQL via `.env`:

```env
AGENT_PHP_DB_DRIVER=mysql
AGENT_PHP_DB_HOST=127.0.0.1
AGENT_PHP_DB_PORT=3306
AGENT_PHP_DB_NAME=notes
AGENT_PHP_DB_USER=notes_user
AGENT_PHP_DB_PASSWORD=secret
```

---

## Portable Runtime

The `bootstrap/setup.py` script downloads a self-contained runtime into `runtime/`.

| Subfolder | Contents | Size |
| :--- | :--- | :--- |
| `runtime/python/` | Portable CPython 3.12.11 + stdlib + pip deps | ~120 MB + deps |
| `runtime/php/` | PHP 8.4 CLI + CGI + php.ini + ext/ | ~40 MB |
| `runtime/sqlite/` | SQLite CLI tools | ~3 MB |
| `runtime/llama-server/`| Optional native llama.cpp binary | ~10–50 MB |

**CLI Flags:** `--force`, `--skip-python`, `--skip-php`, `--skip-sqlite`, `--skip-deps`, `--check`, `--verbose`.

---

## API Reference

The OpenAPI schema is served at `/docs` and `/openapi.json`.

### Chat
```bash
# Non-streaming
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 6 times 7?","history":[]}'

# SSE streaming
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 6 times 7?","history":[]}'
```

### Graph
```bash
curl -X POST http://127.0.0.1:8000/api/graph/run \
  -H 'Content-Type: application/json' -d @graph.json
```

### Ollama Management
```bash
curl -X POST http://127.0.0.1:8000/api/ollama/models/pull \
  -H 'Content-Type: application/json' -d '{"name":"llama3.2"}'
```

---

## Event Schema

Events utilize a unified schema across WebSocket, SSE, and batched JSON.

### Agent Events
| Type | Fields | Meaning |
| :--- | :--- | :--- |
| `token` | `content` | Streamed token of the final answer |
| `thinking` | `content` | Streamed reasoning token |
| `tool_call` | `name`, `args` | Agent invoking a tool |
| `tool_result`| `name`, `result` | Tool returned a value |
| `final` | `content`, `messages`| Turn complete |

### Graph Events
| Type | Fields | Meaning |
| :--- | :--- | :--- |
| `graph_start`| `order` | Execution order computed |
| `node_done` | `node`, `outputs` | Node completed |
| `graph_done` | `outputs` | Graph complete |

**Example Turn:**
```json
{"type": "thinking", "content": "Let me compute that..."}
{"type": "tool_call", "name": "multiply", "args": "{\"a\": 6, \"b\": 7}"}
{"type": "tool_result", "name": "multiply", "result": "42"}
{"type": "token", "content": "6 times 7 is 42."}
{"type": "final", "content": "6 times 7 is 42.", "messages": [...]}
```

---

## Configuration Reference

All settings reside in `.env` and are prefixed with `AGENT_`.

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `AGENT_HOST` | `127.0.0.1` | Bind address |
| `AGENT_PORT` | `8000` | Bind port |
| `AGENT_LLM_BASE_URL` | `http://localhost:11434/v1` | Primary LLM endpoint |
| `AGENT_LLM_MODEL` | `llama3.2` | Default model |
| `AGENT_LLM_TEMPERATURE`| `0.0` | Sampling temperature |
| `AGENT_DISABLE_TOOLS` | `false` | Global tool kill switch |
| `AGENT_MAX_ITERATIONS` | `10` | Max tool-call iterations per turn |
| `AGENT_STREAM_STALL_TIMEOUT`| `360.0` | Abort stalled turn after N seconds |

---

## Cross-Platform Notes

### Windows 10 / 11
- **PowerShell:** May require `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.
- **Event Loop:** `WindowsSelectorEventLoopPolicy` is enforced automatically to prevent MCP handshake failures.
- **Antivirus:** Add `.venv/` and `runtime/php/` to Windows Defender exclusions if startup hangs occur.

### Linux
- **PyQt6 Dependencies:** Minimal distributions require `libgl1`, `libegl1`, `libxkbcommon0`, etc.
- **Wayland:** Export `QT_QPA_PLATFORM=wayland`.

### macOS
- **Apple Silicon:** All dependencies possess `arm64` wheels.
- **Gatekeeper:** Clear quarantine attributes if PyQt6 is blocked: `xattr -dr com.apple.quarantine .venv/...`

---

## Testing

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```
The suite focuses on pure-logic components (Topological sort, Registry, LLM prompt builder, Control flow). Integration surfaces are deliberately excluded to avoid external service dependencies.

---

## Packaging

Build executable bundles via PyInstaller:

```bash
pip install pyinstaller
pyinstaller pyinstaller.spec
```
*Output:* `dist/3olutionAgentGraph/` (Single-folder distribution to accommodate PyQt6-WebEngine's Chromium footprint).

---

## Troubleshooting

| Symptom | Cause | Resolution |
| :--- | :--- | :--- |
| `ModuleNotFoundError: backend` | Wrong working directory | `cd` to project root, rerun. |
| `Address already in use` | Port 8000 occupied | Change `AGENT_PORT` or kill the process. |
| Web UI shows disconnected | Backend not running | Start the backend. |
| MCP subprocess hangs | Windows Proactor loop | Launch via `scripts/serve.py`. |
| `PyQt6 xcb error` on Linux | Missing system libs | Install required `apt`/`dnf` packages. |
| Agent never calls tools | Model lacks function-calling | Use `gpt-4o-mini`, `llama3.2`, or `qwen2.5`. |
| `sessions.db locked` | SQLite one-writer limit | Stop other backend instances. |

---

## Extending

1. **Add a tool:** Drop a file in `mcp_server/tools/*.py`.
2. **Add a graph node:** Drop a file in `backend/graph/custom_nodes/*.py`.
3. **Add a web page:** Add HTML/CSS/JS to `web/` and register a route in `backend/main.py`.
4. **Add a test:** Drop a `test_*.py` file in `tests/`.

---

## Future Extensions

- **Vector Memory:** Embed past conversations for top-k retrieval.
- **Sandboxed Code Execution:** Docker-backed `run_python` MCP tool.
- **Multi-MCP-server:** Route tools across multiple MCP subprocesses.
- **Auth + Multi-user:** Bearer tokens, per-user sessions, rate limits.
- **Subgraph Nodes:** Nodes containing nested graphs.

---

## Documentation Index

### Root-Level Docs
| File | Coverage |
| :--- | :--- |
| `README.md` | This file |
| `INSTALLATION.md` | Step-by-step install paths and troubleshooting |
| `HOW_TO_USE.md` | Interface tour (Chat, Graph, Settings, PHP, Desktop) |
| `ERROR_HANDLING.md`| Error taxonomy and recovery procedures |
| `FAQ.md` | ~70 common questions grouped by topic |
| `FUTURE_EXTENSIONS.md`| 50+ roadmap items |

### Per-Folder Docs
Each subfolder contains an `Extensive_Documentation.md` providing deep dives into that specific layer (e.g., `backend/`, `mcp_server/`, `desktop/`, `php_app/`).

---

## Contributing

Pull requests are welcome. All changes must:
1. Pass `pytest tests/ -v`.
2. Include a `CHANGES.md` entry under `[Unreleased]`.
3. Follow the existing code style (see `CONTRIBUTING.md`).
4. Update documentation if behavior changes.

**Reporting Issues:** Open an issue at [github.com/3olution/agent-graph/issues](https://github.com/3olution/agent-graph/issues).  
**Security Issues:** Do not open public issues. Email [security@3olution.com](mailto:security@3olution.com).

---

## Full Documentation and Help Files

https://3olution.github.io/3olution_Agent_Graph/index.html

---

## License & Branding

**MIT License.** See `LICENSE` for the full text.

Third-party components bundled or downloaded at setup time retain their respective licenses (PSF, MIT, PHP License 3.01, Public Domain).

*The 3olution name and logo are trademarks of 3olution. Using them in a fork or derivative work requires written permission.*

<div align="center">
  <strong> Built by 3olution * https://3olution.com </strong>
</div>


