Test suite overview
text
tests/
    __init__.py         package marker + usage docs
    test_engine.py      pure logic: topological sort, JSON safety
    test_nodes.py       built-in node functions and graph parsing
Test file	Tests	Covers
test_engine.py	15	_topo_sort() (linear, fan-out, fan-in, diamond, disconnected, empty, single, cycles, dangling edges) and _safe()
test_nodes.py	30+	Registry, input nodes, output nodes, LLM prompt builder, text templating, control flow (branch, merge, foreach), utility nodes, Graph.from_dict()
Every async test is decorated with @pytest.mark.asyncio. No pytest.ini or pyproject.toml configuration is required — the suite runs from a plain pip install pytest pytest-asyncio.

Run commands:

bash
pytest tests/ -v
pytest tests/test_engine.py -v
pytest tests/ -k "topo_sort or branch" -v
What is deliberately not tested
The suite does not cover:

Layer	Why not
FastAPI routes	Requires a live HTTP server; covered indirectly by _check_router() at startup
LLM client (backend/llm.py)	Depends on an external OpenAI-compatible endpoint
MCP client (backend/mcp_client.py)	Requires a live MCP subprocess
llama-cpp manager	Requires the native library and a GGUF file
PHP executor	Requires a php-cgi binary
Desktop client	Requires a GUI environment
These are integration-level concerns. Adding mocked integration tests is a legitimate future extension — see the README's "Future Extensions" section.