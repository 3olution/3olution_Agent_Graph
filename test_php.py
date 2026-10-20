"""
Diagnose the exact reason PHP fails to launch.
https://3olution.com

Reproduces the subprocess call that backend/php_server.py makes,
with the same environment, cwd, and arguments. Prints the exact
exception class and message.

Run with:
    runtime\\python\\python.exe test_php.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PHP_BIN = ROOT / "runtime" / "php" / "php.exe"
PHP_APP = ROOT / "php_app"


def main() -> int:
    print("=" * 60)
    print("  PHP launch diagnostic")
    print("=" * 60)
    print()

    print(f"Python:   {sys.executable}")
    print(f"PHP dir:  {PHP_BIN.parent}")
    print(f"PHP exe:  {PHP_BIN}")
    print(f"PHP app:  {PHP_APP}")
    print()

    if not PHP_BIN.exists():
        print("[X] PHP binary missing.")
        return 1

    size = PHP_BIN.stat().st_size
    print(f"[+] PHP binary present ({size:,} bytes)")

    # Build the same environment php_server.py builds.
    env = os.environ.copy()
    env["AGENT_PHP_DB_DRIVER"] = "sqlite"
    env["AGENT_PHP_SQLITE_PATH"] = "data/app.db"
    env["GATEWAY_INTERFACE"] = "CGI/1.1"
    env["SERVER_PROTOCOL"] = "HTTP/1.1"
    env["REQUEST_METHOD"] = "GET"
    env["SCRIPT_FILENAME"] = (PHP_APP / "index.php").as_posix()
    env["SCRIPT_NAME"] = "/php/index.php"
    env["DOCUMENT_ROOT"] = PHP_APP.as_posix()
    env["QUERY_STRING"] = ""
    env["CONTENT_TYPE"] = ""
    env["CONTENT_LENGTH"] = "0"
    env["SERVER_NAME"] = "127.0.0.1"
    env["SERVER_PORT"] = "8000"
    env["SERVER_SOFTWARE"] = "3olution-Python-PHP"
    env["REMOTE_ADDR"] = "127.0.0.1"
    env["REDIRECT_STATUS"] = "1"

    args = [
        str(PHP_BIN),
        "-d", "cgi.force_redirect=0",
        "-d", "cgi.fix_pathinfo=1",
        "-d", "display_errors=1",
        "-d", "display_startup_errors=1",
        "-d", "error_reporting=E_ALL",
    ]

    print()
    print("Attempting subprocess.run with the same parameters")
    print("that backend/php_server.py uses...")
    print()

    try:
        result = subprocess.run(
            args,
            input=b"",
            capture_output=True,
            timeout=30,
            env=env,
            cwd=str(PHP_APP),
        )

        print("[+] subprocess.run returned")
        print(f"    exit code: {result.returncode}")
        print()
        print("--- stdout ---")
        print(result.stdout.decode("utf-8", errors="replace")[:2000])
        print()
        print("--- stderr ---")
        print(result.stderr.decode("utf-8", errors="replace")[:2000])
        return 0 if result.returncode == 0 else 2

    except PermissionError as exc:
        print("[X] PermissionError caught.")
        print(f"    winerror: {getattr(exc, 'winerror', '?')}")
        print(f"    strerror: {getattr(exc, 'strerror', '?')}")
        print(f"    filename: {getattr(exc, 'filename', '?')}")
        print(f"    message:  {exc}")
        print()
        print("    This is the same error the backend gets.")
        return 3

    except FileNotFoundError as exc:
        print(f"[X] FileNotFoundError: {exc}")
        return 4

    except subprocess.TimeoutExpired:
        print("[X] The PHP process timed out after 30 seconds.")
        print("    This usually means PHP is running but stuck.")
        return 5

    except Exception as exc:
        print(f"[X] Unexpected {type(exc).__name__}: {exc}")
        print()
        traceback.print_exc()
        return 6


if __name__ == "__main__":
    sys.exit(main())