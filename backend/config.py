"""
Application configuration loaded from .env via pydantic-settings.
https://3olution.com

Design notes:
  - Primary LLM defaults to a local Ollama instance.
  - A secondary "fallback" LLM can be configured for redundancy.
  - A locally-managed llama-cpp-python server is available as a third
    provider for GGUF files, spawned as a subprocess.
  - The MCP server command defaults to the interpreter running the
    backend (sys.executable). This prevents the subprocess from
    picking up a system Python that lacks the `mcp` package.
  - PHP is optional. When no PHP binary is found, the /php/ app is
    disabled and the rest of the application continues normally.
  - The `models/`, `logs/`, and `php_app/data/` paths are exposed as
    absolute `Path` objects via the `*_path` properties. Bare
    directory names in .env resolve against the project root;
    absolute paths pass through unchanged.

IMPORTANT - FILE ENCODING
  This file uses ASCII-only comments. Earlier versions used Unicode
  box-drawing characters in section headers; those broke when copied
  through editors that saved as Windows-1252. Every comment below is
  plain ASCII so the file is safe in any encoding.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# ======================================================================
#  Module constants
# ======================================================================

# backend/config.py -> project root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The interpreter that runs the backend. Used as the default MCP
# server command so the subprocess always shares the same venv.
_DEFAULT_MCP_COMMAND = sys.executable if sys.executable else "python"


# ======================================================================
#  Settings
# ======================================================================

class Settings(BaseSettings):
    """
    All runtime configuration.

    Every field maps to an `AGENT_*` variable in .env. Fields with a
    default value work without a .env file at all; the application
    boots against a local Ollama instance with the default model.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AGENT_",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    #  Uvicorn
    # ------------------------------------------------------------------
    uvicorn_access_log: bool = False

    # ------------------------------------------------------------------
    #  Primary LLM (Ollama by default)
    # ------------------------------------------------------------------
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.2"
    llm_temperature: float = 0.0
    llm_timeout: float = 120.0
    llm_warmup: bool = True

    # ------------------------------------------------------------------
    #  Fallback LLM (optional external endpoint)
    # ------------------------------------------------------------------
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_fallback_timeout: float = 120.0

    # ------------------------------------------------------------------
    #  Tool use
    # ------------------------------------------------------------------
    disable_tools: bool = False
    no_tools_models: str = ""

    # ------------------------------------------------------------------
    #  Reasoning / thinking
    # ------------------------------------------------------------------
    show_thinking: bool = True
    ollama_unload_on_switch: bool = True

    # ------------------------------------------------------------------
    #  Ollama management API
    # ------------------------------------------------------------------
    ollama_host: str = "http://127.0.0.1:11434"

    # ------------------------------------------------------------------
    #  llama-cpp-python server
    # ------------------------------------------------------------------
    llama_cpp_enabled: bool = True
    llama_cpp_host: str = "127.0.0.1"
    llama_cpp_port: int = 8080
    llama_cpp_base_url: str = "http://127.0.0.1:8080"
    llama_cpp_api_key: str = ""
    llama_cpp_model: str = "Bonsai-8B.gguf"
    llama_cpp_n_ctx: int = 8192
    llama_cpp_n_gpu_layers: int = -1
    llama_cpp_chat_format: str = "chatml"
    llama_cpp_startup_timeout: float = 360.0
    llama_cpp_embedding: bool = False
    llama_cpp_embedding_model: str = "bge-m3-Q8_0.gguf"
    llama_cpp_loras: str = ""
    llama_cpp_allow_runtime_swap: bool = True
    llama_cpp_keep_alive: bool = True
    # Path to the native llama-server binary. Empty means auto-detect:
    #   1. runtime/llama-server/llama-server.exe
    #   2. llama-server.exe on PATH
    llama_cpp_binary: str = ""
    # Model ID the server advertises and the backend sends. Must match
    # the --alias passed to llama-server.exe.
    llama_cpp_alias: str = "local"

    # ------------------------------------------------------------------
    #  PHP built-in server
    # ------------------------------------------------------------------
    php_enabled: bool = True
    php_binary: str = ""
    php_timeout: float = 60.0

    # ------------------------------------------------------------------
    #  SQL backend for PHP
    # ------------------------------------------------------------------
    php_db_driver: str = "sqlite"
    php_sqlite_path: str = "data/app.db"
    php_db_host: str = "127.0.0.1"
    php_db_port: int = 3306
    php_db_name: str = "app"
    php_db_user: str = "app"
    php_db_password: str = ""

    # ------------------------------------------------------------------
    #  Models directory
    # ------------------------------------------------------------------
    models_dir: str = "models"

    # ------------------------------------------------------------------
    #  Logging
    # ------------------------------------------------------------------
    logs_dir: str = "logs"
    log_file_level: str = "DEBUG"
    log_retention_days: int = 14
    log_to_file: bool = True

    # ------------------------------------------------------------------
    #  Server
    # ------------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "*"

    # ------------------------------------------------------------------
    #  MCP
    # ------------------------------------------------------------------
    mcp_server_command: str = _DEFAULT_MCP_COMMAND
    mcp_server_args: str = "-m mcp_server.server"
    tool_timeout: float = 60.0

    # ------------------------------------------------------------------
    #  Agent loop
    # ------------------------------------------------------------------
    max_iterations: int = 10

    # ------------------------------------------------------------------
    #  Streaming
    # ------------------------------------------------------------------
    stream_tokens: bool = True
    stream_stall_timeout: float = 360.0

    # ------------------------------------------------------------------
    #  Retry
    # ------------------------------------------------------------------
    retry_attempts: int = 3
    retry_backoff: float = 1.0

    # ------------------------------------------------------------------
    #  Storage
    # ------------------------------------------------------------------
    session_db: str = "sessions.db"
    max_graph_runs: int = 100

    # ------------------------------------------------------------------
    #  Custom nodes
    # ------------------------------------------------------------------
    load_custom_nodes: bool = True

    # ------------------------------------------------------------------
    #  Desktop
    # ------------------------------------------------------------------
    desktop_backend: str = "http://127.0.0.1:8000"

    # ==================================================================
    #  Derived properties
    # ==================================================================

    @property
    def project_root(self) -> Path:
        """Absolute path to the project root."""
        return PROJECT_ROOT

    @property
    def models_path(self) -> Path:
        """
        Absolute path to the models folder.

        A bare directory name resolves against the project root. An
        absolute path passes through unchanged.
        """
        p = Path(self.models_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def logs_path(self) -> Path:
        """Absolute path to the logs folder."""
        p = Path(self.logs_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def mcp_args_list(self) -> list[str]:
        """
        Parse AGENT_MCP_SERVER_ARGS into a list.

        Uses shlex.split() with posix=False on Windows so that
        backslashes in paths survive parsing.
        """
        text = self.mcp_server_args.strip()
        if not text:
            return []
        try:
            return shlex.split(text, posix=(sys.platform != "win32"))
        except ValueError as exc:
            from loguru import logger
            logger.warning(
                "shlex.split failed on AGENT_MCP_SERVER_ARGS ({}); "
                "falling back to whitespace split",
                exc,
            )
            return text.split()

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fallback_enabled(self) -> bool:
        """True if a fallback LLM endpoint has been configured."""
        return bool(self.llm_fallback_base_url.strip())

    @property
    def primary_is_placeholder(self) -> bool:
        """Detect the 'default OpenAI URL + placeholder key' trap."""
        return (
            "api.openai.com" in self.llm_base_url
            and self.llm_api_key.strip() in ("", "not-needed")
        )

    @property
    def no_tools_model_list(self) -> list[str]:
        """Parse AGENT_NO_TOOLS_MODELS into a list of model names."""
        if not self.no_tools_models:
            return []
        return [
            m.strip() for m in self.no_tools_models.split(",") if m.strip()
        ]

    @property
    def llama_cpp_lora_list(self) -> list[str]:
        """
        Parse AGENT_LLAMA_CPP_LORAS into a list of specs.

        Each spec is either 'name=path' or 'name=path=scale'.
        """
        if not self.llama_cpp_loras:
            return []
        return [
            s.strip() for s in self.llama_cpp_loras.split(",") if s.strip()
        ]


# ======================================================================
#  Singleton
# ======================================================================

settings = Settings()