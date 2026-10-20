# Contributing to 3olution Agent Graph

Thanks for your interest! This document explains how to set up a
development environment, where things live, how to extend the
project, and what a good pull request looks like.

By participating in this project you agree to abide by the
[Code of Conduct][coc] and to license your contributions under the
project's MIT license.

[coc]: https://www.contributor-covenant.org/version/2/1/code_of_conduct/


## Development setup

### Prerequisites

- Python 3.8 or newer on `PATH` (the bootstrap installer needs it
  to run; it then installs its own portable Python 3.12).
- Git.
- Optionally, a C++ compiler for building Python packages from
  source. Most packages ship wheels, so this is rarely needed.
  On Windows: [Microsoft C++ Build Tools][msvc]. On Linux:
  `build-essential` and `python3-dev`. On macOS: Xcode Command
  Line Tools.
- Optionally, [Ollama][ollama] for a local LLM endpoint.

[msvc]: https://visualstudio.microsoft.com/visual-cpp-build-tools/
[ollama]: https://ollama.com

### Clone and bootstrap

```bash
git clone https://github.com/3olution/agent-graph.git
cd agent-graph

# Downloads portable Python 3.12, PHP 8.4 (CLI + CGI), SQLite 3.46
# into runtime/, and installs every pip dependency into that
# private interpreter. Takes 2–5 minutes on a fresh checkout.
python bootstrap/setup.py

# Or, if you prefer a plain .venv:
python run.py setup
Run the backend
bash
python run.py backend          # dev mode with reload on
python scripts/serve.py        # production-shaped, no reload
./start.sh                     # portable launcher (Linux/macOS)
start.bat                      # portable launcher (Windows)
The backend listens on http://127.0.0.1:8000 by default. Visit:

/ — chat UI

/graph — graph editor

/settings — LLM and model management

/php/ — PHP notes app

/docs — FastAPI's auto-generated OpenAPI browser

Run the tests
bash
pip install pytest pytest-asyncio
pytest tests/ -v
The suite runs in under a second. If it takes longer, something
is wrong — most likely a test is accidentally calling an external
service.

Project structure
Folder	What lives here
backend/	FastAPI application — routes, agent loop, LLM client, storage
backend/graph/	Graph engine — model, registry, nodes, executor, routes
backend/graph/custom_nodes/	User-authored node types, auto-loaded
backend/routes/	Additional FastAPI routers (Ollama, llama-cpp, system, PHP)
mcp_server/	Model Context Protocol tool server (stdio subprocess)
mcp_server/tools/	Auto-loaded tool modules
desktop/	PyQt6 desktop client (embedded Chromium or native widgets)
web/	Chat, graph editor, and settings pages (plain HTML/CSS/JS)
web/vendor/	LiteGraph.js — downloaded on demand, gitignored
php_app/	PHP notes application served at /php/
php_app/static/	Static assets served at /php-static/*
tests/	pytest suite
scripts/	Launch helpers and the vendor downloader
bootstrap/	One-shot installer for the portable runtime
runtime/	Portable Python, PHP, SQLite (gitignored, regenerable)
models/	GGUF model files (gitignored)
logs/	Rotating daily log files (gitignored)
For the full picture, see File_Tree.txt at the project root.

Code style
Python
PEP 8 — four-space indents, snake_case functions and
variables, PascalCase classes.

Type hints on every public function signature. Use
from __future__ import annotations at the top of new modules
so forward references work on Python 3.8.

Docstrings on every module, class, and public function. The
project's style is a one-line summary, a blank line, then
details. Multi-line strings use """ on their own lines.

ASCII only in comments and identifiers. Windows editors
occasionally save as Windows-1252, which corrupts box-drawing
characters. Use -, =, and * for section dividers.

ruff for linting and formatting. Run ruff check . and
ruff format . before committing.

mypy for static type checking. Not required to pass
cleanly today, but new code should be typed.

JavaScript
2-space indentation.

"use strict"; at the top of every file.

No semicolons where ASI (automatic semicolon insertion)
handles it cleanly; semicolons on the same line as the
statement they terminate when ambiguity would result.

const and let, never var.

async / await over .then() chains.

Template literals for string interpolation.

No frameworks, no bundler. Everything is plain ES2020
running directly in the browser.

CSS
kebab-case class names: .model-picker, .thinking-body.

CSS custom properties for anything themeable. Each page
defines its own copy of the --bg, --text, --accent, etc.
variables — the project does not share a stylesheet between
pages.

Flexbox and grid for layout. No float-based layouts.

No CSS frameworks. The stylesheets are small and
hand-written.

HTML
Self-closing void elements: <meta ... />, <link ... />,
<input ... />.

UTF-8 charset declared first in <head>.

Viewport meta tag on every page.

Commit messages
Short imperative summary on the first line, 50 characters or
fewer. Blank line, then an optional longer explanation wrapped at
72 characters:

text
Fix MCP handshake on Windows

The MCP server subprocess now sets SelectorEventLoopPolicy
before importing the SDK. Proactor's pipe handling is unreliable
and the handshake failed roughly one time in five.
Do not prefix with feat:, fix:, or similar tags. The summary
line carries the information.

Changelog
Every user-visible change goes into CHANGES.md under the
[Unreleased] section, in one of the six categories (Added,
Changed, Deprecated, Removed, Fixed, Security). See the existing
entries for the expected level of detail.

Adding a node type
There are two ways to add a node type:

Built-in node
Use this when the node is part of the project's core feature set.

Open backend/graph/nodes.py.

Write an async def _my_node(ctx, inputs, params) -> dict
function. Return a dict keyed by output port name.

Register it with a register(NodeType(...)) call. Include at
least a name, display, category, fn, and a list of
Port and Param objects.

Add tests to tests/test_nodes.py. Aim for one test per
realistic input, plus one error case.

Restart the backend. The node appears in the palette.

Custom node
Use this when the node is a personal extension or an experiment.
Custom nodes do not modify the core tree.

Create backend/graph/custom_nodes/my_nodes.py.

Import NodeType, Port, and Param from
..model, and register from ..registry.

Write your node function and register it exactly as above.

Restart the backend. The loader picks up the new file
automatically.

The loader skips files whose names start with an underscore, so
private helpers can live in the same folder.

For a full worked example, see
backend/graph/custom_nodes/example_nodes.py and the
README.md next to it.

Adding an MCP tool
Tools live in mcp_server/tools/ and are auto-loaded at server
startup.

Create a new .py file in mcp_server/tools/. Do not start
its name with an underscore.

Import the shared instance:

python
from .._server import mcp
Decorate a function with @mcp.tool():

python
@mcp.tool()
def my_tool(arg1: str, arg2: int) -> str:
    """One-line description the LLM will read."""
    return f"{arg1} x {arg2}"
Restart the backend. The tool appears in the agent's tool
list on the next turn.

Rules:

Never print to stdout. That channel is the JSON-RPC
transport. Use loguru or sys.stderr instead.

Type hints become the parameter schema. Use str, int,
float, bool, list[str], dict[str, int].

Return JSON-serialisable values. Anything else is
stringified before reaching the LLM.

Keep docstrings short and imperative. The LLM reads them
to decide when to call the tool.

For details, see mcp_server/tools/README.md.

Adding a web page
The web frontend has three pages today. Adding a fourth follows
the same pattern as the existing three.

Create web/mypage.html, web/mypage.css, and
web/mypage.js in web/.

Add an explicit route in backend/main.py before the
static mount:

python
@app.get("/mypage", include_in_schema=False)
async def mypage():
    return _serve_or_404(
        WEB_DIR / "mypage.html",
        "My Page",
        "The page HTML file is missing.",
    )
Add a navigation link to the other pages' <header> sections.

Do not import JavaScript from the existing pages. Each page is
fully self-contained by design, so a change to the chat client
cannot break the graph editor.

If the new page needs a shared style token, add the variable to
its own :root block — the pages do not share a stylesheet.

Adding a test
Tests live in tests/ and use pytest plus pytest-asyncio.

Async tests must be decorated with
@pytest.mark.asyncio. The suite does not use auto-mode.

Fixtures go in the test file that needs them, not in a
shared conftest.py. There is no conftest.py today.

Test names describe the behaviour, not the function:
test_topo_sort_diamond, not test_toposort_4.

One assertion per concept. Use pytest.raises for error
cases; the match= argument is a substring search, so
match="cycle" is enough.

See tests/test_engine.py and tests/test_nodes.py for the
expected style.

Running the whole stack locally
For frontend work, run the backend and open the page in a
browser. For most changes you do not need the desktop client:

bash
python run.py backend
# then open http://127.0.0.1:8000/ in a browser
To test the desktop client:

bash
python run.py desktop        # embedded Chromium
python run.py native         # native PyQt6 widgets
To test the MCP server standalone:

bash
python -m mcp_server.server
The server is silent until it receives a JSON-RPC frame on
stdin. To see what it is doing, watch the backend log instead.

Pull request checklist
Before opening a pull request, confirm every box below:

□ Tests pass (pytest tests/ -v).
□ No new ruff warnings (ruff check .).
□ Documentation updated if behaviour changed. This includes
the relevant README.md, the Extensive_Documentation.md
files, CHANGES.md, and any docstrings.
□ CHANGES.md has an entry under [Unreleased].
□ Commit messages are descriptive. The summary line explains
what, the body explains why.
□ No secrets in the diff. .env, API keys, and personal
paths should never be committed.
□ If the change touches the frontend, all three pages still
render correctly in a browser.
□ If the change touches the backend, the startup log has no
new warnings.
PRs that are small, focused, and self-contained get reviewed
faster than PRs that bundle unrelated changes.

Reporting issues
Open an issue at
https://github.com/3olution/agent-graph/issues and include:

Environment — OS and version, Python version
(python --version), and whether you are using the portable
runtime or a system Python.

Steps to reproduce. A numbered list is best. If the bug
only happens with a specific model, include its name.

Expected behaviour. What you thought would happen.

Actual behaviour. What actually happened, including the
exact error message.

Logs. The last 200 lines of
logs/backend-YYYY-MM-DD.log are almost always enough. If
the issue is MCP-related, also include the output of
python -m mcp_server.server.

Before opening an issue, check the existing issues — the problem
may already be reported. If it is, add a reaction or a comment
rather than opening a duplicate.

Security issues
Do not open a public issue for a security vulnerability.
Instead, email security@3olution.com with a description, a
reproduction if possible, and a note about whether the issue is
already public. You will get a response within 72 hours.

Branding and licensing
The 3olution name and the gradient "3" logo are trademarks of
3olution. Using them in a fork or a derivative work requires
written permission.

The source code is licensed under the MIT license. Contributions
are accepted under the same terms. See LICENSE for the full
text.

By submitting a pull request you confirm that you have the right
to license your contribution under these terms.

text

**What was restored (no content changed):**

| Fix | Detail |
|---|---|
| Code fence for `python bootstrap/setup.py` block | Restored the opening and closing triple-backtick fences with the `bash` language tag |
| Code fence for "Run the backend" commands | Same — `bash` fence restored |
| Code fence for "Run the tests" commands | Same — `bash` fence restored |
| Project structure table | Restored the markdown table header separator row (`|---|---|`) so the table renders as a table instead of plain text |
| Commit message example | Wrapped in a `text` fenced code block |
| Node type / MCP tool / web page numbered lists | Restored `1.`, `2.`, `3.`, etc. so they render as proper ordered lists |
| Nested code blocks inside ordered lists | Restored the triple-backtick fences with the correct indentation so they render as fenced code blocks inside the list items, not as inline text |
| Pull request checklist | Changed `□` to `- [ ]` so GitHub renders them as interactive checkboxes |
| Inline code spans | Restored backticks around file paths, function names, env variables, and identifiers that had lost their formatting |
| `**bold**` markers | Restored around the lead-ins like `**PEP 8**`, `**Type hints**`, `**Custom node**`, `**Never print to stdout.**`, etc. |
| Section heading for "Security issues" | Promoted from a plain text line to a `###` subheading (it was already structurally a subheading; the plain-text version just lost the marker) |
| `what` / `why` emphasis | Restored the `*what*` and `*why*` italics in the PR checklist item about commit messages |
| Angle-bracket link for issues URL | Restored `<https://...>` so the URL renders as a link rather than raw text |

No content was added, removed, or reworded. Only the markdown markup was restored so the file renders correctly in GitHub, GitLab, VS Code preview, and any standard markdown renderer.