"""
Filesystem tools.
https://3olution.com

Read-only access to the local filesystem. These tools run with the
same privileges as the backend process, which is usually the user's
own account.

================================================================
  SECURITY NOTE
================================================================

  There is NO sandboxing. A user who can reach the chat interface
  can read any file the backend process can read. On a single-user
  machine this is fine. On a shared machine, do not expose the
  backend to untrusted clients without authentication in front of
  it.

  The tools do not support writing, deleting, or moving files. If
  you need those, write a new tool in a separate module and add
  explicit safeguards (allowlisted root directory, size limits,
  path validation).

================================================================
  MEMORY SAFETY
================================================================

  `read_file` reads only `max_bytes` from disk. Earlier versions
  called `Path.read_bytes()` and sliced the result, which allocated
  the full file size in memory. For a 10 GB file, that would
  allocate 10 GB even though the caller asked for 4 KB.
"""

from __future__ import annotations

import os
from pathlib import Path

from .._server import mcp


# Hard cap on `read_file`'s max_bytes, regardless of what the caller
# passes. Prevents a misbehaving model from requesting a gigabyte.
_MAX_READ_BYTES = 1_048_576  # 1 MiB


@mcp.tool()
def list_directory(path: str = ".") -> str:
    """
    List the entries in a directory.

    The path is interpreted relative to the MCP server's working
    directory, which is the project root when the backend spawns it.

    Returns a newline-separated list of entry names, sorted
    alphabetically. Directories are not distinguished from files in
    the output — the caller can stat individual entries if needed.

    Returns a descriptive error string on failure (permission
    denied, not a directory, does not exist).
    """
    try:
        entries = sorted(os.listdir(path))
    except OSError as exc:
        return f"Error: {exc}"

    if not entries:
        return "(empty directory)"
    return "\n".join(entries)


@mcp.tool()
def read_file(path: str, max_bytes: int = 4096) -> str:
    """
    Read up to `max_bytes` bytes from a text file.

    The path is interpreted relative to the MCP server's working
    directory.

    The byte limit defaults to 4096 (4 KB) and is enforced by
    truncation, not rejection — reading a 10 MB file with the
    default limit returns the first 4 KB. The value is clamped to
    a hard maximum of 1 MiB regardless of what is requested. This
    keeps the LLM's context window from being flooded by an
    accidental large read.

    Returns the decoded UTF-8 text. If the file is not valid UTF-8
    (binary, or a different encoding), returns an error string
    instead of raising.

    Use this to inspect configuration files, logs, source code, or
    any small text file the user asks about.
    """
    p = Path(path)
    if not p.is_file():
        return f"Error: not a file: {path}"

    # Clamp before touching the filesystem so a hostile request
    # cannot open a huge file even briefly.
    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        limit = 4096
    limit = max(1, min(limit, _MAX_READ_BYTES))

    # Read only `limit` bytes. The previous implementation called
    # p.read_bytes() and sliced the result — which loads the whole
    # file into memory before discarding most of it.
    try:
        with p.open("rb") as f:
            data = f.read(limit)
    except OSError as exc:
        return f"Error: {exc}"

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "Error: file is not valid UTF-8"