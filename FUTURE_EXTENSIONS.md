50 Possible Extensions for the AI Agent Foundation
Organized by category, with a short note on each — what it adds and roughly how much effort it takes (🟢 small, 🟡 medium, 🔴 large).

🧰 Tools (MCP Server Additions)
Web Search Tool 🟢 — Wrap a search API (Brave, SearXNG, Tavily) so the agent can look things up. Single async function.

URL Fetch + Readability Tool 🟢 — Fetch a page, strip nav/ads, return clean article text. Pairs with web search.

Weather Tool 🟢 — Hit Open-Meteo (no key needed) for current + forecast by city or lat/lon.

Wikipedia Tool 🟢 — Query the Wikipedia REST API for summaries and sections.

News Headlines Tool 🟢 — Pull top headlines from a news API or RSS feeds.

Currency + Unit Converter 🟢 — Exchange rates (exchangerate.host) and unit conversions in one tool.

Code Runner (Sandboxed) 🟡 — Run Python/JS in Docker or a subprocess jail. The single highest-leverage tool for an agent.

Shell Tool (Guarded) 🔴 — Allowlisted commands only, path-restricted. Dangerous but powerful; must be sandboxed.

File Writer / Editor Tool 🟡 — Create, edit, patch files inside a project root. Turns the agent into a coder.

PDF Reader Tool 🟢 — Extract text from PDFs via pypdf for document Q&A.

Spreadsheet Tool 🟢 — Read/write CSV/XLSX with openpyxl or pandas.

Image Tool 🟡 — Generate (DALL·E, Stable Diffusion) or analyze (vision model) images.

OCR Tool 🟢 — Extract text from images using pytesseract or a vision model.

SQL Query Tool 🟡 — Connect to SQLite/Postgres, run read-only queries, return rows as JSON.

HTTP Request Tool 🟢 — Generic GET/POST with auth header passthrough — a Swiss army knife for APIs.

Email Tool 🟡 — Read/send via IMAP + SMTP, with allowlists.

Calendar Tool 🟡 — Read/write events via CalDAV or Google Calendar API.

Git Tool 🟢 — status, diff, log, commit, branch — read-heavy, write-guarded.

GitHub Tool 🟢 — Search repos, read issues/PRs, open issues via the GitHub API.

Translation Tool 🟢 — Wrap a translation API (DeepL, LibreTranslate) for multi-language replies.

🧠 Memory & Retrieval
Conversation Persistence (SQLite) 🟢 — Store every turn; replay by session ID.

Vector Memory 🟡 — Embed past turns (Chroma/Qdrant/pgvector), retrieve the top-k relevant ones into the system prompt.

RAG over Local Documents 🟡 — Index a folder of PDFs/Markdown, expose search_documents as a tool.

Entity Memory 🟡 — Extract and store facts about the user (name, preferences) across sessions.

Summarizing Memory 🟢 — When history exceeds N tokens, summarize the oldest turns into one message.

Episodic Memory Log 🟢 — Append every tool call to a JSONL audit file with timestamp, args, result.

Session Branching 🟡 — Snapshot conversation state, branch it, compare outcomes.

🤖 Agent Behavior
Streaming Token Output 🟢 — Change llm.py to stream=True, emit token events, update both frontends.

Multi-Step Planner 🟡 — Before acting, ask the LLM to produce a plan; execute step by step and mark progress.

Reflection / Self-Critique Loop 🟡 — After the final answer, ask the model to critique it and revise if needed.

ReAct Prompting Mode 🟢 — Alternate Thought → Action → Observation formatting for models that don't support native tool calls.

Sub-Agents / Roles 🔴 — Specialized agents (Researcher, Coder, Reviewer) coordinated by a supervisor.

Parallel Tool Calls 🟢 — Execute multiple tool calls from one turn with asyncio.gather instead of sequentially.

Retry with Backoff 🟢 — Wrap LLM and tool calls with exponential backoff on 429/5xx.

Model Fallback Chain 🟢 — Try a small model first, escalate to a bigger one if it fails or refuses.

Budget / Cost Tracker 🟢 — Count tokens and estimate cost per session; warn before a threshold.

Human-in-the-Loop Approvals 🟡 — Before dangerous tools run, pause and ask the user to approve via the UI.

Deterministic Tool Routing 🟢 — Skip the LLM for obvious cases ("what time is it?") with a fast-path rule.

Guardrails / Output Filters 🟢 — Block prompt-injection patterns, redact secrets, and validate tool args before execution.

Scheduled / Cron Agent 🟡 — Run tasks on a schedule ("summarize my inbox every morning").

🖥️ Frontends & UX
Embedded Web UI in PyQt6 (QWebEngineView + QWebChannel) 🟡 — One UI codebase, wrapped in a native window; Python bridge for tools.

System Tray / Global Hotkey 🟢 — Summon the desktop agent with a keyboard shortcut (see AIRAG).

Markdown + Code Highlighting in Web UI 🟢 — Render agent replies with marked + highlight.js; huge readability boost.

Voice Input (STT) + Voice Output (TTS) 🟡 — whisper.cpp or Web Speech API in, piper or OpenAI TTS out.

File Attachment / Drag-and-Drop 🟢 — Let users drop a PDF or image; the agent reads it via a tool.

Multi-Session Sidebar 🟡 — Tabs or a sidebar to switch between conversations in the same UI.

Mobile-Friendly PWA 🟢 — Add a manifest + service worker to the web UI for install-to-homescreen.

Slack / Discord / Telegram Bot Frontend 🟡 — Bridge the backend to a chat platform; the agent becomes a bot.

🏗️ Infrastructure & Ops
Dockerize Everything 🟡 — Dockerfile + docker-compose.yml for backend + sandbox + vector DB; one command to run.

Auth + Multi-User 🔴 — Bearer token or session cookies on /api/* and /ws, per-user history, rate limits. Required before any public deployment.

🎯 If You Only Pick Three
Code Runner (sandboxed) — #7. Transforms the agent from a talker into a doer.

Vector Memory — #22. Makes the agent feel like it remembers you.

Streaming Tokens — #28. Single biggest perceived-latency win for both UIs.

🧭 Suggested Roadmap
Phase	Extensions	Goal
v0.2 — polish	#28, #32, #35, #43	Better UX, safer loops
v0.3 — memory	#21, #22, #25	Context persists, doesn't overflow
v0.4 — capability	#1, #2, #7, #9, #10	Real research + file/code work
v0.5 — ops	#37, #39, #50, #49	Safe enough to share
v0.6 — reach	#41, #44, #48	Everywhere you work
Each of these is a self-contained addition — the seams in the foundation (mcp_server/server.py for tools, backend/agent.py for behavior, web/app.js and desktop/app.py for UX, .env for config) are already in place. Pick one and it slots in without refactoring.