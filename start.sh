#!/usr/bin/env bash
#
# 3olution Agent Graph — start the backend (Linux / macOS)
# https://3olution.com
#
# Launches the FastAPI backend using the portable Python interpreter
# in runtime/. If the runtime is missing, this script runs setup.sh
# automatically, then starts the backend.
#
# Usage:
#   ./start.sh                     # start the backend on 127.0.0.1:8000
#   ./start.sh --host 0.0.0.0      # bind to all interfaces (add auth!)
#   ./start.sh --port 9000         # custom port
#   ./start.sh --reload            # development mode with auto-reload
#   ./start.sh --no-open           # don't try to open a browser
#
# Environment:
#   AGENT_HOST                     default bind host (127.0.0.1)
#   AGENT_PORT                     default bind port (8000)
#
# After starting, the following URLs are available:
#   http://HOST:PORT/              Chat UI
#   http://HOST:PORT/graph         Graph editor
#   http://HOST:PORT/settings      Settings (LLM runtime, Ollama, models)
#   http://HOST:PORT/php/          PHP notes app (if PHP is enabled)
#
# Stopping:
#   Press Ctrl+C. The MCP subprocess, llama-cpp server (if any), and
#   PHP-CGI children are cleaned up by the backend's lifespan hook.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────
#  Resolve the project root
# ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ──────────────────────────────────────────────────────────────────────
#  Colors
# ──────────────────────────────────────────────────────────────────────

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    C_CYAN="\033[36m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_RESET="\033[0m"
else
    C_CYAN=""
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
    C_RESET=""
fi

info()  { printf "${C_CYAN}•${C_RESET} %s\n" "$*"; }
ok()    { printf "${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn()  { printf "${C_YELLOW}!${C_RESET} %s\n" "$*" >&2; }
error() { printf "${C_RED}✗${C_RESET} %s\n" "$*" >&2; }

# ──────────────────────────────────────────────────────────────────────
#  Parse arguments
# ──────────────────────────────────────────────────────────────────────
#
# We parse a small set of launcher-specific flags here. Everything
# else is forwarded to uvicorn via scripts/serve.py.

HOST="${AGENT_HOST:-127.0.0.1}"
PORT="${AGENT_PORT:-8000}"
RELOAD="0"

POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --reload)
            RELOAD="1"
            shift
            ;;
        --help|-h)
            cat <<EOF
3olution Agent Graph — start the backend

Usage: ./start.sh [OPTIONS]

Options:
  --host HOST       bind address (default: $HOST, env: AGENT_HOST)
  --port PORT       bind port    (default: $PORT, env: AGENT_PORT)
  --reload          enable uvicorn auto-reload (development)
  --help, -h        show this message

Environment:
  AGENT_HOST        default bind address
  AGENT_PORT        default bind port
  AGENT_LOG_LEVEL   console log level (default: INFO)
  PYTHON            override the Python interpreter path

The backend will be available at:
  http://$HOST:$PORT/             Chat UI
  http://$HOST:$PORT/graph        Graph editor
  http://$HOST:$PORT/settings     Settings
EOF
            exit 0
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

# Restore positional args (currently unused, reserved for future).
if [ ${#POSITIONAL[@]} -gt 0 ]; then
    set -- "${POSITIONAL[@]}"
fi

# ──────────────────────────────────────────────────────────────────────
#  Print banner
# ──────────────────────────────────────────────────────────────────────

printf "\n"
printf "${C_CYAN}╔══════════════════════════════════════════════════════════════════╗${C_RESET}\n"
printf "${C_CYAN}║${C_RESET}  3olution Agent Graph — Starting Backend                         ${C_CYAN}║${C_RESET}\n"
printf "${C_CYAN}║${C_RESET}  https://3olution.com                                            ${C_CYAN}║${C_RESET}\n"
printf "${C_CYAN}╚══════════════════════════════════════════════════════════════════╝${C_RESET}\n"
printf "\n"

# ──────────────────────────────────────────────────────────────────────
#  Verify the runtime is installed, run setup if not
# ──────────────────────────────────────────────────────────────────────

PYTHON_BIN="runtime/python/bin/python3"
SERVE_SCRIPT="scripts/serve.py"

if [ ! -x "$PYTHON_BIN" ]; then
    warn "Portable runtime not found at $PYTHON_BIN"
    info "Running setup.sh first…"
    printf "\n"

    if ! ./setup.sh; then
        error "Setup failed. Fix the errors above and re-run."
        exit 1
    fi

    printf "\n"
    if [ ! -x "$PYTHON_BIN" ]; then
        error "Setup completed but $PYTHON_BIN is still missing."
        error "Try: python bootstrap/setup.py --force --verbose"
        exit 1
    fi
fi

if [ ! -f "$SERVE_SCRIPT" ]; then
    error "Server launcher not found: $SERVE_SCRIPT"
    error "The project appears to be incomplete."
    exit 1
fi

# ──────────────────────────────────────────────────────────────────────
#  Verify the .env file exists (or warn)
# ──────────────────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
    warn "No .env file found. Using built-in defaults:"
    warn "  LLM:    http://localhost:11434/v1  (Ollama)"
    warn "  Model:  deepseek-r1:1.5b"
    warn ""
    warn "To customise, copy the template:"
    warn "  cp .env.example .env"
    warn "  \$EDITOR .env"
    printf "\n"
fi

# ──────────────────────────────────────────────────────────────────────
#  Print the URLs the user will want
# ──────────────────────────────────────────────────────────────────────

info "Backend will start at http://$HOST:$PORT"
printf "\n"
printf "  Chat UI:       ${C_CYAN}http://%s:%s/${C_RESET}\n" "$HOST" "$PORT"
printf "  Graph editor:  ${C_CYAN}http://%s:%s/graph${C_RESET}\n" "$HOST" "$PORT"
printf "  Settings:      ${C_CYAN}http://%s:%s/settings${C_RESET}\n" "$HOST" "$PORT"
printf "  PHP notes:     ${C_CYAN}http://%s:%s/php/${C_RESET}\n" "$HOST" "$PORT"
printf "\n"
printf "  Press ${C_YELLOW}Ctrl+C${C_RESET} to stop.\n"
printf "\n"

# ──────────────────────────────────────────────────────────────────────
#  Export the environment uvicorn and the backend will read
# ──────────────────────────────────────────────────────────────────────

export AGENT_HOST="$HOST"
export AGENT_PORT="$PORT"
export AGENT_RELOAD="$RELOAD"
export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"

# Prepend the portable runtime's bin/ to PATH so any subprocess that
# shells out to "python" gets the portable one, not a system one.
RUNTIME_BIN="$SCRIPT_DIR/runtime/python/bin"
if [ -d "$RUNTIME_BIN" ]; then
    export PATH="$RUNTIME_BIN:$PATH"
fi

# Same idea for PHP: point subprocesses at the portable binary.
RUNTIME_PHP_DIR="$SCRIPT_DIR/runtime/php"
if [ -d "$RUNTIME_PHP_DIR" ]; then
    export PATH="$RUNTIME_PHP_DIR:$PATH"
fi

# ──────────────────────────────────────────────────────────────────────
#  Hand off to the server launcher
# ──────────────────────────────────────────────────────────────────────
#
# scripts/serve.py handles:
#   - Windows Proactor event-loop policy (irrelevant on Unix, but harmless)
#   - sys.path and PYTHONPATH for the reloader child
#   - passing the correct loop="asyncio" to uvicorn
#
# We exec it directly so the Python process replaces this shell and
# Ctrl+C is delivered straight to uvicorn.

exec "$PYTHON_BIN" "$SERVE_SCRIPT"