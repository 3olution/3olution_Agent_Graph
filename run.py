#!/usr/bin/env python3
"""
3olution Agent Graph — cross-platform launcher.
https://3olution.com

This is the developer-workflow entry point. It creates a standard
Python virtual environment (.venv/) in the project root and installs
dependencies there. For the end-user portable workflow, use
setup.sh / setup.ps1 and start.sh / start.bat instead — those use the
self-contained runtime/ directory created by bootstrap/setup.py.

================================================================
  COMMANDS
================================================================

    python run.py setup       Create .venv/, install deps, download
                              LiteGraph vendor files, create the
                              models folder.

    python run.py backend     Start the FastAPI backend + web UI.
                              Accessible at http://127.0.0.1:8000

    python run.py desktop     Start the PyQt6 desktop client with
                              an embedded Chromium browser view.
                              Requires the backend to be running.

    python run.py native      Start the PyQt6 native chat client
                              (no embedded browser). Requires the
                              backend to be running.

    python run.py mcp         Run the MCP server in the foreground
                              for debugging. Normally the backend
                              spawns this automatically.

    python run.py vendor      Force re-download of LiteGraph vendor
                              files into web/vendor/.

================================================================
  WHY TWO WORKFLOWS?
================================================================

  Developer workflow (run.py):
    Uses the Python interpreter the developer already has on PATH.
    Creates .venv/ and installs requirements there. Fast iteration,
    familiar tooling, standard virtualenv semantics.
    Use this when you are working on the code.

  End-user workflow (setup.sh + start.sh):
    Uses bootstrap/setup.py to download a self-contained Python,
    PHP, and SQLite into runtime/. No system-level dependencies.
    Copy the project folder to a USB stick, run start.sh on any
    machine, and it works.
    Use this when you are distributing the app.

  Both workflows run the same backend code. The only difference is
  which Python interpreter executes it.

================================================================
  ENVIRONMENT
================================================================

  PYTHON              Override the interpreter used for the venv
                      (default: sys.executable)

  AGENT_HOST          Bind address (default: 127.0.0.1)
  AGENT_PORT          Bind port (default: 8000)

  PYTHONIOENCODING    Forced to utf-8 for all subprocesses
  PYTHONUTF8          Forced to 1 for all subprocesses
  PYTHONPATH          Prepended with the project root so child
                      processes can import the `backend` package
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
#  Paths and constants
# ══════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENDOR = ROOT / "web" / "vendor"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"

VENDOR_FILES = ["litegraph.min.js", "litegraph.css"]
DOWNLOAD_SCRIPT = ROOT / "scripts" / "download_vendor.py"
SERVE_SCRIPT = ROOT / "scripts" / "serve.py"
REQUIREMENTS = ROOT / "requirements.txt"

# The minimum Python version required by the application. Anything
# older will fail on `asyncio.to_thread`, `X | Y` type hints, and
# other modern constructs.
MIN_PYTHON = (3, 10)


# ══════════════════════════════════════════════════════════════════════
#  Colors (best-effort)
# ══════════════════════════════════════════════════════════════════════

def _supports_color() -> bool:
    """True if the current stdout supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        # Modern Windows Terminal and PowerShell support ANSI, but
        # we have no reliable way to detect it. Assume yes.
        return True
    return True


_C = _supports_color()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _C else text


def info(msg: str) -> None:
    print(f"{_c('36', '•')} {msg}")


def ok(msg: str) -> None:
    print(f"{_c('32', '✓')} {msg}")


def warn(msg: str) -> None:
    print(f"{_c('33', '!')} {msg}", file=sys.stderr)


def error(msg: str) -> None:
    print(f"{_c('31', '✗')} {msg}", file=sys.stderr)


def title(msg: str) -> None:
    bar = "═" * 66
    print()
    print(_c("36", bar))
    print(_c("36", f"  {msg}"))
    print(_c("36", bar))


# ══════════════════════════════════════════════════════════════════════
#  Environment helpers
# ══════════════════════════════════════════════════════════════════════

def venv_python() -> Path:
    """Return the path to the venv's Python interpreter."""
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _subprocess_env() -> dict[str, str]:
    """
    Build the environment for child processes.

    Sets:
      - PYTHONIOENCODING=utf-8  (fixes Unicode on Windows consoles)
      - PYTHONUTF8=1            (enables the UTF-8 mode)
      - PYTHONUNBUFFERED=1      (log lines appear immediately)
      - PYTHONPATH              (project root, so `import backend` works)
    """
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

    root_str = str(ROOT)
    existing = env.get("PYTHONPATH", "")
    parts = existing.split(os.pathsep) if existing else []
    if root_str not in parts:
        parts.insert(0, root_str)
    env["PYTHONPATH"] = os.pathsep.join(parts)

    return env


def _check_python_version() -> None:
    """Abort if the current interpreter is too old."""
    if sys.version_info < MIN_PYTHON:
        error(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required. "
            f"You are running {sys.version_info.major}.{sys.version_info.minor}."
        )
        print()
        print("  Install a newer Python and re-run this script:")
        print("    https://www.python.org/downloads/")
        print()
        sys.exit(2)


# ══════════════════════════════════════════════════════════════════════
#  Directory bootstrap
# ══════════════════════════════════════════════════════════════════════

def ensure_venv() -> None:
    """
    Create .venv/ if it doesn't exist, and install requirements.

    Idempotent: a second call does nothing if .venv/ already exists.
    """
    if VENV.exists():
        return

    title("Creating virtual environment")

    info(f"Location: {VENV}")
    try:
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    except subprocess.CalledProcessError as e:
        error(f"Could not create venv: {e}")
        error("Install the venv module if your Python distribution omits it:")
        error("  Debian/Ubuntu:  sudo apt install python3-venv")
        error("  Fedora:         sudo dnf install python3-virtualenv")
        sys.exit(1)

    info("Upgrading pip")
    subprocess.check_call(
        [str(venv_python()), "-m", "pip", "install", "--upgrade", "pip"],
    )

    if not REQUIREMENTS.exists():
        error(f"requirements.txt not found at {REQUIREMENTS}")
        sys.exit(1)

    info(f"Installing {REQUIREMENTS.name} (this may take a few minutes)")
    try:
        subprocess.check_call([
            str(venv_python()), "-m", "pip", "install", "-r",
            str(REQUIREMENTS),
        ])
    except subprocess.CalledProcessError as e:
        error(f"pip install failed: {e}")
        sys.exit(1)

    ok("Virtual environment ready")


def ensure_models_dir() -> None:
    """
    Create the models/ folder with a README explaining how to use it.
    """
    if MODELS_DIR.exists():
        return
    info(f"Creating models folder: {MODELS_DIR}")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    readme = MODELS_DIR / "README.md"
    readme.write_text(
        "# 3olution Agent Graph — Models Folder\n"
        "\n"
        "Place GGUF files here for use with the llama-cpp-python server.\n"
        "\n"
        "## Downloading models\n"
        "\n"
        "```cmd\n"
        "pip install huggingface-hub\n"
        "huggingface-cli download <repo-id> <filename>.gguf --local-dir .\n"
        "```\n"
        "\n"
        "Example:\n"
        "\n"
        "```cmd\n"
        "huggingface-cli download TheBloke/Llama-2-7B-Chat-GGUF \\\n"
        "    llama-2-7b-chat.Q4_K_M.gguf --local-dir .\n"
        "```\n"
        "\n"
        "## Configuring\n"
        "\n"
        "In `.env`:\n"
        "\n"
        "```env\n"
        "AGENT_LLAMA_CPP_ENABLED=true\n"
        "AGENT_LLAMA_CPP_MODEL=your-model.Q4_K_M.gguf\n"
        "```\n"
        "\n"
        "The backend resolves bare filenames against this folder.\n"
        "Absolute paths are used as-is.\n"
        "\n"
        "## Note\n"
        "\n"
        "GGUF files are gigabytes. This folder is gitignored.\n"
        "Do not commit model weights.\n",
        encoding="utf-8",
    )


def ensure_logs_dir() -> None:
    """Create the logs/ folder if it doesn't exist."""
    if LOGS_DIR.exists():
        return
    info(f"Creating logs folder: {LOGS_DIR}")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════
#  Vendor files
# ══════════════════════════════════════════════════════════════════════

def vendor_files_ok() -> bool:
    """True if every expected vendor file exists and looks non-empty."""
    for name in VENDOR_FILES:
        p = VENDOR / name
        if not p.exists() or p.stat().st_size < 100:
            return False
    return True


def ensure_vendor(force: bool = False) -> bool:
    """
    Ensure the LiteGraph vendor files are present.

    Returns True if the files exist after the call. Never raises.
    """
    if not force and vendor_files_ok():
        return True

    if not DOWNLOAD_SCRIPT.exists():
        warn(f"Download script not found: {DOWNLOAD_SCRIPT}")
        warn("The graph editor at /graph will fall back to a CDN.")
        return False

    title("Downloading LiteGraph vendor files")
    args = [sys.executable, str(DOWNLOAD_SCRIPT)]
    if force:
        args.append("--force")

    try:
        result = subprocess.call(args, cwd=str(ROOT), env=_subprocess_env())
    except Exception as exc:
        error(f"Could not run downloader: {exc}")
        return False

    if result == 0 and vendor_files_ok():
        ok("Vendor files ready")
        return True

    warn("Vendor download failed.")
    warn("The graph editor at /graph will fall back to a CDN at runtime.")
    warn("To retry later:  python run.py vendor")
    return False


# ══════════════════════════════════════════════════════════════════════
#  Commands
# ══════════════════════════════════════════════════════════════════════

def cmd_setup() -> None:
    """
    Full developer setup.

    Creates .venv/, installs requirements, downloads vendor files,
    creates the models and logs directories, and prints next steps.
    """
    ensure_venv()
    ensure_vendor()
    ensure_models_dir()
    ensure_logs_dir()

    title("Setup complete")
    print()
    print("  Start the backend:")
    print("      python run.py backend")
    print()
    print("  Start the desktop client (in a second terminal):")
    print("      python run.py desktop        # embedded Chromium")
    print("      python run.py native         # native widgets")
    print()
    print("  Open the web UI:")
    print("      http://127.0.0.1:8000/")
    print()
    print("  Create a .env file to customise LLM endpoints:")
    print("      cp .env.example .env")
    print()


def cmd_backend() -> None:
    """
    Start the FastAPI backend + web UI.

    Uses scripts/serve.py rather than uvicorn directly, because
    serve.py handles:
      - Windows Proactor event-loop policy
      - sys.path and PYTHONPATH for the reloader child
      - passing loop="asyncio" to uvicorn
    """
    ensure_venv()
    ensure_vendor()
    ensure_models_dir()
    ensure_logs_dir()

    if not SERVE_SCRIPT.exists():
        error(f"Server launcher not found: {SERVE_SCRIPT}")
        sys.exit(1)

    try:
        subprocess.check_call(
            [str(venv_python()), str(SERVE_SCRIPT)],
            cwd=str(ROOT),
            env=_subprocess_env(),
        )
    except KeyboardInterrupt:
        # User hit Ctrl+C — normal shutdown, not an error.
        pass


def cmd_desktop() -> None:
    """
    Start the PyQt6 desktop client with the embedded Chromium view.

    Warns if PyQt6-WebEngine is missing, but does not abort — the
    user can fall back to `python run.py native`.
    """
    ensure_venv()

    # Quick check that PyQt6-WebEngine is actually importable.
    try:
        subprocess.check_call(
            [
                str(venv_python()), "-c",
                "from PyQt6.QtWebEngineWidgets import QWebEngineView",
            ],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        warn("PyQt6-WebEngine is not installed.")
        warn("Install it with:  pip install PyQt6-WebEngine")
        warn("Falling back to native widgets…")
        warn("")

    try:
        subprocess.check_call(
            [str(venv_python()), "-m", "desktop.app"],
            cwd=str(ROOT),
            env=_subprocess_env(),
        )
    except KeyboardInterrupt:
        pass


def cmd_native() -> None:
    """
    Start the PyQt6 native chat client (no embedded browser).

    Assumes the backend is already running. Prints a reminder.
    """
    ensure_venv()
    print()
    info("Native client connects to the backend at http://127.0.0.1:8000")
    info("Start the backend first if you haven't already:")
    info("    python run.py backend")
    print()

    try:
        subprocess.check_call(
            [str(venv_python()), "-m", "desktop.native_chat"],
            cwd=str(ROOT),
            env=_subprocess_env(),
        )
    except KeyboardInterrupt:
        pass


def cmd_mcp() -> None:
    """
    Run the MCP server in the foreground.

    Normally the backend spawns this automatically. This command is
    for debugging the MCP server in isolation: you can see its output
    directly and interact with it over stdin/stdout.
    """
    ensure_venv()
    print()
    info("Running MCP server in the foreground.")
    info("It communicates over stdio. Press Ctrl+C to stop.")
    print()

    try:
        subprocess.check_call(
            [str(venv_python()), "-m", "mcp_server.server"],
            cwd=str(ROOT),
            env=_subprocess_env(),
        )
    except KeyboardInterrupt:
        pass


def cmd_vendor() -> None:
    """Force re-download of LiteGraph vendor files."""
    ensure_vendor(force=True)


# ══════════════════════════════════════════════════════════════════════
#  Dispatch
# ══════════════════════════════════════════════════════════════════════

COMMANDS = {
    "setup":   cmd_setup,
    "backend": cmd_backend,
    "desktop": cmd_desktop,
    "native":  cmd_native,
    "mcp":     cmd_mcp,
    "vendor":  cmd_vendor,
}


def print_usage() -> None:
    print("3olution Agent Graph — launcher")
    print()
    print("Usage:  python run.py <command>")
    print()
    print("Commands:")
    print("  setup      Create .venv/, install deps, download vendor files")
    print("  backend    Start the backend + web UI on http://127.0.0.1:8000")
    print("  desktop    Start the PyQt6 desktop client (embedded Chromium)")
    print("  native     Start the PyQt6 native chat client (no browser)")
    print("  mcp        Run the MCP server in the foreground (debugging)")
    print("  vendor     Force re-download of LiteGraph vendor files")
    print()
    print("For the portable end-user workflow, use:")
    print("  ./setup.sh && ./start.sh          (Linux / macOS)")
    print("  setup.ps1 ; start.bat             (Windows)")
    print()


def main() -> int:
    _check_python_version()

    if len(sys.argv) < 2:
        print_usage()
        return 0

    cmd = sys.argv[1].lower()

    if cmd in ("help", "-h", "--help"):
        print_usage()
        return 0

    if cmd not in COMMANDS:
        error(f"Unknown command: {cmd}")
        print()
        print_usage()
        return 1

    try:
        COMMANDS[cmd]()
    except KeyboardInterrupt:
        print()
        info("Interrupted.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())