"""
3olution Agent Graph — test suite.
https://3olution.com

Run all tests:

    pytest tests/ -v

Run a specific file:

    pytest tests/test_engine.py -v
    pytest tests/test_nodes.py -v

Run tests matching a pattern:

    pytest tests/ -k "topo_sort" -v

================================================================
  DEPENDENCIES
================================================================

  pytest          test runner
  pytest-asyncio  async test support (required for test_nodes.py)

Install both:

    pip install pytest pytest-asyncio

================================================================
  WHY SO FEW TESTS
================================================================

  This suite deliberately covers only the pure-logic parts of the
  codebase — the graph engine's topological sort and the built-in
  node implementations. Those are the parts where a subtle bug is
  most likely to hide and where a fast test loop is most valuable.

  The integration surface (FastAPI routes, MCP subprocess, LLM
  clients) is not covered by unit tests because:

    1. Every route ultimately depends on an external service
       (Ollama, llama-cpp, MCP server). Testing them requires
       mocking infrastructure that quickly outweighs the value of
       the tests.

    2. The startup diagnostics in backend/main.py already provide
       runtime validation. A broken route fails loudly on startup
       via _check_router().

  Adding integration tests is a legitimate future extension —
  see the README's "Future Extensions" section.

================================================================
  ASYNC TESTS
================================================================

  pytest-asyncio must be configured to run tests in async mode.
  Two options:

    1. Set it in pytest.ini / pyproject.toml:

           [tool.pytest.ini_options]
           asyncio_mode = "auto"

    2. Mark each async test with @pytest.mark.asyncio:

           @pytest.mark.asyncio
           async def test_something():
               ...

  This suite uses option 2 (explicit markers) so it does not
  require a pytest.ini file. That keeps the project root clean
  and lets users drop the tests into another project without
  needing to configure pytest.
"""

__all__ = []