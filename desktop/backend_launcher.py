"""
Backend process launcher for the desktop client.
https://3olution.com

Responsibilities:

  1. Detect whether a backend is already running at the configured
     address. If one is found, attach to it instead of spawning a
     duplicate.

  2. Otherwise, spawn `scripts/serve.py` as a subprocess using the
     correct interpreter (the .venv or portable runtime), and
     monitor it for readiness.

  3. Stream the subprocess's stdout and stderr to the desktop
     client's log dock and to logs/backend-*.log.

  4. Handle graceful shutdown: on close, terminate the subprocess
     tree (taskkill /T on Windows, SIGTERM + SIGKILL on Unix).

================================================================
  SIGNALS
================================================================

  The launcher emits Qt signals:

      started()      — the backend is ready to serve requests
      stopped()      — the subprocess has exited cleanly
      failed(str)    — startup failed with the given message
      log(str)       — one line of subprocess output

  The main window connects to these to update its status bar and
  log dock.

================================================================
  THREADING MODEL
================================================================

  The launcher runs on the Qt event loop's thread (the main GUI
  thread), because it uses Qt's QTimer for polling readiness.

  Subprocess output is read on a background threading.Thread, and
  that thread emits `log` signals. Qt signals are safe to emit
  from any thread — the receiving slot runs on the thread that the
  QObject lives on.

================================================================
  INTERPRETER RESOLUTION
================================================================

  The subprocess must run with an interpreter that has the backend
  dependencies installed. The resolution order is:

    1. .venv/Scripts/python.exe   (Windows)
       .venv/bin/python            (Unix)

    2. sys.executable — the interpreter running the desktop client.

  The first path exists when the developer ran `python run.py setup`.
  The second is a fallback that works in packaged builds where the
  interpreter is bundled inside the PyInstaller archive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


# ══════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════

# How long the launcher waits for the backend to answer /api/health.
# Large cold-load models can take a while to warm up, but the health
# endpoint responds as soon as uvicorn is up — long before the LLM
# is warm. 60 s is generous.
STARTUP_TIMEOUT_SECONDS = 60.0

# How often the readiness poll fires.
HEALTH_POLL_INTERVAL_MS = 500

# Timeout for individual health probes. Kept short so a hung probe
# does not delay the whole startup.
HEALTH_REQUEST_TIMEOUT = 1.5

# Timeout for the graceful stop-all API call.
STOP_ALL_REQUEST_TIMEOUT = 5.0

# How long to wait between terminating the subprocess and force-
# killing it if it does not exit on its own.
GRACEFUL_STOP_TIMEOUT = 5.0


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _venv_python(project_root: Path) -> Path | None:
    """
    Return the path to the venv Python if a venv exists, else None.
    """
    if sys.platform == "win32":
        candidate = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = project_root / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def _resolve_interpreter(project_root: Path) -> str:
    """
    Return the interpreter to run the backend with.

    Prefers the venv Python; falls back to the interpreter running
    this process.
    """
    venv = _venv_python(project_root)
    if venv is not None:
        return str(venv)
    return sys.executable


def _check_health(url: str) -> bool:
    """
    Return True if the backend at `url` answers /api/health with 200.

    Never raises — any failure mode is reported as False.
    """
    try:
        r = httpx.get(f"{url}/api/health", timeout=HEALTH_REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _terminate_tree(proc: subprocess.Popen) -> None:
    """
    Terminate a subprocess and all of its children.

    On Windows, uses taskkill /T because uvicorn spawns child
    processes for reload mode and for the MCP server. Plain
    terminate() leaves orphans.

    On Unix, uses SIGTERM first, escalates to SIGKILL after the
    graceful timeout.
    """
    if proc.poll() is not None:
        return

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=GRACEFUL_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


# ══════════════════════════════════════════════════════════════════════
#  BackendLauncher
# ══════════════════════════════════════════════════════════════════════

class BackendLauncher(QObject):
    """
    Spawns and monitors the backend.

    Usage:

        launcher = BackendLauncher(project_root=Path("."))
        launcher.started.connect(on_started)
        launcher.failed.connect(on_failed)
        launcher.log.connect(on_log)
        launcher.start()
    """

    # ── Signals ──────────────────────────────────────────────────────
    started = pyqtSignal()
    stopped = pyqtSignal()
    failed = pyqtSignal(str)
    log = pyqtSignal(str)

    # ── Lifecycle ────────────────────────────────────────────────────

    def __init__(
        self,
        project_root: Path,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        super().__init__()
        self.project_root = project_root.resolve()
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"

        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._ready_timer: QTimer | None = None
        self._health_deadline: float = 0.0
        self._stopping = False

        # True if WE started the process. False if we attached to one
        # that was already running. Used to decide whether to stop the
        # backend on close.
        self.spawned_by_us = False

    def is_running(self) -> bool:
        """True if the subprocess we started is still alive."""
        return self._proc is not None and self._proc.poll() is None

    # ── Start ────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Attach to an existing backend, or spawn a new one.

        The attach path is important during development: the user
        usually has `uvicorn --reload` running in a terminal and
        launches the desktop client to check the UI. Detecting the
        running backend avoids a port conflict and avoids the
        confusion of two backends fighting over the same SQLite
        file.
        """
        # Already running? Emit started and return.
        if self.is_running():
            self.started.emit()
            return

        # Health probe — attach if a backend is up.
        if _check_health(self.url):
            self.log.emit(
                f"Backend already running at {self.url} — attaching."
            )
            self.spawned_by_us = False
            self.started.emit()
            return

        # Otherwise spawn.
        self._spawn()

    # ── Stop ─────────────────────────────────────────────────────────

    def stop(self) -> None:
        """
        Terminate the subprocess (if we started it).

        Called on application close. If we attached to an external
        backend, does nothing to it — the user manages that backend
        themselves.
        """
        self._stopping = True

        if self._ready_timer is not None:
            self._ready_timer.stop()
            self._ready_timer = None

        if self._proc is None:
            return

        if not self.spawned_by_us:
            self._proc = None
            self.stopped.emit()
            return

        self.log.emit("Stopping backend…")
        _terminate_tree(self._proc)

        if (
            self._reader_thread is not None
            and self._reader_thread.is_alive()
        ):
            self._reader_thread.join(timeout=2.0)

        self._proc = None
        self.spawned_by_us = False
        self.stopped.emit()

    async def stop_all_async(self) -> None:
        """
        Call the backend's /api/system/stop-all endpoint, then
        ensure the process is gone.

        The desktop client's Stop All menu action uses this instead
        of `stop()`, because it triggers a coordinated shutdown of
        the MCP subprocess and the llama-cpp server as well.
        """
        self._stopping = True

        # Ask the backend to shut down cleanly.
        try:
            async with httpx.AsyncClient(
                timeout=STOP_ALL_REQUEST_TIMEOUT,
            ) as client:
                await client.post(f"{self.url}/api/system/stop-all")
            self.log.emit("Backend acknowledged stop-all request.")
        except Exception as exc:
            self.log.emit(
                f"Stop-all API call failed ({exc}); "
                f"falling back to kill."
            )

        # Give the backend a moment to run its shutdown hook.
        import asyncio
        await asyncio.sleep(1.2)

        # If it is still alive, kill the tree.
        if self._proc is not None and self._proc.poll() is None:
            self.log.emit("Backend still alive — killing process tree.")
            _terminate_tree(self._proc)

        if self._ready_timer is not None:
            self._ready_timer.stop()
            self._ready_timer = None

        self._proc = None
        self.spawned_by_us = False
        self.stopped.emit()

    # ── Spawn ────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        """
        Launch the backend subprocess.

        Prefers scripts/serve.py (which handles the Windows event
        loop policy and sys.path fixes) but falls back to a direct
        uvicorn invocation if serve.py is missing.
        """
        serve_py = self.project_root / "scripts" / "serve.py"
        interpreter = _resolve_interpreter(self.project_root)

        if serve_py.exists():
            cmd = [interpreter, str(serve_py)]
        else:
            cmd = [
                interpreter, "-m", "uvicorn",
                "backend.main:app",
                "--host", self.host,
                "--port", str(self.port),
            ]

        # Environment for the subprocess.
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["AGENT_HOST"] = self.host
        env["AGENT_PORT"] = str(self.port)
        env["AGENT_RELOAD"] = "0"  # no reload in a bundled desktop app

        # Make `backend` importable from any working directory.
        root_str = str(self.project_root)
        existing = env.get("PYTHONPATH", "")
        if root_str not in existing.split(os.pathsep):
            env["PYTHONPATH"] = (
                root_str + (os.pathsep + existing if existing else "")
            )

        self.log.emit(f"Launching backend: {' '.join(cmd)}")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            self.failed.emit(f"Could not launch backend: {exc}")
            return
        except Exception as exc:
            self.failed.emit(f"Could not launch backend: {exc}")
            return

        self.spawned_by_us = True
        self._stopping = False

        # Start the log reader thread.
        self._reader_thread = threading.Thread(
            target=self._read_output,
            args=(self._proc,),
            daemon=True,
            name="backend-log-reader",
        )
        self._reader_thread.start()

        # Start the readiness poll.
        self._health_deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        self._ready_timer = QTimer(self)
        self._ready_timer.timeout.connect(self._poll_health)
        self._ready_timer.start(HEALTH_POLL_INTERVAL_MS)

    # ── Output reading ───────────────────────────────────────────────

    def _read_output(self, proc: subprocess.Popen) -> None:
        """
        Read the subprocess's stdout and emit each line as a signal.

        Runs in a daemon thread. When the process exits, emits
        `failed` if the exit was unexpected.
        """
        try:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line:
                    self.log.emit(line)
        except Exception:
            pass
        finally:
            try:
                rc = proc.wait(timeout=5)
            except Exception:
                rc = -1
            if not self._stopping and self.spawned_by_us:
                self.failed.emit(
                    f"Backend exited unexpectedly (exit code {rc})"
                )

    # ── Readiness poll ───────────────────────────────────────────────

    def _poll_health(self) -> None:
        """
        Poll /api/health until the backend responds or the timeout
        expires.
        """
        # If the process died, stop polling — the reader thread will
        # emit a `failed` signal.
        if self._proc is not None and self._proc.poll() is not None:
            if self._ready_timer is not None:
                self._ready_timer.stop()
                self._ready_timer = None
            return

        if _check_health(self.url):
            if self._ready_timer is not None:
                self._ready_timer.stop()
                self._ready_timer = None
            self.log.emit(f"Backend is ready at {self.url}")
            self.started.emit()
            return

        if time.monotonic() > self._health_deadline:
            if self._ready_timer is not None:
                self._ready_timer.stop()
                self._ready_timer = None
            self.failed.emit(
                f"Backend did not become ready within "
                f"{int(STARTUP_TIMEOUT_SECONDS)} seconds. "
                f"Check the backend log for details."
            )
            self.stop()