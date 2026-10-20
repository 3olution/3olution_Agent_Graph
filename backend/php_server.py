"""
PHP execution engine.
https://3olution.com

Runs PHP scripts through a CGI-capable binary (php-cgi / php-cgi.exe)
without requiring Apache, nginx, or Java.

================================================================
  BINARY RESOLUTION
================================================================

  Search order:

    1. AGENT_PHP_BINARY, if set and pointing at an existing file.
    2. runtime/php/php-cgi.exe  (Windows portable)
       runtime/php/php-cgi      (Unix portable)
    3. runtime/php/php.exe      (Windows portable CLI fallback)
       runtime/php/php          (Unix portable CLI fallback)
    4. php-cgi / php-cgi.exe on PATH
    5. php / php.exe on PATH
    6. Known install locations (XAMPP, WAMP, Homebrew, /usr/bin)

================================================================
  PHP CONFIGURATION
================================================================

  The official Windows PHP build does NOT ship a php.ini. Without
  one, PHP loads with every extension disabled and the notes app
  cannot connect to SQLite.

  This module looks for a php.ini next to the PHP binary and passes
  it explicitly via `-c <path>`. If no php.ini exists, it falls back
  to enabling the essential extensions via `-d` flags, so the notes
  app still works even on a PHP that was not set up by our bootstrap.

  Extensions loaded in the fallback path:

      pdo_sqlite    required for the SQLite connection
      sqlite3       companion to pdo_sqlite
      mbstring      used by mb_strlen in index.php
      openssl       used for HTTPS, hmac, hash functions
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from .config import settings


# ══════════════════════════════════════════════════════════════════════
#  Paths
# ══════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PHP_APP_DIR = PROJECT_ROOT / "php_app"
PORTABLE_PHP_DIR = PROJECT_ROOT / "runtime" / "php"


_WINDOWS_CANDIDATES = [
    r"C:\php\php-cgi.exe", r"C:\php\php.exe",
    r"C:\xampp\php\php-cgi.exe", r"C:\xampp\php\php.exe",
    r"C:\wamp64\bin\php\php.exe",
    r"C:\Program Files\PHP\php-cgi.exe", r"C:\Program Files\PHP\php.exe",
]

_UNIX_CANDIDATES = [
    "/usr/bin/php-cgi", "/usr/local/bin/php-cgi", "/opt/homebrew/bin/php-cgi",
    "/usr/bin/php", "/usr/local/bin/php", "/opt/homebrew/bin/php",
]


# ══════════════════════════════════════════════════════════════════════
#  Binary discovery
# ══════════════════════════════════════════════════════════════════════

def find_php_cgi() -> str | None:
    """Locate a CGI-capable PHP binary, or return None."""
    if settings.php_binary:
        p = Path(settings.php_binary)
        if p.exists():
            return str(p)
        logger.warning("AGENT_PHP_BINARY points to a non-existent file: {}",
                       settings.php_binary)

    for name in ("php-cgi.exe", "php-cgi"):
        p = PORTABLE_PHP_DIR / name
        if p.is_file():
            return str(p)

    for name in ("php.exe", "php"):
        p = PORTABLE_PHP_DIR / name
        if p.is_file():
            return str(p)

    for name in ("php-cgi", "php-cgi.exe"):
        found = shutil.which(name)
        if found:
            return found

    for name in ("php", "php.exe"):
        found = shutil.which(name)
        if found:
            return found

    candidates = (
        _WINDOWS_CANDIDATES if sys.platform == "win32"
        else _UNIX_CANDIDATES
    )
    for cand in candidates:
        if Path(cand).exists():
            return cand

    return None


def is_cgi_binary(php_path: str) -> bool:
    return "cgi" in Path(php_path).name.lower()


def detect_php_version(php_binary: str) -> str | None:
    try:
        result = subprocess.run(
            [php_binary, "-v"],
            capture_output=True, text=True, timeout=5,
        )
        lines = (result.stdout or "").splitlines()
        return lines[0] if lines else None
    except Exception as exc:
        logger.debug("Could not detect PHP version: {}", exc)
        return None


def php_status() -> dict[str, Any]:
    php = find_php_cgi()
    if php is None:
        return {
            "available": False, "binary": None, "is_cgi": False,
            "version": None, "app_dir": str(PHP_APP_DIR),
            "error": ("PHP not found. Run bootstrap/setup.py to install the "
                      "portable PHP, or set AGENT_PHP_BINARY in .env."),
        }
    version = detect_php_version(php)
    cgi = is_cgi_binary(php)
    error = None
    if not cgi:
        error = ("The resolved PHP binary is a CLI build, not a CGI build. "
                 "The /php/ app will return empty responses. Install a "
                 "CGI-capable PHP (php-cgi.exe on Windows).")
    return {
        "available": True, "binary": php, "is_cgi": cgi,
        "version": version, "app_dir": str(PHP_APP_DIR), "error": error,
    }


# ══════════════════════════════════════════════════════════════════════
#  PHP configuration discovery
# ══════════════════════════════════════════════════════════════════════

def _find_php_ini(php_binary: str) -> Path | None:
    """
    Return the path to a php.ini next to `php_binary`, or None.

    Checks for `php.ini` in the same directory as the PHP executable.
    Does not look at system-wide locations — the portable install is
    self-contained by design.
    """
    php_dir = Path(php_binary).parent
    ini = php_dir / "php.ini"
    if ini.is_file():
        return ini
    return None


# ══════════════════════════════════════════════════════════════════════
#  CGI environment
# ══════════════════════════════════════════════════════════════════════

def _build_cgi_env(
    *, script_filename, script_name, request_method, query_string,
    content_type, content_length, document_root, server_host, server_port,
    remote_addr, extra=None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("AGENT_PHP_DB_DRIVER", settings.php_db_driver)
    env.setdefault("AGENT_PHP_SQLITE_PATH", settings.php_sqlite_path)
    env.setdefault("AGENT_PHP_DB_HOST", settings.php_db_host)
    env.setdefault("AGENT_PHP_DB_PORT", str(settings.php_db_port))
    env.setdefault("AGENT_PHP_DB_NAME", settings.php_db_name)
    env.setdefault("AGENT_PHP_DB_USER", settings.php_db_user)
    env.setdefault("AGENT_PHP_DB_PASSWORD", settings.php_db_password)

    env.update({
        "GATEWAY_INTERFACE": "CGI/1.1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REQUEST_METHOD": request_method.upper(),
        "SCRIPT_FILENAME": script_filename.as_posix(),
        "SCRIPT_NAME": script_name,
        "DOCUMENT_ROOT": document_root.as_posix(),
        "PATH_TRANSLATED": script_filename.as_posix(),
        "QUERY_STRING": query_string,
        "CONTENT_TYPE": content_type or "",
        "CONTENT_LENGTH": str(content_length),
        "SERVER_NAME": server_host,
        "SERVER_PORT": str(server_port),
        "SERVER_SOFTWARE": "3olution-Python-PHP",
        "REMOTE_ADDR": remote_addr,
        "REDIRECT_STATUS": "1",
    })

    if extra:
        for k, v in extra.items():
            env["HTTP_" + k.upper().replace("-", "_")] = v

    return env


# ══════════════════════════════════════════════════════════════════════
#  Execution
# ══════════════════════════════════════════════════════════════════════

def _build_php_args(php_binary: str) -> list[str]:
    """
    Build the command-line arguments for a php-cgi invocation.

    Loads php.ini if one exists next to the binary. Otherwise falls
    back to enabling the essential extensions via -d flags, using the
    ext/ folder next to the binary.
    """
    args: list[str] = [php_binary]

    ini = _find_php_ini(php_binary)
    if ini is not None:
        args += ["-c", str(ini)]
    else:
        # Fallback: enable extensions explicitly. This works on the
        # official Windows PHP build, whose extensions live in ext/.
        php_dir = Path(php_binary).parent
        ext_dir = php_dir / "ext"
        if ext_dir.is_dir():
            args += ["-d", f"extension_dir={ext_dir}"]
            for ext in ("pdo_sqlite", "sqlite3", "mbstring", "openssl"):
                args += ["-d", f"extension={ext}"]

    args += [
        "-d", "cgi.force_redirect=0",
        "-d", "cgi.fix_pathinfo=1",
        "-d", "display_errors=1",
        "-d", "display_startup_errors=1",
        "-d", "error_reporting=E_ALL",
    ]
    return args


def execute_php(
    *, script_path, script_name, request_method="GET",
    query_string="", headers=None, body=b"", timeout=None,
) -> dict[str, Any]:
    """Execute a PHP script and return its parsed response."""
    php_binary = find_php_cgi()
    if php_binary is None:
        return {
            "status": 500,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": (b"PHP binary not found.\n"
                     b"See /api/php/status for details, or install PHP and\n"
                     b"set AGENT_PHP_BINARY in .env."),
            "stderr": "", "error": "php_not_found",
        }

    if not script_path.exists() or not script_path.is_file():
        return {
            "status": 404,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": f"PHP script not found: {script_name}".encode("utf-8"),
            "stderr": "", "error": "script_not_found",
        }

    headers = headers or {}
    extra_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in ("content-length", "content-type", "host")
    }

    env = _build_cgi_env(
        script_filename=script_path,
        script_name=script_name,
        request_method=request_method,
        query_string=query_string,
        content_type=headers.get("content-type", ""),
        content_length=len(body),
        document_root=PHP_APP_DIR,
        server_host=settings.host,
        server_port=settings.port,
        remote_addr="127.0.0.1",
        extra=extra_headers,
    )

    php_args = _build_php_args(php_binary)

    try:
        result = subprocess.run(
            php_args,
            input=body,
            capture_output=True,
            timeout=timeout or settings.php_timeout,
            env=env,
            cwd=str(PHP_APP_DIR),
        )
    except subprocess.TimeoutExpired:
        logger.warning("PHP request timed out: {}", script_name)
        return {
            "status": 504,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": b"PHP script timed out.",
            "stderr": "", "error": "timeout",
        }
    except Exception as exc:
        logger.exception("PHP execution failed: {}", exc)
        return {
            "status": 500,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": f"PHP execution error: {exc}".encode("utf-8"),
            "stderr": "", "error": str(exc),
        }

    return _parse_cgi_output(result.stdout, result.stderr)


def _parse_cgi_output(stdout: bytes, stderr: bytes) -> dict[str, Any]:
    normalised = stdout.replace(b"\r\n", b"\n")

    if b"\n\n" in normalised:
        head, body = normalised.split(b"\n\n", 1)
        parsed_ok = True
    else:
        head, body = b"", normalised
        parsed_ok = False

    status = 200 if parsed_ok else 500
    headers: dict[str, str] = {}

    for raw_line in head.split(b"\n"):
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        value = value.strip()
        if name.lower() == "status":
            parts = value.split(None, 1)
            if parts and parts[0].isdigit():
                status = int(parts[0])
            continue
        headers[name] = value

    headers.setdefault("Content-Type", "text/html; charset=utf-8")

    return {
        "status": status,
        "headers": headers,
        "body": body,
        "stderr": stderr.decode("utf-8", errors="replace") if stderr else "",
        "error": None if parsed_ok else "no_cgi_headers",
    }