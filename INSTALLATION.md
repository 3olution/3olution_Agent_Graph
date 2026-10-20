
# Installation Guide — 3olution Agent Graph

## Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.10 | 3.11+ recommended |
| pip | latest | `python -m pip install -U pip` |
| LLM endpoint | any OpenAI-compatible | Ollama is free and local |
| OS | Win 10+, macOS 12+, Linux | PyQt6 is cross-platform |
| RAM | 2 GB for the app | LLM RAM is separate |

The bootstrap installer needs Python 3.8 or newer on `PATH` to run.
It then downloads its own portable Python 3.12 into `runtime/`, so
the version of the system Python does not affect the runtime once
the install completes.

## Step 1 — Clone

```bash
git clone https://github.com/3olution/agent-graph.git
cd agent-graph
Step 2 — Install dependencies
There are two supported installation paths. Pick one.

Option A — Portable runtime (recommended)
Downloads self-contained binaries into runtime/ and installs
every pip dependency into the private interpreter. Does not touch
the system Python, PATH, or site-packages.

bash
python bootstrap/setup.py
What this does, in order:

Downloads portable Python 3.12.11 into runtime/python/.

Downloads portable PHP 8.4 (CLI + CGI) into runtime/php/.

Downloads the SQLite CLI 3.46 into runtime/sqlite/.

Runs pip install -r requirements.txt against the private
Python.

Downloads the LiteGraph vendor files into web/vendor/.

Writes runtime/runtime.env — a manifest listing every
installed binary and its absolute path.

The whole process takes 2–5 minutes on a fresh checkout. It is
safe to re-run; stages that are already complete are skipped
unless --force is passed.

Common flags:

bash
python bootstrap/setup.py --force          # reinstall everything
python bootstrap/setup.py --skip-php       # skip the PHP runtime
python bootstrap/setup.py --check          # report state, install nothing
Option B — Plain virtual environment
Uses the system Python and a standard .venv/. Faster to set up
if you already have a matching Python on PATH, but does not
install PHP or the SQLite CLI.

bash
python run.py setup
This creates .venv/, upgrades pip, and installs
requirements.txt into it.

Which one should I use?
Scenario	Recommended
First-time install, want everything to just work	Option A
Development on the Python code only	Option B
Shared machine, no admin rights	Option A
Air-gapped environment with a pre-cached runtime/_downloads/	Option A
CI runner with a pinned Python already present	Option B
Step 3 — Set up an LLM
The backend talks to any OpenAI-compatible endpoint. Ollama is the
default and is what most users run locally.

Option A — Ollama (default)
Install Ollama from https://ollama.com/download.

Pull a model:

bash
ollama pull llama3.2
Confirm it is running:

bash
curl http://localhost:11434/api/version
You should see a JSON response with a version field.

The backend's defaults already point at Ollama
(http://localhost:11434/v1, model llama3.2), so no .env
edits are needed.

Option B — A different OpenAI-compatible endpoint
Copy .env.example to .env and set the primary LLM variables:

env
AGENT_LLM_BASE_URL=https://api.example.com/v1
AGENT_LLM_API_KEY=sk-...
AGENT_LLM_MODEL=your-model-name
Any endpoint that speaks the OpenAI Chat Completions wire format
works: LM Studio, vLLM, Groq, Together, OpenRouter, the real
OpenAI API, and so on.

Option C — llama-cpp-python (local GGUF files)
Installed on demand from the Settings page after the backend is
running. See Step 6.

Step 4 — Configure (optional)
The application runs against Ollama with sensible defaults and no
.env file. To customise anything, copy the example:

bash
cp .env.example .env
Every variable is documented in the Extensive_Documentation.md
files. The most commonly changed ones:

Variable	Default	Purpose
AGENT_HOST	127.0.0.1	Bind address
AGENT_PORT	8000	Bind port
AGENT_LLM_BASE_URL	http://localhost:11434/v1	Primary LLM endpoint
AGENT_LLM_MODEL	llama3.2	Default model
AGENT_LLM_FALLBACK_BASE_URL	(empty)	Optional failover endpoint
AGENT_LLAMA_CPP_ENABLED	true	Enable the local llama-cpp server
AGENT_PHP_ENABLED	true	Mount the /php/ notes app
Step 5 — Run
Pick one of the launch paths below. All of them start the same
FastAPI backend on http://127.0.0.1:8000.

Portable launcher (uses runtime/python)
bash
./start.sh          # Linux / macOS
start.bat           # Windows
Developer launcher
bash
python run.py backend
Starts uvicorn with --reload so code changes are picked up
without a restart.

Direct
bash
python scripts/serve.py
The same uvicorn process the launchers use, but without a
wrapper.

Confirm it is running
Open http://127.0.0.1:8000/api/health in a browser. You should
see a JSON object with "status": "ok". The first request after a
cold start may take a few seconds while the LLM warms up.

Then open:

http://127.0.0.1:8000/ — chat UI

http://127.0.0.1:8000/graph — graph editor

http://127.0.0.1:8000/settings — LLM and model management

http://127.0.0.1:8000/docs — auto-generated API browser

Step 6 — (Optional) Install llama-cpp-python
Skip this step if you are happy using Ollama.

llama-cpp-python runs GGUF models directly inside the backend
process, without a separate Ollama instance. The correct wheel
depends on your GPU, so the install happens from the Settings page
rather than from requirements.txt.

Open http://127.0.0.1:8000/settings.

In the LLM Runtime card, click Show install options.

The dropdown is pre-selected with the wheel recommended by the
backend based on CUDA detection:

Wheel	GPU class
cpu	CPU only
cu118	CUDA 11.8 (GTX 10/16, RTX 20)
cu121	CUDA 12.1 (older 12.x drivers)
cu124	CUDA 12.4 (RTX 30/40, Ada/Hopper)
Click Install. pip's output streams live into the log panel.

Restart the backend so the newly installed module is
loaded.

After the restart, drop a .gguf file into models/ and load it
from the chat header's 📥 Load GGUF button.

Step 7 — (Optional) Desktop client
The desktop client is a PyQt6 window that renders the same web UI
inside an embedded Chromium view, plus a native-widgets fallback.

bash
python run.py desktop        # embedded Chromium (recommended)
python run.py native         # native PyQt6 widgets
The desktop client spawns the backend as a subprocess and manages
its lifecycle. You do not need to run the backend separately.

The desktop client is included in requirements.txt and installed
by both bootstrap options. If you only use the browser UI, you can
comment out the two PyQt6 lines in requirements.txt before
running the bootstrap to avoid the ~150 MB download.

Step 8 — (Optional) PHP notes app
The PHP runtime is installed by bootstrap/setup.py on both
Windows and Unix. If you used run.py setup instead, the PHP app
will return a 500 error with a message explaining how to install
PHP.

The notes app is available at http://127.0.0.1:8000/php/ and
uses SQLite by default. MySQL and PostgreSQL can be selected via
.env:

env
AGENT_PHP_DB_DRIVER=mysql
AGENT_PHP_DB_HOST=127.0.0.1
AGENT_PHP_DB_PORT=3306
AGENT_PHP_DB_NAME=notes
AGENT_PHP_DB_USER=notes_user
AGENT_PHP_DB_PASSWORD=secret
The database and user must exist before the first request. See
php_app/README and the Extensive_Documentation.md in
backend/ for the full reference.

Step 9 — Verify the install
Run the test suite:

bash
pytest tests/ -v
Expected output: 45 tests, all passing, in under a second.

Run the runtime check:

bash
python bootstrap/setup.py --check
Expected output: every component marked [ok], exit code 0.

Check the health endpoint:

bash
curl http://127.0.0.1:8000/api/health
Expected: "status": "ok" with subsystem fields for LLM, MCP,
llama-cpp, PHP, and storage.

Troubleshooting
python: command not found
Install Python 3.10+ from https://python.org/downloads or your
package manager. On Windows, make sure "Add Python to PATH" is
checked in the installer.

Bootstrap fails with "HTTP 403" or "HTTP 404"
The pinned version was probably retired upstream. Check the
*_VERSION_CANDIDATES lists at the top of bootstrap/setup.py
and add a newer version. See the runtime documentation for the
full list of pinned versions and their sources.

pip install fails on a specific package
Some packages need a C++ compiler. On Windows, install
[Microsoft C++ Build Tools][msvc]. On Linux,
sudo apt install build-essential python3-dev. On macOS,
xcode-select --install.

[msvc]: https://visualstudio.microsoft.com/visual-cpp-build-tools/
"Connection refused" when the backend starts
The LLM endpoint is not reachable. If you are using Ollama, start
it with ollama serve in a separate terminal, then retry. The
backend logs the exact endpoint it tried at startup.

/graph returns a 404 page
The LiteGraph vendor files were not downloaded. Run:

bash
python scripts/download_vendor.py --force
Then reload. The graph editor also has a CDN fallback — a page
with the header and a "loading LiteGraph from jsDelivr" notice is
working as intended.

/php/ returns 500
Either the PHP binary was not installed (use bootstrap/setup.py
instead of run.py setup), or the php.ini is missing. Re-run
the PHP installer:

bash
python bootstrap/setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor
The MCP handshake fails on Windows
Symptom: "Connection closed" in the backend log right after
startup. Cause: a bug in the Windows Proactor event loop. Fix:
launch via scripts/serve.py (or any of the launchers, which
call it), not by importing uvicorn directly. See
scripts/serve.py for the details.

Antivirus quarantines php-cgi.exe
This is a false positive on freshly-extracted PHP binaries. Add
<project>/runtime/php/ to your AV exclusion list, or use
Windows Defender. See the runtime documentation for more.

Still stuck?
Capture the following and open an issue at
https://github.com/3olution/agent-graph/issues:

python --version

The output of python bootstrap/setup.py --check

The last 200 lines of logs/backend-YYYY-MM-DD.log

Your OS and version (winver, uname -a)

Uninstall
To remove the portable runtime without touching the rest of the
project:

bash
rm -rf runtime/            # Linux / macOS
rmdir /s /q runtime        # Windows
This deletes the portable Python, PHP, and SQLite. The next run of
bootstrap/setup.py will re-download them. Nothing outside
runtime/ is affected.

To remove everything:

bash
cd ..
rm -rf agent-graph/
Back up first. sessions.db, php_app/data/app.db, and any
.gguf files in models/ are user data and are not regenerable.

Next steps
Read HOW_TO_USE.md for a tour of the interface.

Read STARTUP.md for the launch options and their differences.

Read ERROR_HANDLING.md for the error taxonomy and recovery.

Read FAQ.md for common questions.

Read FUTURE_EXTENSIONS.md for the roadmap.