#!/usr/bin/env bash
#
# 3olution Agent Graph - portable setup (Linux / macOS)
# https://3olution.com
#
# This script is a thin wrapper around bootstrap/setup.py. Its only
# job is to find a usable Python 3.8+ interpreter on PATH and hand
# off to the actual bootstrap script.
#
# Usage:
#   ./setup.sh                      install everything
#   ./setup.sh --force              re-download everything
#   ./setup.sh --skip-php           skip the PHP runtime
#   ./setup.sh --check              report what's installed
#
# The bootstrap script downloads portable Python, PHP, and SQLite
# into runtime/. After it completes, the application can be launched
# with ./start.sh, which uses runtime/python/bin/python3 directly.
#
# Exit codes:
#   0   success
#   1   no Python found on PATH, or bootstrap failed
#   2   bootstrap found Python but it was too old (< 3.8)
#   130 user interrupted with Ctrl+C

set -euo pipefail

# ----------------------------------------------------------------------
#  Resolve the project root
# ----------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ----------------------------------------------------------------------
#  Colors (disabled when not a terminal or NO_COLOR is set)
# ----------------------------------------------------------------------

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

info()  { printf "${C_CYAN}[info]${C_RESET}  %s\n" "$*"; }
ok()    { printf "${C_GREEN}[ok]${C_RESET}    %s\n" "$*"; }
warn()  { printf "${C_YELLOW}[warn]${C_RESET}  %s\n" "$*" >&2; }
error() { printf "${C_RED}[error]${C_RESET} %s\n" "$*" >&2; }

# ----------------------------------------------------------------------
#  Print banner
# ----------------------------------------------------------------------

printf "\n"
printf "${C_CYAN}=================================================================${C_RESET}\n"
printf "${C_CYAN}  3olution Agent Graph - Portable Setup${C_RESET}\n"
printf "${C_CYAN}  https://3olution.com${C_RESET}\n"
printf "${C_CYAN}=================================================================${C_RESET}\n"
printf "\n"

# ----------------------------------------------------------------------
#  Verify the bootstrap script exists
# ----------------------------------------------------------------------

BOOTSTRAP="bootstrap/setup.py"

if [ ! -f "$BOOTSTRAP" ]; then
    error "Bootstrap script not found: $BOOTSTRAP"
    error "Are you running this from the project root?"
    exit 1
fi

# ----------------------------------------------------------------------
#  Find a Python 3.8+ interpreter
# ----------------------------------------------------------------------

find_python() {
    if [ -n "${PYTHON:-}" ]; then
        if command -v "$PYTHON" >/dev/null 2>&1; then
            echo "$PYTHON"
            return 0
        fi
        warn "PYTHON=$PYTHON is set but not found on PATH; falling back"
    fi

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done

    for path in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if [ -x "$path" ]; then
            echo "$path"
            return 0
        fi
    done

    return 1
}

if ! PYTHON_BIN="$(find_python)"; then
    error "No Python interpreter found on PATH."
    echo
    echo "  The bootstrap script needs Python 3.8+ to run."
    echo "  It will then download a portable Python into runtime/."
    echo
    echo "  Install Python with one of:"
    echo "    Debian / Ubuntu:   sudo apt install python3 python3-venv"
    echo "    Fedora / RHEL:     sudo dnf install python3"
    echo "    Arch:              sudo pacman -S python"
    echo "    macOS (Homebrew):  brew install python@3.12"
    echo
    echo "  Or, if you already have Python installed somewhere unusual:"
    echo "    PYTHON=/path/to/python3 ./setup.sh"
    echo
    exit 1
fi

# ----------------------------------------------------------------------
#  Verify the version
# ----------------------------------------------------------------------

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))')"

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)'; then
    error "Python $PY_VERSION found at $(command -v "$PYTHON_BIN"), but 3.8+ is required."
    echo
    echo "  Upgrade Python with your package manager, or install from:"
    echo "    https://www.python.org/downloads/"
    echo
    exit 2
fi

info "Using Python: $(command -v "$PYTHON_BIN")  ($PY_VERSION)"
printf "\n"

# ----------------------------------------------------------------------
#  Hand off to the bootstrap script
# ----------------------------------------------------------------------

exec "$PYTHON_BIN" "$BOOTSTRAP" "$@"