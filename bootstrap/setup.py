#!/usr/bin/env python3
"""
3olution Agent Graph — portable runtime bootstrap.
https://3olution.com

Downloads and installs portable, self-contained binaries for Python,
PHP, and SQLite into the `runtime/` directory, installs the Python
dependencies into the private interpreter, fetches the LiteGraph
vendor files, creates the plugins/ folder with a starter template,
and writes a manifest describing everything it installed.

================================================================
  WHAT IT INSTALLS
================================================================

  runtime/python/      Portable CPython 3.12.11
  runtime/php/         Portable PHP 8.4 (CLI + CGI) + php.ini
  runtime/sqlite/      SQLite CLI 3.46 (sqlite3, sqldiff, analyzer)
  runtime/runtime.env  Manifest of installed paths

  .venv/               (only when run via run.py setup)

================================================================
  WHAT IT CREATES
================================================================

  plugins/             Plugin folder with README and _template
  plugins/README.md    Guide for plugin authors
  plugins/_template/   Starter plugin to copy
  runtime/ports.json   (created lazily by the plugin port registry)
  models/              GGUF model folder
  logs/                Rotating log folder
  web/vendor/          LiteGraph vendor files (skipped if present)

================================================================
  PHP ON WINDOWS
================================================================

  Windows needs a CGI-capable PHP binary (`php-cgi.exe`) for the
  /php/ notes app. static-php-cli's Windows ZIP only ships the CLI
  SAPI, so this script downloads the official PHP for Windows ZIP
  from windows.php.net instead. That ZIP contains:

      php.exe          CLI SAPI
      php-cgi.exe      CGI SAPI (required by the backend)
      php8ts.dll       the PHP engine
      ext/             extension DLLs (pdo_sqlite, mbstring, …)
      ... plus every runtime DLL

  The official ZIP does NOT ship a php.ini. Without one, PHP loads
  with every extension disabled. After extraction this script writes
  a minimal php.ini into runtime/php/ that enables pdo_sqlite,
  sqlite3, mbstring, and openssl — the extensions the notes app
  requires.

================================================================
  USAGE
================================================================

    python bootstrap/setup.py                     # full install
    python bootstrap/setup.py --force             # reinstall all
    python bootstrap/setup.py --skip-php          # no PHP runtime
    python bootstrap/setup.py --skip-plugins      # no plugins/ folder
    python bootstrap/setup.py --check             # report state
    python bootstrap/setup.py --list-plugins      # list installed plugins
    python bootstrap/setup.py --verbose           # full traceback on error

================================================================
  BACKWARD COMPATIBILITY
================================================================

  Every flag from previous versions keeps its old behaviour. New flags
  are additive. The plugins/ bootstrap only writes files that do not
  already exist — user plugins and customizations are never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════
#  Version pins
# ══════════════════════════════════════════════════════════════════════

PYTHON_VERSION = "3.12.11"
PYTHON_BUILD_TAG = "20250712"

# Ordered newest-first. The installer tries each in turn until one
# downloads successfully. Update this list when upstream retires a
# version — that is the only maintenance this file needs.
PHP_UNIX_VERSION_CANDIDATES = [
    "8.5.0",
    "8.4.20", "8.4.19", "8.4.18", "8.4.17",
    "8.4.16", "8.4.15",
    "8.3.30", "8.3.29", "8.3.28", "8.3.27",
    "8.2.30", "8.2.29",
]

PHP_WINDOWS_VERSION_CANDIDATES = [
    "8.5.0",
    "8.4.15", "8.4.14",
    "8.3.33", "8.3.32", "8.3.31", "8.3.30",
    "8.2.30", "8.2.29",
]

PHP_WINDOWS_URL_FOLDERS = [
    "releases",
    "releases/archives",
]

SQLITE_VERSION = "3460000"
SQLITE_YEAR = "2024"

# Minimum plausible download sizes — anything smaller is almost
# certainly an HTML error page from a captive portal or proxy.
MIN_SIZE_PYTHON_ARCHIVE      = 10_000_000
MIN_SIZE_PHP_UNIX_ARCHIVE    =  3_000_000
MIN_SIZE_PHP_WINDOWS_ARCHIVE = 20_000_000
MIN_SIZE_SQLITE_ARCHIVE      =    500_000
MIN_SIZE_SQLITE_BINARY       =    100_000

DOWNLOAD_TIMEOUT = 300.0


# ══════════════════════════════════════════════════════════════════════
#  Filesystem paths
# ══════════════════════════════════════════════════════════════════════

ROOT          = Path(__file__).resolve().parent.parent
RUNTIME       = ROOT / "runtime"
DOWNLOADS     = RUNTIME / "_downloads"
REQUIREMENTS  = ROOT / "requirements.txt"
VENDOR_SCRIPT = ROOT / "scripts" / "download_vendor.py"
PLUGINS_DIR   = ROOT / "plugins"
MODELS_DIR    = ROOT / "models"
LOGS_DIR      = ROOT / "logs"
WEB_VENDOR    = ROOT / "web" / "vendor"


# ══════════════════════════════════════════════════════════════════════
#  Console helpers
# ══════════════════════════════════════════════════════════════════════

def _try_enable_windows_ansi() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        if h in (0, -1):
            return False
        mode = ctypes.c_ulong()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        if not k32.SetConsoleMode(h, mode.value | 0x0004):
            return False
        return True
    except Exception:
        return False


_USE_COLOR = (
    os.environ.get("NO_COLOR") != "1"
    and sys.stdout.isatty()
    and _try_enable_windows_ansi()
)


class Log:
    @staticmethod
    def _c(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

    @staticmethod
    def info(m: str)  -> None: print(Log._c("36", "[info] ") + m)
    @staticmethod
    def ok(m: str)    -> None: print(Log._c("32", "[ok]   ") + m)
    @staticmethod
    def warn(m: str)  -> None: print(Log._c("33", "[warn] ") + m)
    @staticmethod
    def error(m: str) -> None: print(Log._c("31", "[error] ") + m, file=sys.stderr)

    @staticmethod
    def title(m: str) -> None:
        bar = "=" * 66
        print()
        print(Log._c("36", bar))
        print(Log._c("36", "  " + m))
        print(Log._c("36", bar))

    @staticmethod
    def banner() -> None:
        print()
        print(Log._c("36", "=" * 66))
        print(Log._c("36", "  3olution Agent Graph - Portable Runtime Bootstrap"))
        print(Log._c("36", "  https://3olution.com"))
        print(Log._c("36", "=" * 66))
        print()


# ══════════════════════════════════════════════════════════════════════
#  Platform detection
# ══════════════════════════════════════════════════════════════════════

def detect_platform() -> dict[str, Any]:
    system_raw = platform.system().lower()
    machine_raw = platform.machine().lower()

    if machine_raw in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine_raw in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        raise SystemExit(f"Unsupported CPU: {machine_raw}")

    if system_raw == "windows":
        return {
            "system": "windows", "arch": arch,
            "python_triple": f"{arch}-pc-windows-msvc",
            "php_os": "windows",
            "php_arch": "x64" if arch == "x86_64" else "arm64",
            "sqlite_platform": f"win-{'x64' if arch == 'x86_64' else 'arm64'}",
            "exe_suffix": ".exe", "is_windows": True,
        }
    if system_raw == "linux":
        return {
            "system": "linux", "arch": arch,
            "python_triple": f"{arch}-unknown-linux-gnu",
            "php_os": "linux", "php_arch": arch,
            "sqlite_platform": "linux-x64",
            "exe_suffix": "", "is_windows": False,
        }
    if system_raw == "darwin":
        return {
            "system": "macos", "arch": arch,
            "python_triple": f"{arch}-apple-darwin",
            "php_os": "macos", "php_arch": arch,
            "sqlite_platform": "osx-x64",
            "exe_suffix": "", "is_windows": False,
        }
    raise SystemExit(f"Unsupported OS: {system_raw}")


# ══════════════════════════════════════════════════════════════════════
#  Download helpers
# ══════════════════════════════════════════════════════════════════════

def _human_bytes(n: int) -> str:
    n_f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n_f) < 1024:
            return f"{n_f:.1f} {unit}"
        n_f /= 1024
    return f"{n_f:.1f} TB"


def _render_progress(d, total, start, final=False):
    elapsed = max(time.monotonic() - start, 0.001)
    speed = d / elapsed
    size = _human_bytes(d)
    speed_s = _human_bytes(int(speed)) + "/s"
    if total > 0:
        pct = d / total * 100
        filled = int(24 * pct / 100)
        bar = "#" * filled + "-" * (24 - filled)
        line = f"    [{bar}] {pct:5.1f}%   {size} / {_human_bytes(total)}   {speed_s}"
    else:
        line = f"    {size}   {speed_s}"
    end = "\n" if final else "\r"
    print(line.ljust(78), end=end, flush=True)


def _download_one(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": "3olution-setup/1.0 (+https://3olution.com)"},
    )
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
        total = int(response.headers.get("Content-Length", "0"))
        downloaded = 0
        start = time.monotonic()
        last_render = start
        with open(tmp, "wb") as f:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_render > 0.1:
                    _render_progress(downloaded, total, start)
                    last_render = now
        _render_progress(downloaded, total, start, final=True)
    tmp.replace(dest)


def download(url, dest, *, force=False, expected_min_size=0, label="file") -> Path:
    if dest.exists() and not force:
        size = dest.stat().st_size
        if size >= expected_min_size:
            Log.ok(f"{label} already cached ({_human_bytes(size)})")
            return dest
        Log.warn(f"{label} cached but small - re-downloading")
    Log.info(f"Downloading {label}")
    print(f"    {url}")
    try:
        _download_one(url, dest)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} while downloading {label}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error while downloading {label}: {e}") from e
    except TimeoutError as e:
        raise RuntimeError(f"Timeout downloading {label}") from e
    if dest.stat().st_size < expected_min_size:
        try: dest.unlink()
        except OSError: pass
        raise RuntimeError(f"{label} is too small after download")
    return dest


# ══════════════════════════════════════════════════════════════════════
#  Archive helpers
# ══════════════════════════════════════════════════════════════════════

def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_tar(tf, target):
    target = target.resolve()
    for m in tf.getmembers():
        if not _is_within(target, (target / m.name)):
            raise RuntimeError(f"Unsafe path: {m.name}")
    if sys.version_info >= (3, 12):
        tf.extractall(target, filter="data")
    else:
        tf.extractall(target)


def _safe_extract_zip(zf, target):
    target = target.resolve()
    for m in zf.namelist():
        if not _is_within(target, (target / m)):
            raise RuntimeError(f"Unsafe path: {m}")
    zf.extractall(target)


def _extract_to_temp(archive: Path, fmt: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="3olution-setup-"))
    try:
        if fmt in ("tar.gz", "tar.xz"):
            mode = "r:gz" if fmt == "tar.gz" else "r:xz"
            with tarfile.open(archive, mode) as tf:
                _safe_extract_tar(tf, tmp)
        elif fmt == "zip":
            with zipfile.ZipFile(archive, "r") as zf:
                _safe_extract_zip(zf, tmp)
        else:
            raise ValueError(f"Unknown format: {fmt}")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp


def _find_extract_root(tmp: Path) -> Path:
    entries = list(tmp.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return tmp


# ══════════════════════════════════════════════════════════════════════
#  Binary paths
# ══════════════════════════════════════════════════════════════════════

def python_binary(plat):
    return RUNTIME / "python" / ("python.exe" if plat["is_windows"] else "bin/python3")


def php_cli_binary(plat):
    return RUNTIME / "php" / ("php.exe" if plat["is_windows"] else "php")


def php_cgi_binary(plat):
    return RUNTIME / "php" / ("php-cgi.exe" if plat["is_windows"] else "php-cgi")


def sqlite_binary(plat):
    return RUNTIME / "sqlite" / ("sqlite3.exe" if plat["is_windows"] else "sqlite3")


# ══════════════════════════════════════════════════════════════════════
#  Verifiers
# ══════════════════════════════════════════════════════════════════════

def _verify_python(exe: Path):
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True,
                           text=True, timeout=15)
        if r.returncode == 0:
            return True, (r.stdout or r.stderr).strip()
        return False, f"exit code {r.returncode}"
    except Exception as e:
        return False, str(e)


def _verify_php_cgi(exe: Path):
    try:
        r = subprocess.run([str(exe), "-v"], capture_output=True,
                           text=True, timeout=15)
        if r.returncode == 0:
            output = (r.stdout or r.stderr).strip()
            first_line = output.splitlines()[0] if output else ""
            return True, first_line
        return False, f"exit code {r.returncode}"
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════
#  php.ini writer
# ══════════════════════════════════════════════════════════════════════

PHP_INI_CONTENT = """; =====================================================================
;  3olution Agent Graph - portable PHP configuration
;  https://3olution.com
;
;  This file is loaded by php-cgi.exe. Without it, PHP loads with no
;  extensions enabled, and the notes app cannot connect to SQLite.
; =====================================================================

; -- Extension directory ----------------------------------------------
; The official Windows PHP build keeps extension DLLs in a subfolder
; named "ext" next to php.exe. Relative paths resolve against the
; directory that contains the php-cgi.exe binary.

extension_dir = "ext"

; -- Extensions required by the notes app -----------------------------

extension=pdo_sqlite
extension=sqlite3
extension=mbstring
extension=openssl

; -- Extensions for optional MySQL / PostgreSQL backends --------------
; Uncomment these if you switch AGENT_PHP_DB_DRIVER in .env.

; extension=pdo_mysql
; extension=mysqli
; extension=pdo_pgsql
; extension=pgsql

; -- Error reporting --------------------------------------------------

display_errors = On
display_startup_errors = On
error_reporting = E_ALL

; -- Date / timezone --------------------------------------------------

date.timezone = UTC
"""


def _write_php_ini(target_dir: Path) -> bool:
    """Write a minimal php.ini into `target_dir`. Never raises."""
    ini = target_dir / "php.ini"
    try:
        ini.write_text(PHP_INI_CONTENT, encoding="utf-8")
        Log.ok(f"Wrote {ini}")
        return True
    except Exception as e:
        Log.warn(f"Could not write php.ini: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  Python installation
# ══════════════════════════════════════════════════════════════════════

def install_python(plat, force):
    Log.title(f"Python {PYTHON_VERSION}")
    target_exe = python_binary(plat)
    if target_exe.exists() and not force:
        Log.ok(f"Already installed: {target_exe}")
        return True

    url = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PYTHON_BUILD_TAG}/"
        f"cpython-{PYTHON_VERSION}%2B{PYTHON_BUILD_TAG}-"
        f"{plat['python_triple']}-install_only_stripped.tar.gz"
    )
    archive = DOWNLOADS / f"python-{PYTHON_VERSION}-{plat['python_triple']}.tar.gz"

    try:
        download(url, archive, force=force,
                 expected_min_size=MIN_SIZE_PYTHON_ARCHIVE,
                 label="portable Python")
    except RuntimeError as e:
        Log.error(str(e)); return False

    Log.info("Extracting Python")
    try:
        tmp = _extract_to_temp(archive, "tar.gz")
    except Exception as e:
        Log.error(f"Extraction failed: {e}"); return False

    target = RUNTIME / "python"
    try:
        root = _find_extract_root(tmp)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(root), str(target))
    except Exception as e:
        Log.error(f"Could not place Python: {e}")
        shutil.rmtree(tmp, ignore_errors=True); return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not target_exe.exists():
        Log.error(f"Python executable not found: {target_exe}"); return False

    ok, detail = _verify_python(target_exe)
    if not ok:
        Log.error(f"Python verification failed: {detail}"); return False

    Log.ok(f"Python installed at {target_exe}  ({detail})")
    return True


# ══════════════════════════════════════════════════════════════════════
#  PHP installation
# ══════════════════════════════════════════════════════════════════════

_installed_php_version: Optional[str] = None


def _install_php_unix(plat, force):
    Log.title("PHP runtime (static-php-cli)")
    cgi_exe = php_cgi_binary(plat)
    cli_exe = php_cli_binary(plat)
    if cgi_exe.exists() and not force:
        Log.ok(f"Already installed: {cgi_exe}")
        return True

    last_error = None
    for version in PHP_UNIX_VERSION_CANDIDATES:
        archive_name = f"php-{version}-cli-{plat['php_os']}-{plat['php_arch']}.tar.gz"
        url = (f"https://dl.static-php.dev/static-php-cli/bulk/"
               f"{plat['php_os']}-{plat['php_arch']}/{archive_name}")
        archive = DOWNLOADS / archive_name

        Log.info(f"Trying PHP {version}")
        try:
            download(url, archive, force=force,
                     expected_min_size=MIN_SIZE_PHP_UNIX_ARCHIVE,
                     label=f"portable PHP {version}")
        except RuntimeError as e:
            last_error = str(e); continue

        Log.info(f"Extracting PHP {version}")
        try:
            tmp = _extract_to_temp(archive, "tar.gz")
        except Exception as e:
            last_error = f"extraction failed: {e}"; continue

        target = RUNTIME / "php"
        try:
            entries = list(tmp.iterdir())
            target.mkdir(parents=True, exist_ok=True)
            if len(entries) == 1 and entries[0].is_file():
                shutil.move(str(entries[0]), str(target / entries[0].name))
            else:
                root = _find_extract_root(tmp)
                for item in root.iterdir():
                    dest = target / item.name
                    if dest.exists():
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    shutil.move(str(item), str(dest))
        except Exception as e:
            Log.error(f"Could not place PHP: {e}")
            shutil.rmtree(tmp, ignore_errors=True); continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if not cgi_exe.exists():
            last_error = f"php-cgi not found at {cgi_exe}"; continue

        try:
            cgi_exe.chmod(cgi_exe.stat().st_mode | 0o755)
            cli_exe.chmod(cli_exe.stat().st_mode | 0o755)
        except OSError:
            pass

        ok, detail = _verify_php_cgi(cgi_exe)
        if not ok:
            last_error = f"verification failed: {detail}"; continue

        global _installed_php_version
        _installed_php_version = version
        Log.ok(f"PHP {version} installed at {cgi_exe}")
        return True

    Log.error(f"All PHP versions failed. Last error: {last_error}")
    return False


def _windows_vs_variants(version):
    try:
        parts = version.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if major > 8 or (major == 8 and minor >= 4):
            return ["vs17", "vs16"]
    except Exception:
        pass
    return ["vs16", "vs17"]


def _install_php_windows(plat, force):
    Log.title("PHP runtime (official Windows build)")
    cgi_exe = php_cgi_binary(plat)
    cli_exe = php_cli_binary(plat)

    if cgi_exe.exists() and cli_exe.exists() and not force:
        Log.ok(f"Already installed: {cgi_exe}")
        if not (RUNTIME / "php" / "php.ini").exists():
            Log.info("php.ini is missing - writing a fresh one")
            _write_php_ini(RUNTIME / "php")
        return True

    last_error = None
    for version in PHP_WINDOWS_VERSION_CANDIDATES:
        for vs in _windows_vs_variants(version):
            for folder in PHP_WINDOWS_URL_FOLDERS:
                archive_name = f"php-{version}-Win32-{vs}-x64.zip"
                url = (f"https://windows.php.net/downloads/{folder}/{archive_name}")
                archive = DOWNLOADS / archive_name

                Log.info(f"Trying PHP {version} ({vs}, {folder})")
                try:
                    download(url, archive, force=force,
                             expected_min_size=MIN_SIZE_PHP_WINDOWS_ARCHIVE,
                             label=f"PHP {version}")
                except RuntimeError as e:
                    last_error = str(e); continue

                Log.info(f"Extracting PHP {version}")
                target = RUNTIME / "php"
                target.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(archive, "r") as zf:
                        _safe_extract_zip(zf, target)
                except Exception as e:
                    last_error = f"extraction failed: {e}"; continue

                if not cli_exe.exists():
                    last_error = f"php.exe not found"; continue
                if not cgi_exe.exists():
                    last_error = f"php-cgi.exe not found"; continue

                ok, detail = _verify_php_cgi(cgi_exe)
                if not ok:
                    last_error = f"php-cgi.exe verification failed: {detail}"
                    continue

                Log.info("Writing php.ini")
                _write_php_ini(target)

                global _installed_php_version
                _installed_php_version = version
                Log.ok(f"PHP {version} installed")
                Log.ok(f"  CLI: {cli_exe}")
                Log.ok(f"  CGI: {cgi_exe}")
                Log.ok(f"  {detail}")
                return True

    Log.error(f"All PHP versions failed. Last error: {last_error}")
    return False


def install_php(plat, force):
    if plat["is_windows"]:
        return _install_php_windows(plat, force)
    return _install_php_unix(plat, force)


# ══════════════════════════════════════════════════════════════════════
#  SQLite installation
# ══════════════════════════════════════════════════════════════════════

def install_sqlite(plat, force):
    Log.title(f"SQLite CLI {SQLITE_VERSION}")
    target_exe = sqlite_binary(plat)
    if target_exe.exists() and not force:
        Log.ok(f"Already installed: {target_exe}")
        return True

    url = (f"https://www.sqlite.org/{SQLITE_YEAR}/"
           f"sqlite-tools-{plat['sqlite_platform']}-{SQLITE_VERSION}.zip")
    archive = (DOWNLOADS
               / f"sqlite-tools-{plat['sqlite_platform']}-{SQLITE_VERSION}.zip")

    try:
        download(url, archive, force=force,
                 expected_min_size=MIN_SIZE_SQLITE_ARCHIVE,
                 label="SQLite CLI")
    except RuntimeError as e:
        Log.error(str(e)); return False

    Log.info("Extracting SQLite CLI")
    try:
        tmp = _extract_to_temp(archive, "zip")
    except Exception as e:
        Log.error(f"Extraction failed: {e}"); return False

    target = RUNTIME / "sqlite"
    try:
        root = _find_extract_root(tmp)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(root), str(target))
    except Exception as e:
        Log.error(f"Could not place SQLite: {e}")
        shutil.rmtree(tmp, ignore_errors=True); return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not target_exe.exists():
        Log.error(f"SQLite binary not found: {target_exe}"); return False

    if not plat["is_windows"]:
        try:
            target_exe.chmod(target_exe.stat().st_mode | 0o755)
        except OSError:
            pass

    if target_exe.stat().st_size < MIN_SIZE_SQLITE_BINARY:
        Log.error("SQLite binary suspiciously small"); return False

    Log.ok(f"SQLite CLI installed at {target_exe}")
    return True


# ══════════════════════════════════════════════════════════════════════
#  Plugin folder bootstrap
# ══════════════════════════════════════════════════════════════════════

# ── Embedded template content ────────────────────────────────────────
#
#  These strings are written into plugins/ on first run. They are
#  never overwritten — if a file exists, the bootstrap leaves it
#  alone so users can customize the template without losing their
#  changes on the next bootstrap run.

PLUGIN_README = """# Plugins

Drop a folder here to add a capability to the 3olution Agent Graph.
Every folder with a `plugin.json` is discovered and loaded at startup.

================================================================
  QUICK START
================================================================

    cp -r _template my_plugin
    cd my_plugin
    # Edit plugin.json, tools.py, services.py, …
    # Restart the backend.

The plugin appears in the Admin panel at /admin under "Plugins".
Its tools appear in the LLM's tool list on the next turn.

================================================================
  WHAT A PLUGIN CAN CONTRIBUTE
================================================================

    tools.py       MCP tools the LLM can call
    nodes.py       Graph nodes for the visual editor
    services.py    Long-running background services (TCP, DNS, …)
    routes.py      HTTP endpoints under /api/plugins/<name>/
    events.py      Subscribers to the plugin event bus
    memory.py      Hooks into the agent's before/after turn flow
    static/        UI assets served at /api/plugins/<name>/static/

Every entry point is optional. A plugin that only needs a tool is
one file — `tools.py` plus `plugin.json`.

================================================================
  NAMING RULES
================================================================

  * Folder name must match plugin.json's "name" field.
  * Names are lowercase, alphanumeric, underscores allowed.
  * Folders starting with `_` are skipped by the loader. Use this
    for templates, examples, and private helpers.

================================================================
  CONFIGURATION
================================================================

  Values live in .env, not in plugin.json. plugin.json declares
  the *schema*; .env holds the values. Environment variable name is:

      PLUGIN_<NAME>_<FIELD>

  Example: a field `api_key` in plugin `weather` is read from
  `PLUGIN_WEATHER_API_KEY`.

  The Admin panel writes to .env for you — the user never edits
  the file by hand.

================================================================
  LIFECYCLE
================================================================

    discover    scan plugins/ for folders
    validate    read and validate plugin.json
    configure   merge manifest defaults with .env values
    import      import the declared entry files
    on_load     plugin registers services and subscriptions
    on_start    each service's start() runs
    on_stop     each service's stop() runs (with timeout)
    on_unload   plugin cleanup (only on reload)

  on_load, on_start, on_stop, on_unload are all optional. A plugin
  that only defines tools.py never sees them.

================================================================
  FAILURE ISOLATION
================================================================

  A broken plugin logs a traceback and is skipped. Every other
  plugin still loads. The backend never fails to start because of
  a bad plugin.

  To disable a plugin temporarily, rename its folder to start with
  an underscore: `plugins/_web_search/`.

================================================================
  EXAMPLES IN THIS REPOSITORY
================================================================

  _template/            A starter plugin to copy
  tcp_agent_server/     A TCP server with command dispatch
  dns_lab_server/       A DNS server with a local zone table

================================================================
  DOCUMENTATION
================================================================

  See plugins/_template/README.md for a walkthrough of each
  entry point, and the Extensions & Plugins reference at the
  project root for the full architecture.

Built by 3olution — https://3olution.com
"""

PLUGIN_TEMPLATE_JSON = """{
  "name": "my_plugin",
  "version": "0.1.0",
  "description": "One-line description shown in the Admin panel",
  "author": "Your Name <you@example.com>",
  "license": "MIT",
  "api_version": "1",

  "entry": {
    "tools":    "tools.py",
    "services": "services.py"
  },

  "config": {
    "greeting": {
      "type": "string",
      "default": "hello",
      "description": "Text returned by the demo tool"
    },
    "enabled": {
      "type": "bool",
      "default": true,
      "description": "Whether the plugin does anything"
    }
  },

  "ports": {
    "control": 0
  },

  "permissions": [],
  "requires": [],

  "ui": {
    "panel": "static/panel.js",
    "settings": true
  },

  "categories": ["example"]
}
"""

PLUGIN_TEMPLATE_TOOLS = '''"""
MCP tools for this plugin.
https://3olution.com

Every function decorated with @mcp.tool() becomes a callable the LLM
can invoke during a chat turn. The docstring is read by the LLM to
decide when to call the tool, so write it as an instruction.

Rules:

  1. Import from mcp_server._server, not mcp_server.server.
  2. Type hints become the parameter schema the LLM sees.
  3. Return JSON-serializable values: str, int, float, bool, dict,
     list, or None.
  4. Never print to stdout — that channel is the JSON-RPC transport.
     Use loguru instead.
"""
from __future__ import annotations

from mcp_server._server import mcp

# Read this plugin's config at call time so the Admin panel can
# change values without a restart.
from plugins.registry import get as get_plugin


def _cfg(key: str, default=None):
    plugin = get_plugin("my_plugin")
    if plugin is None:
        return default
    return plugin.config.get(key, default)


@mcp.tool()
def my_plugin_hello(name: str = "world") -> str:
    """
    Return a greeting. Replace this with a real tool.

    Use this when the user asks the plugin to say hello. The default
    name is "world"; pass a different name to personalise the reply.
    """
    greeting = _cfg("greeting", "hello")
    return f"{greeting}, {name}!"


@mcp.tool()
def my_plugin_status() -> dict:
    """
    Return the plugin's current configuration and state.

    Use this when the user asks whether the plugin is enabled, what
    configuration it is using, or what it is doing.
    """
    return {
        "name": "my_plugin",
        "enabled": _cfg("enabled", True),
        "greeting": _cfg("greeting", "hello"),
    }
'''

PLUGIN_TEMPLATE_SERVICES = '''"""
Long-running background services.
https://3olution.com

A Service is a supervised asyncio task that survives for the
lifetime of the backend. Use this for anything that listens on a
port, polls an external system, or runs a background loop.

The loader calls register_services(ctx) once at startup. Register
zero, one, or many services. Each is supervised independently: a
crash in one is logged and contained.

The Service base class lives in plugins.api.
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger

from plugins.api import PluginContext, Service


class MyPluginService(Service):
    """
    A minimal example service.

    start() spawns the work loop as a task and returns immediately.
    stop() signals the loop to exit and waits for it to finish.
    status() returns a small dict the Admin panel renders.
    """

    name = "my_plugin.worker"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.interval = float(ctx.config.get("interval", 30.0))
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._ticks = 0
        self._started_at = 0.0

    async def start(self) -> None:
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._loop())
        logger.info("my_plugin: worker started (every {}s)", self.interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("my_plugin: worker stopped")

    def status(self) -> dict:
        return {
            "name": self.name,
            "ticks": self._ticks,
            "interval": self.interval,
            "uptime_seconds": round(time.monotonic() - self._started_at, 1)
                             if self._started_at else 0,
        }

    async def _loop(self) -> None:
        """The actual work. Replace this with something useful."""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
            self._ticks += 1
            # Emit an event other plugins can subscribe to.
            self.ctx.events.publish("my_plugin.tick", {"count": self._ticks})


def register_services(ctx: PluginContext) -> None:
    """Called by the plugin loader after import."""
    ctx.register_service(MyPluginService(ctx))
'''

PLUGIN_TEMPLATE_README = """# My Plugin

One-line description of what this plugin does.

## What it contributes

- **Tools** — `my_plugin_hello`, `my_plugin_status`
- **Service** — `my_plugin.worker` (a 30-second tick loop)

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `greeting` | `hello` | Text returned by `my_plugin_hello` |
| `enabled` | `true` | Whether the plugin is active |
| `interval` | `30.0` | Worker tick interval, in seconds |

Override in `.env`:

    PLUGIN_MY_PLUGIN_GREETING=hi
    PLUGIN_MY_PLUGIN_ENABLED=false

## How to make this your plugin

1. Rename the folder to your plugin's name.
2. Edit `plugin.json` — change `name` to match the folder, update
   the description, and remove the parts you do not need.
3. Edit `tools.py` — replace the example tools with your own.
4. Edit `services.py` — replace the worker with your service, or
   delete the file and remove the `services` key from the manifest.
5. Restart the backend. The plugin appears in the Admin panel at
   `/admin` under the Plugins tab.

## Entry points

Every entry point is optional. The loader imports only the files
declared in `plugin.json`'s `entry` map:

| Key | File | What it does |
|---|---|---|
| `tools` | `tools.py` | Registers MCP tools with `@mcp.tool()` |
| `nodes` | `nodes.py` | Registers graph nodes with `register(NodeType(...))` |
| `services` | `services.py` | Registers long-running services |
| `routes` | `routes.py` | Exposes an APIRouter mounted under `/api/plugins/<name>/` |
| `events` | `events.py` | Subscribes to events on the plugin bus |
| `memory` | `memory.py` | Hooks into `before_turn` and `after_turn` |

## The Service contract

A service subclasses `plugins.api.Service` and overrides:

- `start()` — must return when the service is running. Spawn any
  background loops as tasks; do not await them.
- `stop()` — must cleanly release resources (close sockets, cancel
  tasks, wait for drains). Called during shutdown.
- `status()` — returns a small dict the Admin panel renders.

The loader wraps each service in a supervised task. A crash in one
service is logged and contained; the backend keeps running.

## The Event bus

Publish an event:

    self.ctx.events.publish("my_plugin.tick", {"count": 42})

Subscribe to an event:

    def on_load(ctx):
        ctx.events.subscribe("dns.query", my_handler)

    async def my_handler(event):
        # event has a "topic" key plus whatever the publisher sent
        ...

The core publishes a fixed set of events plugins can subscribe to:

    turn.completed
    tool.called
    session.created
    backend.ready
    backend.shutting_down

Plugins can define their own topics. Namespaces are conventions, not
enforced — use `plugin_name.event_name` to avoid collisions.

## Ports

To bind a port, declare it in `plugin.json`:

    "ports": {
      "control": 7777
    }

Then in `services.py`:

    port = ctx.ports.acquire("my_plugin.control", preferred=7777)

The registry reserves the backend's own ports, avoids collisions
between plugins, persists assignments to `runtime/ports.json`, and
falls back to an ephemeral port if the preferred one is taken.

## Testing

To test the plugin without starting the backend, import the tools
module directly:

    python -c "import plugins.my_plugin.tools"

To test a service, construct it with a mock context:

    python -c "
    from plugins.api import PluginContext
    from plugins.my_plugin.services import MyPluginService
    # ... build a minimal ctx, then await service.start()
    "

In practice, the fastest loop is: edit files, restart the backend,
open `/admin`, look at the Plugins and Services tabs.

Built by 3olution — https://3olution.com
"""

PLUGIN_TEMPLATE_NODES = '''"""
Graph nodes for this plugin.
https://3olution.com

Nodes appear in the LiteGraph editor's palette under the category
you specify. The editor fetches them automatically from
/api/graph/litegraph-types at page load.

The example below is a no-op node — replace it with your own.
"""
from __future__ import annotations

from backend.graph.model import NodeType, Param, Port
from backend.graph.registry import register


async def _my_node(ctx, inputs, params):
    """The node's implementation. Return a dict keyed by output port."""
    value = inputs.get("value") or params.get("value", "")
    return {"out": str(value)}


register(NodeType(
    name="my_plugin.passthrough",
    display="My Node",
    category="My Plugin",
    description="Pass a value through unchanged.",
    inputs=[Port("value", "any", optional=True)],
    outputs=[Port("out", "any")],
    params=[Param("value", "text", "")],
    color="#1e4a5f",
    fn=_my_node,
))
'''

PLUGIN_TEMPLATE_ROUTES = '''"""
HTTP routes for this plugin.
https://3olution.com

The backend mounts this router under /api/plugins/<name>/. Return
a module-level `router` — the loader looks for it by name.
"""
from __future__ import annotations

from fastapi import APIRouter


router = APIRouter(tags=["plugin:my_plugin"])


@router.get("/info")
async def info():
    """Metadata the Admin panel reads."""
    return {
        "name": "my_plugin",
        "version": "0.1.0",
    }


@router.get("/example")
async def example():
    """Replace this with a real endpoint."""
    return {"ok": True, "message": "hello from my_plugin"}
'''

PLUGIN_TEMPLATE_PANEL = """// plugins/_template/static/panel.js
//
// The Admin page loads this file and calls mount() when the panel
// is first shown. Register the panel by pushing to the global
// window.PluginPanels array.
//
// The ctx object provides:
//   ctx.api(path, opts)      — fetch under /api/plugins/<name>
//   ctx.config               — this plugin's current config
//   ctx.setConfig(key, val)  — persist a config change
//   ctx.log(line)            — write to the Admin event log

window.PluginPanels = window.PluginPanels || [];

window.PluginPanels.push({
  name: "my_plugin",
  title: "My Plugin",
  icon: "🧩",
  order: 100,

  async mount(container, ctx) {
    container.innerHTML = `
      <div class="plugin-panel">
        <h3>My Plugin</h3>
        <p class="muted">
          Replace this content with your plugin's UI.
        </p>
        <button id="my-plugin-ping">Ping</button>
        <pre id="my-plugin-out" class="output"></pre>
      </div>
    `;

    const btn = container.querySelector("#my-plugin-ping");
    const out = container.querySelector("#my-plugin-out");

    btn.onclick = async () => {
      out.textContent = "…";
      try {
        const res = await ctx.api("/example");
        out.textContent = JSON.stringify(res, null, 2);
      } catch (err) {
        out.textContent = "Error: " + err.message;
      }
    };
  },

  unmount(container) {
    // Optional cleanup.
  },
});
"""


def _write_if_missing(path: Path, content: str) -> bool:
    """
    Write `content` to `path` only if the file does not already exist.
    Returns True if the file was written, False if it was skipped.
    """
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except Exception as exc:
        Log.warn(f"Could not write {path}: {exc}")
        return False


def install_plugins_folder(force: bool = False) -> bool:
    """
    Create the plugins/ folder with a README and a starter template.

    Files are written only if they do not already exist, so this is
    safe to re-run. Pass force=True to overwrite the README and
    template files (user plugins are never touched).
    """
    Log.title("Plugin folder")

    created = 0
    skipped = 0

    # ── Top-level folder ─────────────────────────────────────────────
    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        Log.error(f"Could not create {PLUGINS_DIR}: {exc}")
        return False

    # ── plugins/__init__.py (empty package marker) ───────────────────
    init_file = PLUGINS_DIR / "__init__.py"
    if force and init_file.exists():
        try: init_file.unlink()
        except OSError: pass
    if _write_if_missing(init_file, '"""3olution Agent Graph — plugin system."""\n'):
        created += 1
    else:
        skipped += 1

    # ── plugins/README.md ────────────────────────────────────────────
    readme = PLUGINS_DIR / "README.md"
    if force and readme.exists():
        try: readme.unlink()
        except OSError: pass
    if _write_if_missing(readme, PLUGIN_README):
        created += 1
    else:
        skipped += 1

    # ── plugins/_template/ ───────────────────────────────────────────
    template_dir = PLUGINS_DIR / "_template"
    template_files = {
        "plugin.json":  PLUGIN_TEMPLATE_JSON,
        "tools.py":     PLUGIN_TEMPLATE_TOOLS,
        "services.py":  PLUGIN_TEMPLATE_SERVICES,
        "nodes.py":     PLUGIN_TEMPLATE_NODES,
        "routes.py":    PLUGIN_TEMPLATE_ROUTES,
        "README.md":    PLUGIN_TEMPLATE_README,
        "static/panel.js": PLUGIN_TEMPLATE_PANEL,
    }
    for rel, content in template_files.items():
        path = template_dir / rel
        if force and path.exists():
            try: path.unlink()
            except OSError: pass
        if _write_if_missing(path, content):
            created += 1
        else:
            skipped += 1

    if created:
        Log.ok(f"Plugin folder ready ({created} file(s) created, {skipped} already present)")
    else:
        Log.ok(f"Plugin folder ready ({skipped} file(s) already present)")

    # Count user-installed plugins (folders that do not start with _).
    user_plugins = [
        d for d in PLUGINS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__"
    ]
    if user_plugins:
        Log.info(f"  {len(user_plugins)} user plugin(s) installed: "
                 f"{', '.join(p.name for p in user_plugins)}")
    else:
        Log.info("  No user plugins installed yet")
        Log.info(f"  Copy plugins/_template/ to start one")

    return True


# ══════════════════════════════════════════════════════════════════════
#  Post-install
# ══════════════════════════════════════════════════════════════════════

def ensure_project_dirs() -> None:
    """Create models/, logs/, and web/vendor/ if missing."""
    for d in (MODELS_DIR, LOGS_DIR, WEB_VENDOR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            Log.warn(f"Could not create {d}: {exc}")


def install_python_deps(plat):
    Log.title("Python dependencies")
    exe = python_binary(plat)
    if not exe.exists():
        Log.error(f"Portable Python not found: {exe}"); return False
    if not REQUIREMENTS.exists():
        Log.error(f"requirements.txt not found: {REQUIREMENTS}"); return False

    try:
        subprocess.check_call([str(exe), "-m", "pip", "--version"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        Log.info("pip not found - running ensurepip")
        try:
            subprocess.check_call([str(exe), "-m", "ensurepip", "--upgrade"],
                                  stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            Log.error(f"ensurepip failed: {e}"); return False

    Log.info("Upgrading pip")
    try:
        subprocess.check_call([str(exe), "-m", "pip", "install", "--upgrade", "pip"],
                              stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        Log.warn(f"pip upgrade failed (continuing): {e}")

    Log.info(f"Installing {REQUIREMENTS.name}")
    try:
        subprocess.check_call([str(exe), "-m", "pip", "install",
                               "-r", str(REQUIREMENTS)])
    except subprocess.CalledProcessError as e:
        Log.error(f"pip install failed: {e}"); return False

    Log.ok("Python dependencies installed")
    return True


def download_vendor(plat):
    """
    Download LiteGraph vendor files.

    Vendor files are optional — the graph editor falls back to a
    CDN at runtime. A download failure is logged but does not fail
    the bootstrap.
    """
    Log.title("LiteGraph vendor files")
    if not VENDOR_SCRIPT.exists():
        Log.warn(f"Vendor script not found: {VENDOR_SCRIPT}")
        return True
    exe = python_binary(plat)
    if not exe.exists():
        exe = Path(sys.executable)
    try:
        subprocess.check_call([str(exe), str(VENDOR_SCRIPT)])
    except subprocess.CalledProcessError as e:
        Log.warn(
            f"Vendor download failed: {e}\n"
            f"  The graph editor will load LiteGraph from jsDelivr "
            f"at runtime.\n"
            f"  To retry later: python run.py vendor"
        )
        return True  # non-fatal
    Log.ok("LiteGraph vendor files ready")
    return True


def write_runtime_env(plat):
    Log.title("Runtime manifest")
    py = python_binary(plat)
    php_cli = php_cli_binary(plat)
    php_cgi = php_cgi_binary(plat)
    sqlite = sqlite_binary(plat)
    php_version = _installed_php_version or "unknown"

    # Count user plugins for the manifest.
    user_plugins: list[str] = []
    if PLUGINS_DIR.exists():
        user_plugins = sorted(
            d.name for d in PLUGINS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__"
        )

    lines = [
        "# ==================================================================",
        "# 3olution Agent Graph - Portable Runtime Manifest",
        "# https://3olution.com",
        "#",
        "# Generated by bootstrap/setup.py. Do not edit by hand.",
        "# ==================================================================",
        "",
        f"RUNTIME_PLATFORM={plat['system']}",
        f"RUNTIME_ARCH={plat['arch']}",
        f"RUNTIME_PYTHON_VERSION={PYTHON_VERSION}",
        f"RUNTIME_PHP_VERSION={php_version}",
        f"RUNTIME_SQLITE_VERSION={SQLITE_VERSION}",
        f"RUNTIME_PLUGIN_COUNT={len(user_plugins)}",
        "",
        f"RUNTIME_PYTHON={py}" if py.exists() else "RUNTIME_PYTHON=",
        f"RUNTIME_PHP_CLI={php_cli}" if php_cli.exists() else "RUNTIME_PHP_CLI=",
        f"RUNTIME_PHP_CGI={php_cgi}" if php_cgi.exists() else "RUNTIME_PHP_CGI=",
        f"RUNTIME_SQLITE={sqlite}" if sqlite.exists() else "RUNTIME_SQLITE=",
        "",
    ]
    if user_plugins:
        lines.append("# User-installed plugins:")
        for name in user_plugins:
            lines.append(f"RUNTIME_PLUGIN_{name.upper()}=enabled")
        lines.append("")

    runtime_env = RUNTIME / "runtime.env"
    runtime_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    Log.ok(f"Wrote {runtime_env}")


# ══════════════════════════════════════════════════════════════════════
#  --check
# ══════════════════════════════════════════════════════════════════════

def cmd_check(plat):
    Log.title("Runtime status")
    all_ok = True

    py = python_binary(plat)
    if not py.exists():
        Log.error(f"Python: missing ({py})"); all_ok = False
    else:
        ok, detail = _verify_python(py)
        (Log.ok if ok else Log.error)(f"Python: {py}" + (f"  ({detail})" if ok else f" - {detail}"))
        if not ok: all_ok = False

    php_cli = php_cli_binary(plat)
    if php_cli.exists():
        Log.ok(f"PHP CLI: {php_cli}")
    else:
        Log.warn(f"PHP CLI: missing ({php_cli})")

    php_cgi = php_cgi_binary(plat)
    if not php_cgi.exists():
        Log.error(f"PHP CGI: missing ({php_cgi})"); all_ok = False
    else:
        ok, detail = _verify_php_cgi(php_cgi)
        if ok:
            Log.ok(f"PHP CGI: {php_cgi}")
            Log.ok(f"  {detail}")
        else:
            Log.error(f"PHP CGI: {php_cgi} - {detail}"); all_ok = False

    ini = RUNTIME / "php" / "php.ini"
    if ini.exists():
        Log.ok(f"php.ini: {ini}")
    else:
        Log.warn(f"php.ini: missing ({ini})")
        Log.warn("  Extensions may not load. Run:")
        Log.warn("      python bootstrap/setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor")

    sqlite = sqlite_binary(plat)
    if sqlite.exists():
        Log.ok(f"SQLite CLI: {sqlite}")
    else:
        Log.warn(f"SQLite CLI: missing ({sqlite})")

    runtime_env = RUNTIME / "runtime.env"
    if runtime_env.exists():
        Log.ok(f"Manifest: {runtime_env}")

    # ── Plugins ──────────────────────────────────────────────────────
    Log.title("Plugin folder")
    if not PLUGINS_DIR.exists():
        Log.warn(f"plugins/: missing ({PLUGINS_DIR})")
        Log.warn("  Run: python bootstrap/setup.py --skip-python --skip-php --skip-sqlite --skip-deps --skip-vendor")
    else:
        Log.ok(f"plugins/: {PLUGINS_DIR}")
        template = PLUGINS_DIR / "_template"
        if template.exists():
            Log.ok(f"  _template/ present")
        else:
            Log.warn(f"  _template/ missing")

        user_plugins = sorted(
            d.name for d in PLUGINS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__"
        )
        if user_plugins:
            Log.ok(f"  {len(user_plugins)} user plugin(s): {', '.join(user_plugins)}")
        else:
            Log.info("  No user plugins installed")

        # Report any plugin with a broken manifest.
        for name in user_plugins:
            manifest_path = PLUGINS_DIR / name / "plugin.json"
            if not manifest_path.exists():
                Log.warn(f"  {name}: no plugin.json (folder will be skipped)")
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                Log.ok(f"  {name} v{data.get('version', '?')} — {data.get('description', '(no description)')}")
            except Exception as exc:
                Log.error(f"  {name}: manifest is not valid JSON ({exc})")

    print()
    if all_ok:
        Log.ok("Runtime is fully installed.")
        return 0
    Log.warn("Runtime is incomplete. Run: python bootstrap/setup.py")
    return 1


# ══════════════════════════════════════════════════════════════════════
#  --list-plugins
# ══════════════════════════════════════════════════════════════════════

def cmd_list_plugins() -> int:
    """
    Print a table of every installed plugin, its version, its
    entry points, and its declared ports.
    """
    Log.title("Installed plugins")

    if not PLUGINS_DIR.exists():
        Log.warn(f"plugins/ folder does not exist at {PLUGINS_DIR}")
        Log.warn("Run: python bootstrap/setup.py --skip-python --skip-php --skip-sqlite --skip-deps --skip-vendor")
        return 1

    plugins = sorted(
        d for d in PLUGINS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_") and d.name != "__pycache__"
    )

    if not plugins:
        Log.info("No user plugins installed.")
        Log.info("Copy plugins/_template/ to start one.")
        return 0

    # Column widths for the table.
    name_w = max(len(p.name) for p in plugins) + 2
    ver_w = 10
    desc_w = 48

    header = (
        f"  {'NAME'.ljust(name_w)}"
        f"{'VERSION'.ljust(ver_w)}"
        f"{'DESCRIPTION'.ljust(desc_w)}"
        f"ENTRY"
    )
    print(Log._c("36", header))
    print(Log._c("36", "  " + "-" * (len(header) - 2)))

    failed = 0
    for plugin_dir in plugins:
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            Log.warn(f"  {plugin_dir.name.ljust(name_w)} (no plugin.json — skipped)")
            failed += 1
            continue

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            Log.error(f"  {plugin_dir.name.ljust(name_w)} invalid JSON: {exc}")
            failed += 1
            continue

        name = data.get("name", plugin_dir.name)
        version = data.get("version", "?")
        description = data.get("description", "")

        # Truncate description.
        if len(description) > desc_w - 2:
            description = description[:desc_w - 4] + "…"

        # Collect entry point names.
        entry = data.get("entry", {}) or {}
        entry_names = [k for k, v in entry.items() if v]
        entry_str = ", ".join(sorted(entry_names)) if entry_names else "(none)"

        # Ports declared.
        ports = data.get("ports", {}) or {}
        if ports:
            entry_str += f"  ports:{','.join(ports.keys())}"

        row = (
            f"  {name.ljust(name_w)}"
            f"{version.ljust(ver_w)}"
            f"{description.ljust(desc_w)}"
            f"{entry_str}"
        )
        print(Log._c("32", row) if name == plugin_dir.name else row)

        # Report a name mismatch.
        if name != plugin_dir.name:
            Log.warn(
                f"    ! plugin.json name '{name}' does not match "
                f"folder '{plugin_dir.name}' — the loader will reject it"
            )
            failed += 1

    print()
    total = len(plugins)
    ok = total - failed
    if failed:
        Log.warn(f"{ok} of {total} plugin(s) are valid. {failed} have errors.")
        return 1
    Log.ok(f"{total} plugin(s) installed, all valid.")
    return 0


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        prog="bootstrap/setup.py",
        description=(
            "3olution Agent Graph — portable runtime bootstrap. "
            "Downloads Python, PHP, and SQLite into runtime/, installs "
            "Python dependencies, and prepares the plugins/ folder."
        ),
    )
    p.add_argument("--force", action="store_true",
                   help="Reinstall every component, even if present")
    p.add_argument("--skip-python", action="store_true",
                   help="Do not install portable Python")
    p.add_argument("--skip-php", action="store_true",
                   help="Do not install PHP")
    p.add_argument("--skip-sqlite", action="store_true",
                   help="Do not install the SQLite CLI")
    p.add_argument("--skip-deps", action="store_true",
                   help="Do not run pip install")
    p.add_argument("--skip-vendor", action="store_true",
                   help="Do not download the LiteGraph vendor files")
    p.add_argument("--skip-plugins", action="store_true",
                   help="Do not create or refresh the plugins/ folder")
    p.add_argument("--check", action="store_true",
                   help="Report the current state and exit")
    p.add_argument("--list-plugins", action="store_true",
                   help="List installed plugins and exit")
    p.add_argument("--verbose", action="store_true",
                   help="Print full tracebacks on unexpected errors")
    return p.parse_args()


def main():
    if sys.version_info < (3, 8):
        sys.stderr.write("Requires Python 3.8+\n"); return 2

    args = parse_args()
    plat = detect_platform()

    Log.banner()
    Log.info(f"Detected platform: {plat['system']}-{plat['arch']}")
    Log.info(f"Project root: {ROOT}")
    Log.info(f"Runtime dir:  {RUNTIME}")

    # ── Short-circuit commands ───────────────────────────────────────
    if args.list_plugins:
        return cmd_list_plugins()

    if args.check:
        return cmd_check(plat)

    # ── Ensure base directories exist ────────────────────────────────
    RUNTIME.mkdir(exist_ok=True)
    DOWNLOADS.mkdir(exist_ok=True)
    ensure_project_dirs()

    results: dict[str, bool | None] = {}

    # ── Install components ───────────────────────────────────────────
    results["Python"] = (
        install_python(plat, force=args.force)
        if not args.skip_python else None
    )
    if args.skip_python:
        Log.warn("Python installation skipped")

    results["PHP"] = (
        install_php(plat, force=args.force)
        if not args.skip_php else None
    )
    if args.skip_php:
        Log.warn("PHP installation skipped")

    results["SQLite"] = (
        install_sqlite(plat, force=args.force)
        if not args.skip_sqlite else None
    )
    if args.skip_sqlite:
        Log.warn("SQLite installation skipped")

    # ── Determine whether Python is available for the next steps ─────
    python_ok = results.get("Python") is True or (
        results.get("Python") is None and python_binary(plat).exists()
    )

    # ── Python dependencies ──────────────────────────────────────────
    if python_ok and not args.skip_deps:
        results["Dependencies"] = install_python_deps(plat)
    elif args.skip_deps:
        Log.warn("Python dependencies skipped")
    else:
        Log.error("Skipping dependencies — Python is not installed.")

    # ── Vendor files ─────────────────────────────────────────────────
    if python_ok and not args.skip_vendor:
        results["LiteGraph"] = download_vendor(plat)
    elif args.skip_vendor:
        Log.warn("LiteGraph vendor download skipped")
    else:
        Log.error("Skipping LiteGraph download — Python is not installed.")

    # ── Plugins ──────────────────────────────────────────────────────
    if not args.skip_plugins:
        results["Plugins"] = install_plugins_folder(force=False)
    else:
        Log.warn("Plugin folder skipped")

    # ── Manifest ─────────────────────────────────────────────────────
    if python_ok:
        write_runtime_env(plat)
    else:
        Log.error("Skipping runtime manifest — Python is not installed.")

    # ── Summary ──────────────────────────────────────────────────────
    Log.title("Summary")
    exit_code = 0
    for name, ok in results.items():
        if ok is True:    Log.ok(f"{name}")
        elif ok is False: Log.error(f"{name}"); exit_code = 1
        else:             Log.warn(f"{name} (skipped)")

    print()
    if exit_code == 0:
        Log.ok("Bootstrap complete.")
        print()
        print("  Next steps:")
        if plat["is_windows"]:
            print("      start.bat")
        else:
            print("      ./start.sh")
        print()
        print("  Add a plugin:")
        print("      Copy plugins/_template/ to plugins/<your_name>/")
        print("      Edit plugin.json, then restart the backend.")
        print()
        print("  List installed plugins:")
        print("      python bootstrap/setup.py --list-plugins")
        print()
    else:
        Log.error("Bootstrap finished with errors.")
        print()
        print("  To retry: python bootstrap/setup.py --force")
        print()

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(); Log.warn("Interrupted."); sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        Log.error(f"Unexpected error: {e}")
        if "--verbose" in sys.argv:
            import traceback; traceback.print_exc()
        sys.exit(1)