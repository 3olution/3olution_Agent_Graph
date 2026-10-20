# MCP Tool Modules

Every `.py` file in this folder is imported automatically when the
MCP server starts. Each module registers its tools by decorating
functions with `@mcp.tool()` against the shared `mcp` instance.

Adding a tool requires no changes to any other file — just drop a
new `.py` file here and restart the backend.

---

## Minimal example

Create `my_tools.py`:

```python
from .._server import mcp


@mcp.tool()
def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit."""
    return celsius * 9 / 5 + 32
Restart the backend with python run.py backend. The
celsius_to_fahrenheit tool is now available to the LLM in chats
and callable from a tool.call node in the graph editor.

The rules
File name must not start with _. Files starting with _ are
skipped by the loader so you can keep private helpers there.

Import mcp from .._server, not from ..server:

python
from .._server import mcp    # ✅ correct
from ..server import mcp     # ❌ circular import
Decorate each tool with @mcp.tool(). The decorator reads the
function's name, signature, type hints, and docstring to build the
JSON schema the LLM sees.

The docstring is prompt engineering. The LLM reads it to
decide when to call the tool. Write it like an instruction:
describe what the tool does, what it needs, and what it returns.

Type hints become parameter types. Use float, int, str,
bool, list[str], dict[str, int], etc. FastMCP / MCPServer
translates these into JSON schema types automatically.

Return JSON-serializable values. str, int, float,
bool, dict, list, or None. Structured returns are
stringified in the conversation.

Async functions work too. A tool can be async def and use
await — the server handles it.

Tool design tips
One job per tool. search_and_download is worse than
search + download because the model can compose them.

Fail with a message, not a raise. Returning
"Error: file not found" lets the model recover. Raising a bare
exception produces a generic error the model cannot act on.

Name tools verb-first. add, multiply, read_file,
current_time — consistent with the built-in tools.

Keep descriptions short and imperative. One line is often
enough.

Document edge cases. "rejects negatives", "returns ISO 8601",
"UTF-8 only" — these are the constraints the model needs.

What is NOT a tool
These are graph nodes, not MCP tools. They live in
backend/graph/nodes.py and backend/graph/custom_nodes/:

input.text, input.number, input.json

llm.chat, llm.prompt

tool.call, tool.list

control.branch, control.foreach, control.merge

Everything under the "Custom" category

MCP tools are only callable by the LLM during a chat, or by the
tool.call graph node. Graph nodes are only callable by the graph
engine. They are separate registries.

Debugging
If a new tool doesn't appear:

Check the backend log for Failed to load MCP tool module: ...
or MCP tool loader: N loaded, M failed.

Verify the file name doesn't start with _.

Verify the import is from .._server import mcp.

Verify the file is in mcp_server/tools/, not mcp_server/.

Restart the backend — there is no hot reload.

To test a single module in isolation:

text
python -c "import mcp_server.tools.my_tools"
If that raises, the traceback tells you exactly what's wrong.

The old way (deprecated)
Before this folder existed, tools were defined inline in
mcp_server/server.py. That still works in principle, but adding
tools there bloats the file and mixes unrelated concerns. New tools
should always go into this folder.