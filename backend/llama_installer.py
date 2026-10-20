"""
Automatic installer for llama-cpp-python[server].
https://3olution.com

The llama-cpp-python package is not on PyPI as a pure-Python wheel.
It has a native extension that must be built against the local Python
AND the local GPU stack. Building from source requires a C++ compiler
and takes several minutes. To avoid that, the project ships prebuilt
wheels for common configurations:

    cpu     — CPU only
    cu118   — CUDA 11.8 (GTX 10/16, RTX 20)
    cu121   — CUDA 12.1 (older 12.x drivers)
    cu124   — CUDA 12.4 (RTX 30/40, Ada/Hopper)

This module:

  1. Detects whether llama-cpp-python is already installed.
  2. Detects the local CUDA version via nvidia-smi.
  3. Recommends the correct wheel index for this machine.
  4. Streams the pip install output as SSE so the UI can show
     progress live.
  5. Streams an uninstall for users who want to switch wheels.

================================================================
  WINDOWS CAVEAT
================================================================

  On Windows, the native DLLs of llama-cpp-python are locked while
  the package is loaded in a running process. Upgrading or
  reinstalling requires the backend to be stopped first.

  The installer detects this situation and includes a note in the
  SSE stream telling the user to restart. It does not attempt to
  kill the process itself — that would be surprising.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from typing import AsyncGenerator

from loguru import logger


# ══════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════

# The package spec installed from the wheel index.
PACKAGE_SPEC = "llama-cpp-python[server]>=0.3.0"

# Base URL for the pre-built wheel index.
WHEEL_INDEX_BASE = "https://abetlen.github.io/llama-cpp-python/whl"

# Valid wheel indices, ordered roughly by frequency of use.
VALID_WHEEL_INDICES = {"cpu", "cu118", "cu121", "cu124"}

# Human-readable labels for the UI. The order here is the order the
# frontend displays them in.
WHEEL_INDEX_LABELS = {
    "cpu":   "CPU only (no GPU acceleration)",
    "cu124": "CUDA 12.4 (RTX 30/40, Ada/Hopper)",
    "cu121": "CUDA 12.1 (older 12.x drivers)",
    "cu118": "CUDA 11.8 (GTX 10/16, RTX 20)",
}


# ══════════════════════════════════════════════════════════════════════
#  Environment detection
# ══════════════════════════════════════════════════════════════════════

def in_venv() -> bool:
    """Return True if the current interpreter is running in a venv."""
    return sys.prefix != sys.base_prefix


def venv_path():
    """Return the venv root directory, or None if not in a venv."""
    from pathlib import Path
    if not in_venv():
        return None
    return Path(sys.prefix)


def detect_llama_cpp() -> dict:
    """
    Return the current install state of llama-cpp-python.

    Fields:
        installed       bool
        version         str | None
        gpu_offload     bool | None  (True if this build supports GPU)
        error           str | None
    """
    try:
        import llama_cpp
        version = getattr(llama_cpp, "__version__", "unknown")

        # Try to determine whether this build has GPU offload support.
        # Older builds do not expose the native module; in that case
        # we report None (unknown) rather than guessing.
        gpu: bool | None
        try:
            from llama_cpp import llama_cpp as _native
            gpu = bool(_native.llama_supports_gpu_offload())
        except Exception:
            gpu = None

        return {
            "installed": True,
            "version": str(version),
            "gpu_offload": gpu,
            "error": None,
        }
    except ImportError:
        return {
            "installed": False,
            "version": None,
            "gpu_offload": None,
            "error": None,
        }
    except Exception as exc:
        return {
            "installed": False,
            "version": None,
            "gpu_offload": None,
            "error": str(exc),
        }


def detect_nvidia_cuda() -> str | None:
    """
    Run `nvidia-smi` and parse the CUDA version.

    Returns e.g. "12.4", or None if nvidia-smi is not installed or
    does not report a CUDA version in its output.

    The parse looks for a line containing "CUDA Version:" — the
    exact format of nvidia-smi output varies by driver version but
    that string has been stable for a decade.
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None

    try:
        result = subprocess.run(
            [nvidia_smi],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "CUDA Version:" in line:
                m = re.search(r"CUDA Version:\s*([0-9]+\.[0-9]+)", line)
                if m:
                    return m.group(1)
    except Exception as exc:
        logger.debug("nvidia-smi failed: {}", exc)

    return None


def recommend_wheel_index() -> tuple[str, str]:
    """
    Return (index, reason) for the best wheel on this machine.

    index is one of: cpu, cu118, cu121, cu124.
    reason is a human-readable explanation for the UI.
    """
    cuda = detect_nvidia_cuda()

    if cuda is None:
        return "cpu", "No NVIDIA GPU detected — using CPU build."

    try:
        major_str, minor_str = cuda.split(".")[:2]
        major = int(major_str)
        minor = int(minor_str)
    except Exception:
        return "cpu", f"Could not parse CUDA version '{cuda}' — using CPU build."

    if major >= 12 and minor >= 4:
        return "cu124", f"CUDA {cuda} detected — using CUDA 12.4 build."
    if major >= 12:
        return "cu121", f"CUDA {cuda} detected — using CUDA 12.1 build."
    if major == 11:
        return "cu118", f"CUDA {cuda} detected — using CUDA 11.8 build."

    return "cpu", f"CUDA {cuda} is too old — using CPU build."


# ══════════════════════════════════════════════════════════════════════
#  pip command construction
# ══════════════════════════════════════════════════════════════════════

def build_pip_command(wheel_index: str, upgrade: bool = False) -> list[str]:
    """
    Return the pip install command for the given wheel index.

    Raises ValueError for an unknown wheel index.
    """
    if wheel_index not in VALID_WHEEL_INDICES:
        raise ValueError(f"Invalid wheel index: {wheel_index}")

    cmd = [
        sys.executable, "-m", "pip", "install",
        PACKAGE_SPEC,
        "--extra-index-url", f"{WHEEL_INDEX_BASE}/{wheel_index}",
    ]
    if upgrade:
        cmd.append("--upgrade")
    return cmd


# ══════════════════════════════════════════════════════════════════════
#  Installation streaming
# ══════════════════════════════════════════════════════════════════════

async def stream_install(
    wheel_index: str,
    upgrade: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Run `pip install llama-cpp-python[server]` and yield progress.

    Events:
        {"type": "start",   "command": "...", "wheel_index": "...", "venv": "..."}
        {"type": "line",    "text": "..."}          — one pip output line
        {"type": "success", "version": "...", "gpu_offload": bool, "restart_required": true}
        {"type": "error",   "message": "..."}
        {"type": "done"}
    """
    try:
        cmd = build_pip_command(wheel_index, upgrade=upgrade)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        yield {"type": "done"}
        return

    yield {
        "type": "start",
        "command": " ".join(cmd),
        "wheel_index": wheel_index,
        "venv": str(venv_path()) if in_venv() else "(system Python)",
    }

    # Build a clean environment for pip.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    # Hide the console window on Windows.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:
        yield {"type": "error", "message": f"Could not start pip: {exc}"}
        yield {"type": "done"}
        return

    # Stream pip's output line by line.
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line:
            yield {"type": "line", "text": line}

    exit_code = await proc.wait()

    if exit_code == 0:
        state = detect_llama_cpp()
        yield {
            "type": "success",
            "version": state.get("version"),
            "gpu_offload": state.get("gpu_offload"),
            "restart_required": True,
        }
    else:
        yield {
            "type": "error",
            "message": (
                f"pip exited with code {exit_code}. "
                f"See the log above for details."
            ),
        }

    yield {"type": "done"}


async def stream_uninstall() -> AsyncGenerator[dict, None]:
    """
    Uninstall llama-cpp-python.

    Used before switching wheel variants (e.g. from CPU to CUDA). On
    Windows, the backend must be stopped and restarted between the
    uninstall and the subsequent install, otherwise the pip process
    cannot delete the DLLs still mapped into the running Python.

    Events match stream_install, minus the wheel_index field.
    """
    cmd = [
        sys.executable, "-m", "pip", "uninstall", "-y",
        "llama-cpp-python",
    ]

    yield {"type": "start", "command": " ".join(cmd)}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
    except Exception as exc:
        yield {"type": "error", "message": f"Could not start pip: {exc}"}
        yield {"type": "done"}
        return

    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line:
            yield {"type": "line", "text": line}

    exit_code = await proc.wait()

    if exit_code == 0:
        yield {"type": "success", "restart_required": True}
    else:
        yield {
            "type": "error",
            "message": f"pip exited with code {exit_code}",
        }

    yield {"type": "done"}