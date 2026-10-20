Route inventory
After this batch, every route registered on the FastAPI app is:

Router	Prefix	Routes	Purpose
graph_router	(none)	9	Node types, execution, history
ollama_router	/api/ollama	9	Ollama model management
llama_router	/api/llama	9	llama-cpp install and lifecycle
system_router	/api	4	Stop-all, logs
php_router	(none)	5	PHP execution and static
Total: 36 routes across five routers, all matching the _check_router minimums in main.py:

Router	Expected	Actual
graph_router	≥ 8	9
ollama_router	≥ 4	9
llama_router	≥ 6	9
system_router	≥ 2	4
php_router	≥ 1	5
The /php-static fix
This was one of the issues identified in the earlier review: index.php references /php-static/style.css but no such route existed, so the CSS was a 404 on every platform. php_routes.py now registers:

python
@router.get("/php-static/{asset:path}", include_in_schema=False)
async def php_static(asset: str):
    ...
Files live in php_app/static/ and are served with the same path-traversal protection as the PHP execution route.

Route ordering contract
Two rules must hold in graph/routes.py:

/api/graph/runs/latest must be registered before /api/graph/runs/{run_id}.

/api/graph/runs/all must be registered before /api/graph/runs/{run_id}.

If either rule is violated, Starlette captures "latest" or "all" as a run_id, and the corresponding endpoint becomes unreachable. The current order in the file satisfies both rules.

Graph engine data flow
text
POST /api/graph/run  or  WS /ws/graph
        │
        ▼
Graph.from_dict(payload)          ─►  a Graph dataclass
        │
        ▼
execute(graph, state)             ─►  async generator of events
        │
        ├── _topo_sort(graph)     ─►  [node_id, ...] or ValueError
        │
        └── for each node_id:
                ├── registry.get(node.type)
                ├── _gather_inputs(...)
                ├── await nt.fn(ctx, inputs, params)
                └── yield node_done / node_error
        │
        ▼
save_run(payload, events, status, final_text)
        │
        ▼
SQLite graph_runs table
The engine is transport-agnostic. Both the REST and WebSocket routes wrap the same execute() generator; the only difference is whether the events are collected into a list (REST) or yielded frame-by-frame (WS).

Node type inventory
After this batch, the built-in registry contains:

Category	Nodes	Count
Input	input.text, input.number, input.json	3
Output	output.text	1
LLM	llm.prompt, llm.chat	2
Tools	tool.list, tool.call	2
Text	text.template, text.join	2
Control	control.branch, control.merge, control.foreach	3
Utility	util.constant, util.log, util.delay	3
Total built-in		16
Plus seven custom nodes in custom_nodes/example_nodes.py:

Custom node	Demonstrates
custom.uppercase	minimal node
custom.reverse	one-line transformation
custom.random	numeric params with a range
custom.json_extract	dotted-path traversal
custom.llm_json	LLM call + JSON parsing
custom.sleep	async waiting
custom.random_string	parameterised generation
The dynamic param mechanism
Two sources are supported today:

dynamic value	Endpoint fetched	Payload prepended	llm.chat uses
"ollama.models"	/api/ollama/models	"(default)"	—
"all.models"	/api/llama/all-models	"(default)"	✔
llm.chat uses all.models because it lets the user pick either an Ollama model or a local GGUF. The frontend encodes each option's value as provider::model (e.g. "llama_cpp::llama-2-7b.Q4_K_M.gguf") and parses it on submit to route to the right provider.

Custom node loader pattern
The loader in custom_nodes/__init__.py mirrors the MCP tool loader — same file-naming convention (leading underscore means private), same per-module try/except, same "drop a file, restart, it's there" workflow.

text
custom_nodes/
    __init__.py            ← loader
    README.md              ← documentation
    example_nodes.py       ← 7 example nodes, auto-loaded
    _my_private_helpers.py ← skipped (leading underscore)
    my_nodes.py            ← auto-loaded on next restart
Setting AGENT_LOAD_CUSTOM_NODES=false in .env disables the loader entirely — the escape hatch when a broken custom node is blocking startup.