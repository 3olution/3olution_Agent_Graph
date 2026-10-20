"""
Lifecycle manager for the llama-server subprocess.
https://3olution.com

Supports two backends, chosen automatically:

  1. NATIVE llama-server.exe (preferred)
     A self-contained C++ binary from the upstream llama.cpp project.
     No Python dependency. No AVX-512 requirement in the CPU build.
     Configured via AGENT_LLAMA_CPP_BINARY, or auto-detected at
     runtime/llama-server/llama-server.exe or on PATH.

  2. PYTHON module (fallback)
     `python -m llama_cpp.server` from the llama-cpp-python package.
     Used only when no native binary is found. This path is what
     fails with 0xC000001D on CPUs without AVX-512.

The manager exposes the same interface in both cases: start, stop,
swap models, embed text, list LoRAs.

================================================================
  WHY THE NATIVE BINARY IS PREFERRED
================================================================

  The prebuilt llama-cpp-python wheels are compiled with AVX-512
  instructions. On a CPU without AVX-512 (Intel 9th gen and older,
  most 10th/11th gen desktop SKUs), the first GGML kernel that runs
  raises STATUS_ILLEGAL_INSTRUCTION (0xC000001D) and the process
  dies. The native llama.cpp binaries are compiled with the baseline
  instruction set (or AVX2, depending on which ZIP you download) and
  run on any modern x64 CPU.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from loguru import logger

from .config import settings
from .models_setup import ensure_models_dir, resolve_model_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTABLE_LLAMA_DIR = PROJECT_ROOT / "runtime" / "llama-server"


# ======================================================================
#  Binary discovery
# ======================================================================

def find_llama_server() -> str | None:
    """
    Return the absolute path to a native llama-server binary, or None.

    Search order:
      1. AGENT_LLAMA_CPP_BINARY (relative paths resolve against the
         project root)
      2. runtime/llama-server/llama-server.exe (portable, project-local)
      3. llama-server.exe on PATH (system-wide install)
    """
    # 1. Explicit configuration
    if settings.llama_cpp_binary:
        p = Path(settings.llama_cpp_binary)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if p.is_file():
            return str(p)
        logger.warning(
            "AGENT_LLAMA_CPP_BINARY points to a non-existent file: {}",
            p,
        )

    # 2. Portable runtime
    for name in ("llama-server.exe", "llama-server"):
        p = PORTABLE_LLAMA_DIR / name
        if p.is_file():
            return str(p)

    # 3. System PATH
    for name in ("llama-server", "llama-server.exe"):
        found = shutil.which(name)
        if found:
            return found

    return None


def python_module_available() -> bool:
    """Return True if llama_cpp.server can be imported in this process."""
    try:
        import llama_cpp.server  # noqa: F401
        return True
    except Exception:
        return False


# ======================================================================
#  LlamaServerManager
# ======================================================================

class LlamaServerManager:
    """
    Manages the llama-server subprocess.

    One instance is created during the FastAPI lifespan and reused
    for every request. Concurrent swaps are serialised by an
    asyncio.Lock.
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.binary_path: str | None = None
        self.backend_kind: str = "none"   # "native" | "python" | "none"
        self.base_url: str = settings.llama_cpp_base_url.rstrip("/")
        self.current_model: str | None = None
        self._current_model_path: Path | None = None
        self._lock = asyncio.Lock()

        self._output_lines: list[str] = []
        self._reader_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    #  Read-only properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def loaded_model(self) -> str | None:
        return self.current_model

    # ------------------------------------------------------------------
    #  Subprocess environment
    # ------------------------------------------------------------------

    def _build_subprocess_env(self, executable: str) -> dict[str, str]:
        """
        Build the environment for the subprocess.

        The executable's own directory is prepended to PATH so sidecar
        DLLs (ggml.dll, llama.dll, ggml-cpu.dll, etc.) are found at
        load time. On Windows, this is the only reliable way to make
        a native binary find its dependencies without registering a
        DLL directory from Python.
        """
        env = os.environ.copy()

        binary_dir = str(Path(executable).parent)
        existing = env.get("PATH", "")
        parts = existing.split(os.pathsep) if existing else []
        if binary_dir not in parts:
            parts.insert(0, binary_dir)
        env["PATH"] = os.pathsep.join(parts)

        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        return env

    # ------------------------------------------------------------------
    #  Command construction
    # ------------------------------------------------------------------

    def _build_native_command(self, binary: str, model_path: Path) -> list[str]:
        """
        Build the command line for native llama-server.exe.

        Flag reference (from llama.cpp's tools/server/README.md):

          -m, --model PATH          model file
          --host HOST               bind host
          --port PORT               bind port
          -c, --ctx-size N          context size
          -ngl, --n-gpu-layers N    GPU layers to offload
          --alias NAME              model ID advertised in /v1/models
          --api-key KEY             optional bearer token
          --embeddings              enable /v1/embeddings
          --pooling mean            pooling strategy for embeddings
          --lora PATH               load a LoRA adapter (repeatable)
        """
        cmd: list[str] = [
            binary,
            "-m", str(model_path),
            "--host", settings.llama_cpp_host,
            "--port", str(settings.llama_cpp_port),
            "-c", str(settings.llama_cpp_n_ctx),
            "-ngl", str(settings.llama_cpp_n_gpu_layers),
            "--alias", settings.llama_cpp_alias,
        ]

        if settings.llama_cpp_api_key:
            cmd += ["--api-key", settings.llama_cpp_api_key]

        if settings.llama_cpp_embedding:
            cmd += ["--embeddings", "--pooling", "mean"]

        for spec in settings.llama_cpp_lora_list:
            parts = spec.split("=", 2)
            if len(parts) >= 2:
                cmd += ["--lora", parts[1]]

        return cmd

    def _build_python_command(self, model_path: Path) -> list[str]:
        """
        Build the command line for `python -m llama_cpp.server`.

        Only used when no native binary is available. This is the path
        that crashes with 0xC000001D on CPUs without AVX-512 if the
        installed wheel requires AVX-512.
        """
        cmd: list[str] = [
            sys.executable,
            "-m", "llama_cpp.server",
            "--model", str(model_path),
            "--host", settings.llama_cpp_host,
            "--port", str(settings.llama_cpp_port),
            "--n_ctx", str(settings.llama_cpp_n_ctx),
            "--n_gpu_layers", str(settings.llama_cpp_n_gpu_layers),
            "--model_alias", settings.llama_cpp_alias,
        ]

        if settings.llama_cpp_chat_format:
            cmd += ["--chat_format", settings.llama_cpp_chat_format]

        if settings.llama_cpp_api_key:
            cmd += ["--api_key", settings.llama_cpp_api_key]

        if settings.llama_cpp_embedding:
            cmd += ["--embedding"]

        return cmd

    # ------------------------------------------------------------------
    #  Start
    # ------------------------------------------------------------------

    async def start(self, model_path: Path | None = None) -> bool:
        """Start the llama-server. Returns True on success."""
        if not settings.llama_cpp_enabled:
            logger.info("llama-cpp server disabled via config")
            return False

        if self.is_running and await self.is_ready():
            logger.info(
                "llama-cpp server already running with model '{}'",
                self.current_model or "(unknown)",
            )
            return True

        # -- Resolve the model path -----------------------------------
        ensure_models_dir()

        if model_path is None:
            if self._current_model_path is not None:
                model_path = self._current_model_path
            else:
                model_path = resolve_model_path(settings.llama_cpp_model)

        if model_path is None or not model_path.exists():
            logger.error(
                "llama-cpp: model file not found: {}\n"
                "  Looked in: {}\n"
                "  Fix: drop a .gguf file in the models folder, set "
                "AGENT_LLAMA_CPP_MODEL to its filename, or set "
                "AGENT_LLAMA_CPP_ENABLED=false to skip llama-cpp.",
                settings.llama_cpp_model,
                settings.models_path,
            )
            return False

        file_size = model_path.stat().st_size
        if file_size < 1024 * 1024:
            logger.error(
                "llama-cpp: model file looks too small ({} bytes): {}",
                file_size, model_path,
            )
            return False

        # -- Choose backend -------------------------------------------
        binary = find_llama_server()

        if binary is not None:
            self.backend_kind = "native"
            self.binary_path = binary
            cmd = self._build_native_command(binary, model_path)
            env = self._build_subprocess_env(binary)
            logger.info(
                "Spawning llama-server (native, model size {:.2f} GB, "
                "port {}, alias '{}')",
                file_size / 1e9,
                settings.llama_cpp_port,
                settings.llama_cpp_alias,
            )
            logger.debug("Command: {}", " ".join(cmd))
        elif python_module_available():
            self.backend_kind = "python"
            self.binary_path = sys.executable
            cmd = self._build_python_command(model_path)
            env = self._build_subprocess_env(sys.executable)
            logger.warning(
                "No native llama-server binary found. Falling back to "
                "the Python module. If the process crashes with "
                "0xC000001D, install the AVX2 wheel:\n"
                "    runtime\\python\\python.exe -m pip install "
                "llama-cpp-python --prefer-binary --extra-index-url "
                "https://jllllll.github.io/llama-cpp-python-cuBLAS-wheels/AVX2/cpu\n"
                "Or download a native binary from "
                "https://github.com/ggml-org/llama.cpp/releases and "
                "extract into runtime/llama-server/."
            )
            logger.debug("Command: {}", " ".join(cmd))
        else:
            logger.error(
                "llama-cpp: no backend available.\n"
                "  Either:\n"
                "    1. Download a native llama-server binary from\n"
                "       https://github.com/ggml-org/llama.cpp/releases\n"
                "       and extract into runtime/llama-server/\n"
                "    2. Install llama-cpp-python:\n"
                "       pip install llama-cpp-python --prefer-binary \\\n"
                "         --extra-index-url "
                "https://jllllll.github.io/llama-cpp-python-cuBLAS-wheels/AVX2/cpu"
            )
            return False

        # -- Record state ---------------------------------------------
        self._current_model_path = model_path
        self.current_model = model_path.name
        self._output_lines = []

        # -- Spawn ----------------------------------------------------
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            logger.error(
                "llama-server could not launch: {}\n"
                "  Verify the binary exists at: {}",
                exc, binary,
            )
            self.proc = None
            return False
        except Exception as exc:
            logger.exception("llama-server launch failed: {}", exc)
            self.proc = None
            return False

        # -- Capture output -------------------------------------------
        self._reader_thread = threading.Thread(
            target=self._read_output,
            args=(self.proc,),
            daemon=True,
            name="llama-server-log-reader",
        )
        self._reader_thread.start()

        # -- Wait for readiness ---------------------------------------
        deadline = time.monotonic() + settings.llama_cpp_startup_timeout

        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                if self._reader_thread is not None:
                    self._reader_thread.join(timeout=2.0)
                self._log_subprocess_failure()
                self.proc = None
                return False

            if await self.is_ready():
                logger.info(
                    "llama-server ready at {} "
                    "(model={}, backend={}, alias={})",
                    self.base_url,
                    self.current_model,
                    self.backend_kind,
                    settings.llama_cpp_alias,
                )
                return True

            await asyncio.sleep(0.5)

        logger.error(
            "llama-server did not become ready within {}s.",
            settings.llama_cpp_startup_timeout,
        )
        await self.stop()
        return False

    # ------------------------------------------------------------------
    #  Output capture
    # ------------------------------------------------------------------

    def _read_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                self._output_lines.append(line)
                if len(self._output_lines) > 500:
                    self._output_lines = self._output_lines[-500:]
                logger.debug("[llama-server] {}", line)
        except Exception:
            pass

    def _log_subprocess_failure(self) -> None:
        if self.proc is None:
            return

        rc = self.proc.returncode
        lines = self._output_lines[-30:] if self._output_lines else []

        if not lines:
            logger.error(
                "llama-server exited with code {} and produced no "
                "output.\n"
                "  Binary: {}\n"
                "  Run it manually to see the error:\n"
                "      \"{}\" --help",
                rc,
                self.binary_path or "(unknown)",
                self.binary_path or "llama-server",
            )
            return

        logger.error(
            "llama-server exited with code {} - last {} lines:\n{}",
            rc,
            len(lines),
            "\n".join(f"    {ln}" for ln in lines),
        )

    # ------------------------------------------------------------------
    #  Stop
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        if self.proc is None:
            return

        logger.info("Stopping llama-server...")

        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

        self.proc = None

    # ------------------------------------------------------------------
    #  Model swap
    # ------------------------------------------------------------------

    async def load_model(self, filename: str) -> dict:
        if not settings.llama_cpp_allow_runtime_swap:
            return {
                "ok": False, "filename": filename, "path": None,
                "error": (
                    "Runtime model swapping is disabled. Set "
                    "AGENT_LLAMA_CPP_ALLOW_RUNTIME_SWAP=true."
                ),
                "previous": self.current_model,
            }

        async with self._lock:
            model_path = resolve_model_path(filename)
            if model_path is None:
                return {
                    "ok": False, "filename": filename, "path": None,
                    "error": (
                        f"GGUF file not found: {filename} "
                        f"(looked in {settings.models_path})"
                    ),
                    "previous": self.current_model,
                }

            previous = self.current_model
            if self._current_model_path == model_path and self.is_running:
                return {
                    "ok": True, "filename": filename,
                    "path": str(model_path), "error": None,
                    "previous": previous,
                }

            logger.info(
                "Swapping llama-cpp model: {} -> {}", previous, filename,
            )
            await self.stop()
            self._current_model_path = model_path
            self.current_model = model_path.name

            ok = await self.start(model_path=model_path)
            if not ok:
                return {
                    "ok": False, "filename": filename,
                    "path": str(model_path),
                    "error": (
                        "Server failed to start with this model. "
                        "Check the backend log for the subprocess output."
                    ),
                    "previous": previous,
                }
            return {
                "ok": True, "filename": filename,
                "path": str(model_path), "error": None,
                "previous": previous,
            }

    async def unload(self) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    #  Readiness check
    # ------------------------------------------------------------------

    async def is_ready(self) -> bool:
        """
        True if the server answers /v1/models.

        Both the native binary and the Python module expose this
        endpoint, so a single check covers both backends.
        """
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.base_url}/v1/models")
                return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  LoRA adapters
    # ------------------------------------------------------------------

    async def list_loras(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/lora-adapters")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return []

    async def set_lora_scale(self, lora_path: str, scale: float) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{self.base_url}/lora-adapters",
                    json=[{"id": lora_path, "scale": scale}],
                )
                return r.status_code in (200, 204)
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  Embeddings
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {"input": texts, "model": settings.llama_cpp_alias}
        headers: dict[str, str] = {}
        if settings.llama_cpp_api_key:
            headers["Authorization"] = f"Bearer {settings.llama_cpp_api_key}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{self.base_url}/v1/embeddings",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()

        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]


# ======================================================================
#  Module-level singleton
# ======================================================================

manager: LlamaServerManager | None = None


def get_manager() -> LlamaServerManager | None:
    return manager