Extensive Documentation
Module dependency graph
text
backend/config.py ─────► (no backend deps)
       │
       ├──► backend/state.py  ────► (no backend deps)
       │
       ├──► backend/retry.py  ────► (no backend deps)
       │
       ├──► backend/thinking.py ──► (no backend deps)
       │
       ├──► backend/storage.py ───► (no backend deps)
       │
       ├──► backend/llm.py       ─► config, retry
       │
       ├──► backend/mcp_client.py ─► config, retry
       │
       ├──► backend/agent.py     ─► config, llm, mcp_client,
       │                             storage, thinking, state
       │
       └──► backend/main.py      ─► config, agent, llm, mcp_client,
                                     storage, models_setup, routes/*
config.py, state.py, retry.py, thinking.py, and storage.py
have no backend imports — they can be imported in isolation. This
matters for scripts/serve.py and for tests that want to poke at one
module without pulling in FastAPI.

Configuration ↔ .env mapping
Every AGENT_* variable in .env.example maps to exactly one field
or property on Settings:

.env variable	Settings field	Type
AGENT_UVICORN_ACCESS_LOG	uvicorn_access_log	bool
AGENT_LLM_BASE_URL	llm_base_url	str
AGENT_LLM_API_KEY	llm_api_key	str
AGENT_LLM_MODEL	llm_model	str
AGENT_LLM_TEMPERATURE	llm_temperature	float
AGENT_LLM_TIMEOUT	llm_timeout	float
AGENT_LLM_WARMUP	llm_warmup	bool
AGENT_LLM_FALLBACK_*	llm_fallback_*	str/float
AGENT_DISABLE_TOOLS	disable_tools	bool
AGENT_NO_TOOLS_MODELS	no_tools_models	str → no_tools_model_list
AGENT_SHOW_THINKING	show_thinking	bool
AGENT_OLLAMA_UNLOAD_ON_SWITCH	ollama_unload_on_switch	bool
AGENT_OLLAMA_HOST	ollama_host	str
AGENT_LLAMA_CPP_*	llama_cpp_*	various
AGENT_PHP_*	php_*	various
AGENT_MODELS_DIR	models_dir	str → models_path
AGENT_LOGS_DIR	logs_dir	str → logs_path
AGENT_LOG_FILE_LEVEL	log_file_level	str
AGENT_LOG_RETENTION_DAYS	log_retention_days	int
AGENT_LOG_TO_FILE	log_to_file	bool
AGENT_HOST	host	str
AGENT_PORT	port	int
AGENT_LOG_LEVEL	log_level	str
AGENT_CORS_ORIGINS	cors_origins	str → cors_origin_list
AGENT_MCP_SERVER_COMMAND	mcp_server_command	str
AGENT_MCP_SERVER_ARGS	mcp_server_args	str → mcp_args_list
AGENT_TOOL_TIMEOUT	tool_timeout	float
AGENT_MAX_ITERATIONS	max_iterations	int
AGENT_STREAM_TOKENS	stream_tokens	bool
AGENT_STREAM_STALL_TIMEOUT	stream_stall_timeout	float
AGENT_RETRY_ATTEMPTS	retry_attempts	int
AGENT_RETRY_BACKOFF	retry_backoff	float
AGENT_SESSION_DB	session_db	str
AGENT_MAX_GRAPH_RUNS	max_graph_runs	int
AGENT_LOAD_CUSTOM_NODES	load_custom_nodes	bool
AGENT_DESKTOP_BACKEND	desktop_backend	str
Event schema reference
Every event emitted by the agent loop has a type field:

Type	Fields	Meaning
token	content	A single streamed token of the final answer
thinking	content	A single streamed reasoning token
retry	message	The LLM connection failed; the agent is retrying
fallback	message	Switching from primary to fallback LLM
info	message	Informational notice (e.g. tools disabled for this model)
tool_call	name, args	The agent is invoking a tool
tool_result	name, result	The tool returned a value
final	content, messages	Turn complete; messages is the updated history
The final event appears exactly once per turn and terminates the
stream. Clients should treat any event after final (if it arrives
at all) as a protocol violation.

Guard rails summary
The agent applies four independent guard rails to keep small
models from looping or hanging:

Guard	Trigger	Effect
Duplicate suppression	Same (tool_name, args) twice in one turn	Second call replaced with "produce a final answer" message
Tool call cap	MAX_TOOL_CALLS_PER_TURN (6) reached	Tools stripped from subsequent LLM calls; final answer forced
Stream stall	No token for AGENT_STREAM_STALL_TIMEOUT (360 s)	Turn aborts with TimeoutError
Max iterations	AGENT_MAX_ITERATIONS (10) reached	Turn ends with an explanatory final event
Each guard is independent. A model that calls tools 6 times, then
loops the same tool, then stalls, then exceeds iterations will hit
each guard in turn and produce a coherent error rather than hanging.

Session persistence
Sessions are saved on every terminal path:

Terminal condition	Session saved?
Normal completion (final after a text response)	Yes
LLM error (turn_failed)	Yes
Max iterations reached	Yes
Client disconnect mid-turn (WebSocket)	No (the generator is closed)
Process killed (SIGKILL)	No
The history stored in SQLite is messages[1:] — the system prompt is
stripped before saving, because the system prompt is a backend
concern, not user data. It is re-added on the next turn.

Health endpoint payload
GET /api/health returns a dict describing every subsystem. Fields
are additive over time, so a client should always tolerate unknown
keys.

Field	Meaning
status	Always "ok" if the request succeeds
version	The value of backend.__version__
primary_llm.base_url	Configured primary endpoint
primary_llm.model	Configured primary model
fallback_llm.enabled	Whether a fallback endpoint is configured
llama_cpp.enabled	Whether llama-cpp is enabled in config
llama_cpp.running	Whether the llama-cpp subprocess is up
llama_cpp.ready	Whether it responds to /v1/models
llama_cpp.loaded_model	The currently-loaded GGUF filename
mcp_connected	Whether the MCP stdio session is alive
php.enabled	Whether the PHP notes app is mounted
php.driver	The configured PHP SQL driver
env_file	Whether .env exists (for diagnostics)
models_dir	Absolute path to the models folder
session_db	Absolute or relative path to the SQLite file

Module responsibilities at a glance
Module	Owns	Imports from
llm.py	Multi-provider chat completions (streaming + batch)	config, retry, llama_server
logging_config.py	Loguru console + rotating file sinks	config
mcp_client.py	MCP stdio connection, tool listing, tool calls	config, retry
ollama.py	Ollama native API (list/pull/delete/unload/ps)	config
models_setup.py	models/ directory creation and GGUF resolution	config
llama_installer.py	CUDA detection, wheel recommendation, pip streaming	— (only stdlib + loguru)
llama_server.py	Subprocess lifecycle, model swap, embeddings, LoRAs	config, models_setup
php_server.py	PHP-CGI execution, output parsing, driver hints	config
llm.py — the three-provider model
LLMClient wraps three AsyncOpenAI instances, one per provider:

text
                    ┌────────────────────┐
                    │  AsyncOpenAI       │
                    │  (primary)         │
   "auto"  ────────►│  settings.llm_*    │
   "primary"        └────────────────────┘
   "ollama"
                    ┌────────────────────┐
                    │  AsyncOpenAI       │
                    │  (fallback)        │◄──── "fallback"
                    │  settings.llm_fb_* │
                    └────────────────────┘

                    ┌────────────────────┐
                    │  AsyncOpenAI       │
                    │  (llama_cpp)       │◄──── "llama_cpp"
                    │  settings.llama_*  │       "llamacpp"
                    └────────────────────┘       "local"
Only the primary client is guaranteed to be non-None. self.fallback
is None when AGENT_LLM_FALLBACK_BASE_URL is empty. self.llama_cpp
is always constructed, but its server may not be running — the agent
checks that separately before routing.

Retry semantics
Every call site funnels through retry_async with is_retryable:

Exception	Retried?
openai.APIConnectionError	✅
openai.APITimeoutError	✅
openai.RateLimitError	✅
openai.InternalServerError (5xx)	✅ unless message matches a permanent marker
openai.BadRequestError (400)	❌
openai.AuthenticationError (401)	❌
openai.PermissionDeniedError (403)	❌
openai.NotFoundError (404)	❌
The non-tools cache
_MODELS_WITHOUT_TOOLS is a module-level set. The agent loop calls
mark_model_without_tools(model) the first time a 400 with
is_no_tools_error(exc) is raised. On every subsequent request,
should_use_tools(model) returns False and the tools parameter is
stripped before the SDK call is made.

The cache is process-scoped. It resets on backend restart, which is
fine — the first request re-learns it in under a second.

logging_config.py — sink layout
Sink	Destination	Level	Rotation	Retention
Console	stderr	AGENT_LOG_LEVEL	—	—
File	logs/backend-YYYY-MM-DD.log	AGENT_LOG_FILE_LEVEL	Midnight UTC	AGENT_LOG_RETENTION_DAYS
The file sink uses enqueue=True, which routes writes through a
background thread. That means logging from within an async def never
blocks the event loop, and Windows file locking during rotation is
handled silently by loguru.

mcp_client.py — the 1.x/2.x compatibility shim
The MCP SDK changed one attribute name between versions:

SDK version	Attribute name
1.x	Tool.inputSchema (camelCase)
2.x	Tool.input_schema (snake_case)
_tool_input_schema(tool) reads input_schema first, falls back to
inputSchema, and finally to a well-formed empty schema. That way the
same code works against either version without pinning the SDK.

ollama.py — the httpx DELETE workaround
The one non-obvious line:

python
r = await client.request("DELETE", url, json={"name": name})
Replaces what would normally be:

python
r = await client.delete(url, json={"name": name})  # ✗ TypeError
httpx's .delete() does not accept json=. The generic .request()
method does. This is the only reason delete_model looks unusual.

models_setup.py — resolution rules
resolve_model_path("Bonsai-8B.gguf") checks in order:

Path("Bonsai-8B.gguf").is_absolute() and exists() → return as-is

<project_root>/models/Bonsai-8B.gguf → return if exists

<project_root>/Bonsai-8B.gguf → return if exists

Return None → caller produces a clear error message

Bare filenames always resolve against models/. Absolute paths are
trusted as-is. The project_root fallback is for users who drop a
GGUF at the top level — slightly sloppy but they get a working setup.

llama_installer.py — CUDA recommendation logic
text
detect_nvidia_cuda()
    │
    ├── nvidia-smi not found ────────► "cpu"
    │
    └── parse "CUDA Version: X.Y"
             │
             ├── X >= 12 and Y >= 4 ─► "cu124"
             ├── X >= 12 ────────────► "cu121"
             ├── X == 11 ────────────► "cu118"
             └── X < 11 ─────────────► "cpu" (too old)
The installer streams three event types over SSE: start, line,
success/error, and done. The settings page renders them into a
live-updating log panel.

llama_server.py — the subprocess model
text
┌──────────────────────────────────────────────────────────────────┐
│  Backend process (Python, asyncio)                               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  LlamaServerManager                                        │  │
│  │   ┌───────────────────┐                                    │  │
│  │   │ subprocess.Popen  │  ──┐                               │  │
│  │   └───────────────────┘    │  spawns                       │  │
│  │   ┌───────────────────┐    │                               │  │
│  │   │ reader thread     │ ◄──┘  captures stdout into         │  │
│  │   │  _output_lines    │       a bounded ring buffer        │  │
│  │   └───────────────────┘                                    │  │
│  │   ┌───────────────────┐                                    │  │
│  │   │ httpx clients     │  ──┐  health probes and            │  │
│  │   │  is_ready()       │    │  embeddings go over HTTP       │  │
│  │   │  embed()          │ ◄──┘                               │  │
│  │   │  list_loras()     │                                    │  │
│  │   └───────────────────┘                                    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼ spawns
┌──────────────────────────────────────────────────────────────────┐
│  python -m llama_cpp.server --model X.gguf --port 8080 ...       │
│  (child process, serving /v1/chat/completions and /v1/models)    │
└──────────────────────────────────────────────────────────────────┘
The child is launched with sys.executable, so it inherits the same
interpreter that is running the backend — including the portable
runtime's Python when the app is launched via start.sh / start.bat.

php_server.py — CGI in detail
A PHP CGI request travels like this:

text
FastAPI request
    │
    ▼
php_routes.php_handler
    │
    ├── resolve path safely (reject traversal)
    ├── read body
    └── execute_php(...)
            │
            ├── build CGI env (REQUEST_METHOD, QUERY_STRING, ...)
            ├── spawn php-cgi with php_args
            ├── feed body on stdin
            ├── capture stdout + stderr
            │
            ▼
        _parse_cgi_output
            │
            ├── split at first \n\n
            ├── parse headers, handle "Status: 404 Not Found"
            ├── detect missing-header crash → 500
            └── return {status, headers, body, stderr, error}
    │
    ▼
Response back to FastAPI
The binary is resolved once per request via find_php_cgi(), which
checks AGENT_PHP_BINARY first, then PATH, then a hardcoded list of
XAMPP / WAMP / Homebrew / distro locations.