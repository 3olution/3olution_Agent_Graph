"""
Direct runner for llama-cpp-python's OpenAI-compatible server.
https://3olution.com

Why this exists
---------------

Some builds of `llama_cpp.server` silently drop the `--port` command
line flag and fall back to uvicorn's default of 8000, which collides
with the backend's own port. Setting the LLAMA_CPP_PORT environment
variable does not help in those builds either.

This script bypasses the module's argument parser entirely. It reads
configuration from environment variables, constructs the settings
objects directly, and calls `uvicorn.run()` with an explicit port.

Model ID resolution
-------------------

The server exposes the value of `ModelSettings.model` as the model ID
in `/v1/models`, and requires the same string back in the `model` field
of chat and embeddings requests. This runner resolves the model path to
an absolute path before constructing ModelSettings. backend/llm.py
resolves the same path before sending requests, so the two agree.

Chat format handling
--------------------

If LLAMA_CPP_CHAT_FORMAT is set to a non-empty value, it is passed to
ModelSettings and OVERRIDES the model's embedded chat template. This
is usually the wrong thing to do — nearly every modern GGUF ships its
own template in `tokenizer.chat_template`, and forcing a different one
causes the model to produce garbage or an immediate EOS token.

If LLAMA_CPP_CHAT_FORMAT is empty (or unset), this runner does NOT
pass `chat_format` to ModelSettings. The model's embedded template is
used automatically, which is correct for Llama-3.2, Qwen, Mistral,
Gemma, and every other modern model.

Set LLAMA_CPP_CHAT_FORMAT explicitly only when the GGUF has no
embedded template, or when you deliberately want to override it.

Environment variables read
--------------------------

    LLAMA_CPP_MODEL           Absolute or relative path to .gguf (required)
    LLAMA_CPP_HOST            Bind address (default 127.0.0.1)
    LLAMA_CPP_PORT            Bind port    (default 8081)
    LLAMA_CPP_N_CTX           Context window (default 4096)
    LLAMA_CPP_N_GPU_LAYERS    GPU layers   (default 0)
    LLAMA_CPP_CHAT_FORMAT     Chat format  (default: empty = use model's own)
    LLAMA_CPP_API_KEY         Optional API key
    LLAMA_CPP_EMBEDDING       "true" to enable /v1/embeddings
"""

from __future__ import annotations

import inspect
import os
import sys


# ══════════════════════════════════════════════════════════════════════
#  Environment helpers
# ══════════════════════════════════════════════════════════════════════

def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _str_env(name: str, default: str = "") -> str:
    """Return the env var value with surrounding whitespace stripped."""
    return (os.environ.get(name) or default).strip()


def _log(msg: str) -> None:
    print(f"[llama_cpp_runner] {msg}", file=sys.stderr, flush=True)


# ══════════════════════════════════════════════════════════════════════
#  Settings construction
# ══════════════════════════════════════════════════════════════════════

def _build_model_settings(ModelSettings, kwargs: dict):
    """Construct ModelSettings, tolerating field-set variations."""
    try:
        return ModelSettings(**kwargs)
    except TypeError:
        minimal = {"model": kwargs["model"]}
        ms = ModelSettings(**minimal)
        for key, value in kwargs.items():
            if key == "model":
                continue
            try:
                setattr(ms, key, value)
            except Exception:
                pass
        return ms


def _build_server_settings(ServerSettings, host: str, port: int):
    """Construct ServerSettings with the requested host and port."""
    try:
        return ServerSettings(host=host, port=port)
    except Exception:
        pass

    try:
        ss = ServerSettings()
        try:
            ss.host = host  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ss.port = port  # type: ignore[attr-defined]
        except Exception:
            pass
        return ss
    except Exception:
        pass

    try:
        return ServerSettings()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
#  create_app invocation
# ══════════════════════════════════════════════════════════════════════

def _call_create_app(create_app, model_settings, server_settings):
    """Call create_app with the argument shape its signature expects."""
    try:
        sig = inspect.signature(create_app)
        _log(f"create_app signature: {sig}")
    except Exception:
        pass

    patterns = [
        (
            "list-kwarg",
            lambda: create_app(
                model_settings=[model_settings],
                server_settings=server_settings,
            ),
        ),
        (
            "dict-kwarg",
            lambda: create_app(
                model_settings={model_settings.model: model_settings},
                server_settings=server_settings,
            ),
        ),
        (
            "list-positional",
            lambda: create_app([model_settings], server_settings),
        ),
        (
            "single-kwarg",
            lambda: create_app(
                model_settings=model_settings,
                server_settings=server_settings,
            ),
        ),
        (
            "single-positional",
            lambda: create_app(model_settings, server_settings),
        ),
        (
            "list-only",
            lambda: create_app([model_settings]),
        ),
    ]

    last_exc: Exception | None = None
    for name, fn in patterns:
        try:
            app = fn()
            _log(f"create_app succeeded via '{name}'")
            return app
        except TypeError as exc:
            last_exc = exc
            _log(f"create_app pattern '{name}' raised TypeError: {exc}")
            continue
        except Exception as exc:
            last_exc = exc
            _log(f"create_app pattern '{name}' raised {type(exc).__name__}: {exc}")
            continue

    assert last_exc is not None
    raise last_exc


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    # ── Import SDK pieces ────────────────────────────────────────────
    try:
        from llama_cpp.server.app import create_app
        from llama_cpp.server.settings import ModelSettings, ServerSettings
    except ImportError as exc:
        _log(f"llama-cpp-python is not installed or is broken: {exc}")
        return 1

    # ── Resolve model path ───────────────────────────────────────────
    model_path_raw = _str_env("LLAMA_CPP_MODEL")
    if not model_path_raw:
        _log("LLAMA_CPP_MODEL is required")
        return 2

    model_path = os.path.abspath(model_path_raw)
    _log(f"resolved model path: {model_path}")

    if not os.path.isfile(model_path):
        _log(f"model file does not exist: {model_path}")
        return 2

    # ── Read remaining configuration ─────────────────────────────────
    host = _str_env("LLAMA_CPP_HOST", "127.0.0.1")
    port = _int_env("LLAMA_CPP_PORT", 8081)
    n_ctx = _int_env("LLAMA_CPP_N_CTX", 4096)
    n_gpu_layers = _int_env("LLAMA_CPP_N_GPU_LAYERS", 0)
    api_key = _str_env("LLAMA_CPP_API_KEY") or None
    embedding = _bool_env("LLAMA_CPP_EMBEDDING", False)

    # Chat format is optional. Empty means "use the model's own
    # embedded template". Only pass it through when explicitly set.
    chat_format = _str_env("LLAMA_CPP_CHAT_FORMAT")

    _log(f"host={host} port={port}")
    _log(f"n_ctx={n_ctx} n_gpu_layers={n_gpu_layers}")
    if chat_format:
        _log(f"chat_format={chat_format} (explicit override)")
    else:
        _log("chat_format=(unset — using model's embedded template)")
    _log(f"embedding={embedding}")

    # ── ModelSettings ────────────────────────────────────────────────
    model_kwargs: dict = {
        "model": model_path,
        "n_ctx": n_ctx,
        "n_gpu_layers": n_gpu_layers,
    }
    if chat_format:
        model_kwargs["chat_format"] = chat_format
    if api_key:
        model_kwargs["api_key"] = api_key
    if embedding:
        model_kwargs["embedding"] = True

    try:
        model_settings = _build_model_settings(ModelSettings, model_kwargs)
    except Exception as exc:
        _log(f"ModelSettings construction failed: {exc}")
        return 3

    # ── ServerSettings ───────────────────────────────────────────────
    server_settings = _build_server_settings(ServerSettings, host, port)
    if server_settings is None:
        _log("ServerSettings could not be constructed; continuing without it")

    # ── Build the app ────────────────────────────────────────────────
    try:
        if server_settings is not None:
            app = _call_create_app(create_app, model_settings, server_settings)
        else:
            app = _call_create_app(create_app, model_settings, None)
    except Exception as exc:
        _log(f"create_app failed for all patterns: {exc}")
        return 5

    # ── Run uvicorn with our explicit host and port ──────────────────
    import uvicorn

    _log(f"starting uvicorn on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())