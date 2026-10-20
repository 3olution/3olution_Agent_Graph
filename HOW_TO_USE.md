# How to Use — 3olution Agent Graph

A tour of every interface in the running application, with the
workflows a new user reaches for first.

The backend exposes four pages on the same port. All four are
reachable from any modern browser as soon as `start.sh` /
`start.bat` prints "Backend ready":

| URL | Page | Purpose |
|---|---|---|
| `http://127.0.0.1:8000/` | Chat | Converse with the agent |
| `http://127.0.0.1:8000/graph` | Graph editor | Build visual agent workflows |
| `http://127.0.0.1:8000/settings` | Settings | Install runtime, manage models |
| `http://127.0.0.1:8000/php/` | PHP notes app | Server-rendered CRUD demo |
| `http://127.0.0.1:8000/docs` | API browser | Auto-generated OpenAPI schema |

The desktop client (`python run.py desktop`) wraps the first three
in a native window using embedded Chromium. The native chat client
(`python run.py native`) is a reduced QtWidgets-only fallback.


---

## 1. The Chat Page

### Header controls, left to right

| Control | What it does |
|---|---|
| **3olution Agent Graph** | Brand. Links to `/` |
| **Graph Editor** | Navigate to `/graph` |
| **Settings** | Navigate to `/settings` |
| **Notes** | Navigate to `/php/` |
| **Model:** dropdown | Pick which model to talk to |
| **Load GGUF** | Load a local GGUF file into the llama-cpp server |
| **No tools** checkbox | Disable tool use for this session |
| **+ New** | Start a fresh conversation (new session ID) |
| **Save** | Download the entire conversation as Markdown |
| **⏹ Stop** | Stop the backend and all subprocesses |
| session label | Short prefix of the current session UUID |
| status pill | `connected` / `disconnected` / `stopping` |
| model busy pill | Which models are currently loaded |

### The model picker

The dropdown is populated from `GET /api/llama/all-models`, which
returns both Ollama models and local GGUF files. Entries are
grouped by provider:

```
Ollama (3)
    llama3.2  (4.7 GB)
    qwen2.5-coder:7b  (4.7 GB)
    deepseek-r1:1.5b  (1.1 GB)
Local GGUF (2)
    ✓ llama-3.2-3b.Q4_K_M.gguf  (2.01 GB, loaded)
    qwen2.5-coder-7b.Q4_K_M.gguf  (4.7 GB)
```

The ✓ prefix marks a GGUF file that is currently loaded into the
local server. Picking a non-loaded GGUF prompts to load it
immediately — that avoids the "model not running" error on the
first message.

The selected model is stored in `localStorage` under
`3olution-selected-model` and restored on the next page load.

### Sending a message

Type into the input at the bottom and press **Enter** (or click
**Send**). The agent runs one turn, streaming the response
token by token into a live bubble with a blinking cursor.

While a turn is running:

- The input, model picker, and most buttons are disabled.
- The status pill stays `connected` throughout.
- The transcript auto-scrolls as new content arrives.
- Pressing **Ctrl+C** in the terminal running the backend does
  not cancel the turn; use the browser's reload or wait for the
  turn to finish.

### Reading the response

Every event the agent emits has a distinct visual treatment in
the transcript:

| Event | Rendering |
|---|---|
| `token` | Live text in an agent bubble with a blinking cursor |
| `thinking` | Collapsible panel above the answer, marked 💭 |
| `retry` | Yellow centered notice, italic |
| `fallback` | Teal centered notice with a left border |
| `info` | Grey-blue centered notice (yellow if it mentions tools) |
| `tool_call` | Purple left-bordered bubble, `🔧 name(args)` |
| `tool_result` | Blue left-bordered bubble, `📊 result` |
| `final` | Freezes the streaming bubble, attaches action buttons |

**Thinking panel.** By default the panel is collapsed to a 60-pixel
window with a fade-out mask. Click **Show more** to expand it, or
click the 💭 header. Each new turn creates a fresh collapsed panel.

**Tool calls and results.** The bubbles appear in the transcript
in the order the agent emitted them. A multi-tool turn shows each
call and each result as a separate bubble. The user never needs to
see the machinery — the final answer is independent of how the
tools were chained.

### Action buttons

Every completed message exposes action buttons on hover:

| Button | Appears on | Action |
|---|---|---|
| **Copy** | All messages | Copy the text to the clipboard |
| **Save** | User + agent messages | Download the text as a `.md` file |
| **Edit** | User messages | Load the message back into the input box |
| **Repeat** | User messages | Re-send the same message as a new turn |

The Copy button uses `navigator.clipboard` when available and
falls back to a hidden textarea + `document.execCommand("copy")` in
non-secure contexts (e.g. plain HTTP on a LAN address).

### The stall timer

If no event arrives for **six minutes**, the UI assumes the turn is
stuck, resets the busy state, and shows:

> ⚠ No response for 6 minutes. Try a different model, or click Stop.

The backend has its own stall timeout
(`AGENT_STREAM_STALL_TIMEOUT`, default 360 s) which aborts the
turn server-side. The client timer is a safety net for the case
where the WebSocket itself hangs.

### Session management

The chat client stores three keys in `localStorage`:

| Key | Value |
|---|---|
| `3olution-session-id` | The current session's UUID |
| `3olution-selected-model` | The last model the user picked |
| `3olution-disable-tools` | `"1"` if the No-tools toggle is on |

On page load, if a session ID is present, the client calls
`GET /api/sessions/{id}` to restore the history. If the session
no longer exists (the SQLite file was cleared), a fresh session is
created automatically.

Clicking **+ New** calls `POST /api/sessions` to get a new UUID
and clears the transcript. The old session remains in
`sessions.db` and can be recovered via the API.

### Saving the conversation

The header's **Save** button assembles every message in the
current session into a single Markdown file:

```markdown
# 3olution Agent Graph — Conversation

Session: 8b4a-...
Saved: 2025-01-15T12:34:56.789Z

---

## 🧑 You
What is 12 times 8?

## 🤖 Agent
12 times 8 is 96.
```

The file downloads with a timestamped name such as
`3olution-chat-2025-01-15T12-34-56.md`.

### Stopping everything

The red **⏹ Stop** button calls `POST /api/system/stop-all` after a
confirmation prompt. The backend returns immediately with
`{"status": "stopping"}`, then terminates the llama-cpp server,
closes the MCP session, and exits.

The WebSocket disconnects a moment later — that is expected. To
restart, run `start.bat` (or `./start.sh`) again from a terminal.


---

## 2. The Graph Editor

The graph editor is a visual workflow builder based on
[LiteGraph.js](https://github.com/jagenjo/litegraph.js). Nodes
appear in the canvas, connections are drawn between their ports,
and clicking **Run** executes the graph on the backend.

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Header: brand · Chat · Settings · Run · Save · Load · Clear│
├──────────────────────────────────┬──────────────────────────┤
│                                  │                          │
│                                  │   Current Run            │
│                                  │   (live event log)       │
│      LiteGraph canvas            │                          │
│                                  ├──────────────────────────┤
│                                  │   History                │
│                                  │   (past runs list)       │
│                                  │                          │
└──────────────────────────────────┴──────────────────────────┘
│  Result bar (appears after a run)                           │
└─────────────────────────────────────────────────────────────┘
```

### Canvas basics

| Action | How |
|---|---|
| Add a node | Right-click on the canvas → pick a category → pick a node |
| Or | Double-click the canvas to open the search box, type a name |
| Move a node | Left-click and drag the node body |
| Connect two nodes | Click an output port, drag to an input port |
| Disconnect | Click a link and press Delete, or drag one end away |
| Pan the canvas | Middle-click drag, or spacebar drag |
| Zoom | Mouse wheel |
| Select multiple | Left-click drag a rectangle, or Ctrl+click |
| Delete | Select and press Delete |
| Collapse a node | Click the node's title bar |

Every node has a **status dot** in its top-right corner that
updates live during a run:

| Colour | Meaning |
|---|---|
| Grey | idle — has not run yet |
| Light grey | pending — queued |
| Yellow | running |
| Green | done |
| Red | error |

### Node palette

The palette is grouped by category. Sixteen node types ship with
the editor, plus seven custom examples:

**Input** — `input.text`, `input.number`, `input.json`
**Output** — `output.text`
**LLM** — `llm.prompt`, `llm.chat`
**Tools** — `tool.list`, `tool.call`
**Text** — `text.template`, `text.join`
**Control** — `control.branch`, `control.merge`, `control.foreach`
**Utility** — `util.constant`, `util.log`, `util.delay`
**Custom** — `custom.uppercase`, `custom.reverse`, `custom.random`,
`custom.json_extract`, `custom.llm_json`, `custom.sleep`,
`custom.random_string`

Each node shows its widgets (text fields, dropdowns, toggles) in
its body. The `llm.chat` node has a dynamic **model** dropdown
that is populated from the unified model list at page load. Its
value is encoded as `provider::model` — see the chat page's model
picker for the same encoding.

### Running a graph

1. Build or load a graph on the canvas.
2. Click **▶ Run** in the header.
3. The status pill changes to `running`.
4. Nodes light up in topological order.
5. The **Current Run** panel streams events:

```
▶ Graph start (5 nodes)
✔ System prompt
   text: You are a concise assistant.
✔ User prompt
   text: What is 12 times 8?
✔ Prompt Builder
   messages: [{"role": "system", ...}, {"role": "user", ...}]
✔ LLM Chat
   response: 96
✔ Result
   result: 96
── graph complete ──
▶ Result: 96
💾 run saved: 8b4a-...
```

6. The result bar appears at the bottom with the output text.
7. The run is saved to SQLite and appears in the History panel.

### The result bar

The result bar shows the text from the first `output.text` node.
It has two buttons:

- **Copy** — copy the result to the clipboard
- **✕** — hide the bar

If multiple `output.text` nodes exist, all their results are
concatenated into the bar, each prefixed with its node title.

### History and replay

Every completed run is saved to the `graph_runs` table. The
**History** panel lists the most recent 50, newest first, showing:

- Graph name
- Status dot (green/red)
- Node count and event count
- Relative time ("2m ago", "3d ago")
- A truncated preview of the final text

Clicking a history entry calls
`GET /api/graph/runs/{run_id}` and replays the full event stream
into the Current Run panel, additionally restoring the node
statuses and painting the final result in the result bar.

To delete a single run, click the **×** button on its history card.
To clear the entire history, click **Clear all** at the top of the
panel.

### Saving and loading graphs

- **Save** — download the current canvas as `3olution-graph.json`
- **Load** — open a file picker and feed the JSON back through the
  adapter
- **Clear** — empty the canvas (with confirmation)

The current graph is also saved to `localStorage` under
`3olution-graph-seed-v1` on every edit, so a page reload restores
exactly what you were working on.

### Stopping everything

The red **⏹ Stop All** button behaves like the chat page's Stop
button: confirm, then `POST /api/system/stop-all`, then wait for
the WebSocket to drop.


---

## 3. The Settings Page

The settings page has four cards.

### 3.1 LLM Runtime

Manages the `llama-cpp-python` package used to run local GGUF
files. Displays:

- **Install state** — installed / not installed, with the version
- **GPU offload** — whether the current build supports GPU
- **Python interpreter path** — which Python is being used
- **Virtualenv state** — whether the backend is inside a venv
- **Detected CUDA version** — from `nvidia-smi`
- **Recommended wheel** — auto-selected based on CUDA detection

Clicking **Show install options** reveals the wheel dropdown and
the install/uninstall buttons:

| Wheel | GPU class |
|---|---|
| `cpu` | CPU only |
| `cu124` | CUDA 12.4 (RTX 30/40, Ada/Hopper) |
| `cu121` | CUDA 12.1 (older 12.x drivers) |
| `cu118` | CUDA 11.8 (GTX 10/16, RTX 20) |

Clicking **Install** streams pip's output line by line into a
live log panel. The final `success` event reports the installed
version and whether GPU offload is supported.

> **Windows caveat.** The upgrade checkbox carries an inline
> warning: llama-cpp-python's native DLLs are locked while the
> package is loaded in a running process. Upgrading requires the
> backend to be stopped first.

### 3.2 Ollama Connection

Displays:

- **Status dot** — green (connected) or red (unreachable)
- **Host** — from `AGENT_OLLAMA_HOST`
- **Version** — from `GET /api/version` on the Ollama server

A **↻ Refresh** button at the top of the page re-checks this card
along with the models table.

### 3.3 Installed Models

The Ollama model table shows every installed model with:

- Name (monospaced)
- Size in GB
- Family (llama, qwen, etc.)
- Parameter count
- Quantization level
- Relative modification date
- A red **Delete** button

Deleting a model sends `DELETE /api/ollama/models/{name}` and
refreshes the table.

### 3.4 Pull a Model

Enter a model name from
[ollama.com/library](https://ollama.com/library) — for example
`llama3.2`, `mistral:7b`, or `qwen2.5-coder` — and click **Pull**.

The download streams progress as SSE frames. The UI renders two
indicators:

- A blue-purple progress bar with a percentage fill
- A status line such as `downloading  45.2 / 214.8 MB (21.0%)`

Ollama's `total` field is only present on the `downloading` event.
Before and after that, the status line shows the raw text
(`pulling manifest`, `verifying sha256 digest`, `success`).


---

## 4. The PHP Notes App

Reached at `/php/`. A server-rendered notes application built
with PHP's CGI SAPI, hosted by the Python backend.

### What you can do

- **List notes** — the page shows all notes newest first
- **Add a note** — fill in title (max 200 chars) and body (max
  10,000 chars), click **Save note**
- **Delete a note** — click the × button on any note; a
  confirmation prompt appears first

### Header

The header shows:

- The 3olution Notes logo and title
- **PHP version** — the runtime the notes app is using
- **DB driver** — `sqlite`, `mysql`, or `pgsql`
- The current server time

### Endpoints card

The bottom of the page lists the API endpoints the notes app
exposes:

```
GET  /php/              — this page
GET  /php/demo.php      — SQL demo (JSON)
GET  /api/php/status    — PHP binary info
GET  /api/php/drivers   — PDO drivers installed
```

### Driver-agnostic design

The same code runs against SQLite (default), MySQL, or
PostgreSQL. Switching is a `.env` change:

```env
AGENT_PHP_DB_DRIVER=mysql
AGENT_PHP_DB_HOST=127.0.0.1
AGENT_PHP_DB_PORT=3306
AGENT_PHP_DB_NAME=notes
AGENT_PHP_DB_USER=notes_user
AGENT_PHP_DB_PASSWORD=secret
```

The `notes` table is created automatically on the first request
with the correct DDL for the connected driver.


---

## 5. The Desktop Client

Two frontends live under `desktop/`. Both talk to the same HTTP
and WebSocket API the browser uses.

### `desktop.app` — embedded Chromium (recommended)

Launch with `python run.py desktop`. A native window opens with:

- **Menu bar** — File, View, Backend, Logs, Help
- **Toolbar** — Start Backend, Stop Backend, Chat, Graph,
  Settings, Reload, Open in Browser
- **Central area** — a `QWebEngineView` rendering the same web UI
- **Log dock** — a collapsible panel at the bottom showing
  backend stdout/stderr in real time
- **Status bar** — backend health (model, MCP state, tools
  disabled?) and the current URL

The launcher spawns the FastAPI backend as a subprocess and
monitors it. If a backend is already running at the configured
address, the launcher attaches to it instead of spawning a
duplicate.

#### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| **Ctrl+1** | Navigate to chat |
| **Ctrl+2** | Navigate to graph editor |
| **Ctrl+3** | Navigate to settings |
| **Ctrl+L** | Open the log viewer dialog |
| **Ctrl+B** | Open the current page in the system browser |
| **Ctrl+Q** | Quit |
| **Ctrl+Shift+Q** | Stop all processes |
| **F5** | Reload the page |

#### Log viewer

**View → View Logs… (Ctrl+L)** opens a dialog that reads
`logs/backend-*.log` and `logs/desktop-*.log`. The user picks a
source (all / backend only / desktop only) and a line count
(100–10,000). No live tailing — the dialog refreshes on demand.

#### Stop-all behavior

The **Backend → Stop All Processes…** menu action sends
`POST /api/system/stop-all`. The backend acknowledges immediately,
then shuts down the llama-cpp server, closes the MCP session, and
exits. The launcher waits 1.2 s, then force-kills the tree if it
is still alive.

### `desktop.native_chat` — QtWidgets only

Launch with `python run.py native`. A pure-QtWidgets chat client
for environments without PyQt6-WebEngine.

Supported:
- Streaming token display
- Reasoning (thinking) display
- Tool call and tool result display
- Retry and fallback notices
- Save conversation as Markdown
- Stop-all

Not supported:
- Graph editor
- Settings page
- Ollama model management
- Model picker (uses the default model)

Its transcript surfaces every event as text, which makes it
useful for debugging SSE streams.


---

## 6. Keyboard Shortcuts on the Web UI

| Page | Shortcut | Action |
|---|---|---|
| Chat | Enter | Send message |
| Graph | F5 | Reload (browser default) |
| Graph | Delete | Delete selected nodes |
| Graph | Ctrl+A | Select all nodes |
| Graph | Ctrl+C / Ctrl+V | Copy / paste selected nodes |
| Graph | Spacebar (hold) | Pan the canvas |
| Settings | Enter in the pull-name field | Trigger Pull |

On macOS, substitute **⌘** for Ctrl where the OS does so by
default. On the desktop client, the shortcuts listed in §5 apply
regardless of which page is loaded.


---

## 7. Common Workflows

### "I want a fast first answer from a small model"

1. Open `/settings`.
2. In **Pull a Model**, type `llama3.2:1b` and click **Pull**.
3. Wait for the download to finish.
4. Return to `/`.
5. In the model picker, pick `llama3.2:1b`.
6. Ask your question.

### "I want to run a local GGUF file"

1. Download a `.gguf` file into the `models/` folder. See
   `models/README.md` for repositories and quant suffixes.
2. Open the chat page.
3. Click **📥 Load GGUF** and pick the file.
4. Wait for the load to complete (the busy pill shows the
   current model).
5. Send a message.

### "I want a multi-step workflow"

1. Open `/graph`.
2. Right-click the canvas and build a chain:
   `input.text` → `llm.prompt` → `llm.chat` → `output.text`.
3. Connect the ports.
4. Set the system prompt and user prompt in the two input nodes.
5. Click **▶ Run**.
6. Watch the nodes light up and the Current Run panel stream
   events.
7. Use the **Copy** button in the result bar to grab the output.

### "I want to inspect what a run actually did"

1. Open `/graph`.
2. In the **History** panel, click the run you want to inspect.
3. The full event stream replays into the Current Run panel,
   with the node statuses and the final result restored.

### "I want to add a note to the PHP app"

1. Open `/php/`.
2. Fill in the title and body in the Add a Note card.
3. Click **Save note**.
4. The page reloads and the new note appears at the top of the
   list.

### "I want to check whether the backend is healthy"

1. Open `/settings` and look at the status dots, or
2. `curl http://127.0.0.1:8000/api/health`, or
3. Click **Logs → View Logs…** in the desktop client and check
   the tail of `backend-*.log`.

### "I want to start fresh"

Three levels of reset, from least to most destructive:

| Reset | Command | Effect |
|---|---|---|
| New session | **+ New** in the chat header | Fresh UUID, keeps other sessions |
| Clear graph runs | **Clear all** in the graph History panel | Deletes `graph_runs` rows only |
| Full data wipe | Delete `sessions.db` and `php_app/data/app.db` | Removes all chat and notes |


---

## 8. Where to go next

- `STARTUP.md` — the launch options and their differences.
- `ERROR_HANDLING.md` — what to do when something goes wrong.
- `FAQ.md` — common questions.
- `INSTALLATION.md` — the install paths.
- `FUTURE_EXTENSIONS.md` — what is on the roadmap.

The API reference is at `/docs` in a running backend, and the
backend's own `Extensive_Documentation.md` files walk through
every module.