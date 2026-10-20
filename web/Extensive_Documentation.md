Page inventory and responsibilities
Page	URL	Purpose	Key features
Chat	/	Converse with the agent	WebSocket streaming, model picker, tool toggle, session storage, thinking block
Graph editor	/graph	Build and run agent workflows	LiteGraph canvas, node palette, run history, result bar
Settings	/settings	Configure LLMs and models	LLM Runtime installer, Ollama status, pull/delete models
Each page is fully self-contained — no shared JavaScript between them. That keeps each page simple and lets a browser cache them independently.

Chat UI — event routing
The chat client handles every event the agent loop emits:

Event	Rendering
token	Appends text to a live .msg.streaming bubble (with blinking cursor)
thinking	Appends text to a collapsible .thinking panel
retry	Yellow centered notice
fallback	Teal centered notice
info	Grey-blue centered notice (yellow if it mentions tools)
tool_call	Purple left-bordered bubble with 🔧
tool_result	Blue left-bordered bubble with 📊
final	Freezes the streaming bubble and attaches action buttons
The thinking panel is auto-collapsed by default (.collapsed class caps the visible height at 60 px with a fade-out mask). Users click "Show more" to expand it.

Model picker — the provider::model encoding
The <option> values in the model picker are encoded strings rather than bare model names:

text
ollama::llama3.2
llama_cpp::llama-2-7b-chat.Q4_K_M.gguf
The :: separator lets the client:

Display a single flat list without grouping markup

Parse the provider and model name in one step

parseModelSelection() splits the value on the first ::. The provider (left side) goes into the outgoing message's provider field; the model (right side) goes into model.

When the user selects a GGUF entry that is not currently loaded, the client prompts them to load it immediately. This avoids a silent "model not running" error when they try to chat.

Graph editor — the LiteGraph adapter contract
The adapter layer (web/graph.js) exists because LiteGraph's serialisation format differs from the backend's in several ways:

Concern	LiteGraph	Backend
Node position	pos: [x, y]	position: [x, y]
Node type	type: "Category/name" or type: "name"	type: "name"
Node params	properties: { name: value }	params: { name: value }
Edges	links indexed by ID in lg.links	edges: [{ from_node, from_output, to_node, to_input }]
Type names	derived from registerNodeType argument	exact NodeType.name
graphFromLiteGraph() performs the forward translation; loadGraphIntoLiteGraph() performs the reverse.

The _backendId mechanism keeps a stable identifier on each LiteGraph node so that the same node keeps the same backend ID across serialize/deserialize cycles. Without it, newly added nodes would receive IDs that collide with existing ones on the next run.

Graph editor — history and replay
The right-hand inspector shows two stacked panels:

Current Run — live-streamed events from the active graph execution. Status updates, errors, and the final result appear here in real time.

History — a scrollable list of past runs from /api/graph/runs. Each entry shows the graph name, timestamp, node count, status (green/red dot), and a truncated preview of the final output.

Clicking a history entry calls /api/graph/runs/{run_id} and replays the full event stream into the Current Run panel, additionally restoring the node statuses and painting the final result in the result bar. This makes it possible to inspect what happened in any past run without re-executing it.

The result bar appears at the bottom of the graph page when a run completes and contains the text from the first output.text node. It has a Copy button (copies the result to the clipboard) and a Close button.

Settings page — two independent installers
The settings page manages two different kinds of installable artifacts:

Card	Backend endpoint	Streaming method	Purpose
LLM Runtime	/api/llama/install/*	SSE (install/uninstall events)	pip install llama-cpp-python with the right wheel
Pull a Model	/api/ollama/models/pull	SSE (Ollama NDJSON)	Download a model from the Ollama registry
Both are consumed by the same streamSSE() helper, which parses data: lines and dispatches JSON frames to a callback. This is the web equivalent of the desktop client's SSEWorker in install_dialog.py.

The wheel selector defaults to whatever /api/llama/install/status recommends based on CUDA detection. The installer streams pip's stdout line by line into a <pre> element. A final success event contains the installed version and whether GPU offload is supported.

Session persistence in the chat UI
The chat client stores two keys in localStorage:

Key	Value
3olution-session-id	The current session's UUID
3olution-selected-model	The last model the user picked
On page load, if a session ID is present, the client calls GET /api/sessions/{id} to restore the history. If the session no longer exists (e.g. the SQLite file was cleared), the client creates a fresh session.

When the user clicks + New, the client calls POST /api/sessions to get a new UUID, clears the transcript, and updates localStorage.

Messages are saved to the backend automatically by the agent loop on every terminal path — the client does not need to send explicit "save" requests.

Response format — the final event contract
Every agent turn ends with exactly one final event:

json
{
  "type": "final",
  "content": "The answer text.",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "The answer text."}
  ]
}
The messages field is the complete updated history the client should store and send back on the next turn. It deliberately excludes the system prompt (the backend re-injects it on every turn).

If the turn involved tool calls, the history also contains assistant messages with tool_calls and tool-role messages with tool_call_id. The backend handles these correctly when they are re-sent.

Streaming vs. batched
The web UI uses WebSocket streaming end-to-end. The desktop native client (desktop/native_chat.py) uses the SSE endpoint. Both receive identical event sequences — only the transport differs.

The non-streaming /api/chat endpoint exists for clients that cannot handle streaming (command-line tools, simple scripts, integration tests). It collects every event into an array and returns the array as JSON.

Boot ordering
Chat page (web/app.js):

text
loadModels() → populate model dropdown
  ↓
restore session (or create one)
  ↓
connect() → WebSocket
  ↓
startBusyPolling() → poll /api/ollama/ps every 5 s
Graph page (web/graph.js):

text
fetchUnifiedModels() → cache for dynamic widget choices
  ↓
GET /api/graph/litegraph-types → register all node types
  ↓
new LGraph() + new LGraphCanvas()
  ↓
restore from localStorage, or seed the example graph
  ↓
install onAfterChange → persist on every edit
  ↓
loadHistory() + loadLatestRun()
Settings page (web/settings.js):

text
Promise.all([
    loadRuntimeStatus(),    // llama-cpp install state
    loadStatus(),           // Ollama connection
    loadModels(),           // Ollama model list
])
Cross-platform notes
All three pages are served from the same origin as the backend, so no CORS configuration is required for the standard deployment. The CORS middleware is only needed when a web UI on a different origin (e.g. a dev server on localhost:3000) needs to reach the backend.

The pages work in any modern browser (Chromium 90+, Firefox 90+, Safari 15+). No transpilation, no bundler, no build step. The entire frontend is plain HTML/CSS/JS with a single vendored dependency (LiteGraph).

The settings page's SSE consumption uses ReadableStream and TextDecoder, both of which are baseline features. The chat page's WebSocket handling and the graph page's document.write fallback are equally portable.