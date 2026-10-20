"""
Models directory helpers.
https://3olution.com

Owns the `models/` folder: creates it on first run, writes a README,
lists GGUF files, and resolves a model name to an absolute path.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from .config import settings


README_TEXT = """# Models Directory

Drop `.gguf` files here. The backend will discover them automatically
and list them in the chat UI's model dropdown and the graph editor's
`llm.chat` node.

## Where to download GGUF files

- Hugging Face: search for "GGUF" plus the model name, e.g.
  `bartowski/Llama-3.2-3B-Instruct-GGUF`
- TheBloke's historical uploads on Hugging Face
- Ollama's model library (if you prefer to use Ollama instead)

## Naming

Any `.gguf` file works. The filename becomes the model ID that the
backend sends to llama-server. To override the ID, set
`AGENT_LLAMA_CPP_ALIAS` in `.env` and pass `--alias` to the server.

## Loading

- The chat UI has a "Load GGUF" button in the header.
- The Settings page has a runtime section with the same controls.
- Set `AGENT_LLAMA_CPP_MODEL` in `.env` to load a file at startup.

## Disk space

Each quantized 3B model is ~2 GB, 7B ~4 GB, 13B ~8 GB. Keep an eye on
free space before pulling multiple models.
"""


def ensure_models_dir() -> Path:
    """
    Create the models directory if it does not exist, and write a
    README the first time. Returns the absolute path.
    """
    models_dir = settings.models_path
    models_dir.mkdir(parents=True, exist_ok=True)

    readme = models_dir / "README.md"
    if not readme.exists():
        try:
            readme.write_text(README_TEXT, encoding="utf-8")
            logger.info("Created models directory: {}", models_dir)
        except Exception as exc:
            logger.warning("Could not write models README: {}", exc)

    return models_dir


def list_gguf_files() -> list[dict]:
    """
    Return metadata for every .gguf file in the models directory.

    Each entry has: name, path (absolute), size (bytes), size_gb.
    Sorted alphabetically by filename.
    """
    models_dir = settings.models_path
    if not models_dir.exists():
        return []

    out: list[dict] = []
    for f in sorted(models_dir.glob("*.gguf")):
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        out.append({
            "name": f.name,
            "path": str(f.resolve()),
            "size": size,
            "size_gb": round(size / 1e9, 2),
        })
    return out


def resolve_model_path(name_or_path: str | None) -> Path | None:
    """
    Resolve a model name or path to an absolute Path.

    Accepts:
      - An absolute path to an existing .gguf file
      - A path relative to the models directory
      - A bare filename (resolved against the models directory)
      - A path relative to the project root

    Returns the resolved Path if the file exists, or None otherwise.
    """
    if not name_or_path:
        return None

    p = Path(name_or_path)

    # 1. Absolute path
    if p.is_absolute():
        return p if p.is_file() else None

    # 2. Relative to the models directory
    candidate = settings.models_path / name_or_path
    if candidate.is_file():
        return candidate.resolve()

    # 3. Relative to the project root
    candidate = settings.project_root / name_or_path
    if candidate.is_file():
        return candidate.resolve()

    return None