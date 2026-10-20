Desktop architecture
text
┌─────────────────────────────────────────────────────────────────┐
│  desktop.app  (primary client)                                  │
│                                                                 │
│  ┌──────────────────────┐    ┌──────────────────────────────┐   │
│  │  QtWidgets           │    │  BackendLauncher             │   │
│  │  menu, toolbar,      │    │  spawns + monitors the       │   │
│  │  status bar, log     │    │  backend subprocess          │   │
│  │  dock                │    │                              │   │
│  └──────────────────────┘    └──────────────┬───────────────┘   │
│  ┌──────────────────────┐                   │                   │
│  │  QWebEngineView      │                   │ HTTP + WS         │
│  │  → chat UI           │◄──────────────────┘                   │
│  │  → graph editor      │                                       │
│  │  → settings page     │                                       │
│  └──────────────────────┘                                       │
│  ┌──────────────────────┐                                       │
│  │  LogViewerDialog     │  reads logs/*.log                     │
│  └──────────────────────┘                                       │
│  ┌──────────────────────┐                                       │
│  │  InstallDialog       │  talks to /api/llama/install/*        │
│  └──────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
                │
                │  (backend subprocess)
                ▼
┌─────────────────────────────────────────────────────────────────┐
│  backend.main:app  (uvicorn)                                    │
│  ├─ chat / graph / ollama / llama / system / php                │
│  └─ MCP subprocess, llama-cpp subprocess, PHP-CGI               │
└─────────────────────────────────────────────────────────────────┘
                ▲
                │  (same HTTP API, no subprocess spawn)
                │
┌─────────────────────────────────────────────────────────────────┐
│  desktop.native_chat  (fallback client)                         │
│  QtWidgets only — no WebEngine                                  │
└─────────────────────────────────────────────────────────────────┘
The two desktop frontends compared
Feature	desktop.app	desktop.native_chat
Embedded Chromium	✔	✘
Chat with streaming tokens	✔ (via WebEngine)	✔ (via SSE)
Tool call / result display	✔ (via WebUI)	✔
Graph editor	✔ (via WebEngine)	✘
Settings page	✔ (via WebEngine)	✘
Ollama model management	✔ (via WebEngine)	✘
Native llama-cpp install UI	✔ (via InstallDialog)	✘
Save conversation	✔ (Web UI button)	✔ (native button)
Log viewer dialog	✔	✘
Stop all action	✔	✔
Needs PyQt6-WebEngine	✔	✘
The native client is a reduced feature set aimed at environments where the WebEngine wheel is not available or not wanted.

backend_launcher.py — the subprocess lifecycle
text
    BackendLauncher.start()
            │
            ├── is_running()?        → emit started(), return
            │
            ├── _check_health(url)?  → attach, emit started(), return
            │
            └── _spawn()
                    │
                    ├── build cmd (scripts/serve.py or uvicorn)
                    ├── build env (PYTHONPATH, AGENT_PORT, UTF-8)
                    ├── Popen(..., stdout=PIPE, stderr=STDOUT)
                    ├── spawn reader thread → emit log(line)
                    └── start QTimer → _poll_health() every 500 ms
                            │
                            ├── /api/health returns 200  → started()
                            ├── process died             → stop, failed()
                            └── 60 s timeout             → stop, failed()
Two design decisions worth highlighting:

Attach-first semantics. The launcher first checks whether a backend is already running at the configured URL. This makes it possible to develop against uvicorn --reload in a terminal and launch the desktop client separately without a port conflict.

spawned_by_us flag. Only processes the launcher itself started are terminated on close. An externally-started backend is left running, because the user's terminal session controls its lifecycle.

install_dialog.py — pip streaming without a browser
The dialog consumes the same /api/llama/install/install and /api/llama/install/uninstall SSE endpoints the web settings page uses. The SSEWorker QThread wraps httpx.Client.stream(...) and parses each data: frame into a Python dict, emitting it as a Qt signal.

The wheel recommendation comes from /api/llama/install/status, which in turn calls backend.llama_installer.recommend_wheel_index() — the same CUDA detection logic the web UI uses. So switching between the native dialog and the web page produces identical recommendations.

logging_config.py — desktop log location
Path	Rotation	Retention
logs/desktop-YYYY-MM-DD.log	Midnight UTC	14 days
The desktop log directory is the same as the backend's (logs/), so the LogViewerDialog can show both sources side by side. Filtering by name prefix (backend-*.log vs desktop-*.log) separates them.

The desktop sink uses the same enqueue=True setting as the backend — writes go through a background thread, so logging never blocks the Qt event loop.

Access log filtering
Both desktop.app and backend.logging_config filter HTTP access log entries whose status is 2xx or 3xx. This is critical because the desktop client's status bar polls /api/health every 10 seconds, the chat UI polls /api/ollama/ps every 5 seconds, and the graph editor polls /api/graph/runs on page load. Without filtering, a 4-hour session would produce thousands of identical log lines.

The filter preserves 4xx and 5xx entries — those are real problems worth keeping.

Closing behavior
The main window's closeEvent differs based on how the backend was started:

Scenario	On close
desktop.app spawned the backend	Prompt: "Stop the backend and quit?" — stops on Yes
desktop.app attached to an external backend	No prompt — external backend untouched
desktop.native_chat window closes	Nothing — native client never spawns a backend
For desktop.app, a Ctrl+Shift+Q accelerator triggers the "Stop All" action, which sends /api/system/stop-all and then force-kills the tree if the graceful shutdown does not complete in time.

Platform notes
Platform	Notes
Windows 10/11	CREATE_NO_WINDOW hides the subprocess console. taskkill /F /T terminates the entire process tree, including the MCP and llama-cpp children.
Linux	Requires libxcb-cursor0 and friends for Qt; PyQt6-WebEngine requires libnss3, libxcomposite1, libxdamage1, and others. See the README for the full apt/dnf list.
macOS	First launch of a PyInstaller bundle is blocked by Gatekeeper. Run xattr -dr com.apple.quarantine dist/3olutionAgentGraph.app once. QtWebEngine on Apple Silicon requires the arm64 wheel of PyQt6-WebEngine.