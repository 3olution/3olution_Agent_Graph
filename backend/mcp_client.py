"""
MCP client — Windows-safe stdio connection to an MCP server subprocess.
https://3olution.com

Key fixes:

  - Bare interpreter names ("python", "python3", "py") are resolved
    to sys.executable, so the subprocess always uses the SAME venv.

  - The connection is retried once, handling transient spawn races.

  - Cleanup suppresses ValueError on Windows closed pipes.

  - The `Tool.inputSchema` attribute (MCP SDK 1.x) was renamed to
    `Tool.input_schema` in SDK 2.x. The `_tool_input_schema()`
    helper reads whichever one is present, so the same code works
    against either version of the SDK.

================================================================
  WHY THE SDK VERSION CHECK MATTERS
================================================================

  The MCP Python SDK changed one public attribute between major
  versions:

      SDK 1.x:   Tool.inputSchema   (camelCase)
      SDK 2.x:   Tool.input_schema  (snake_case)

  Pydantic v2 raises AttributeError when an undeclared attribute is
  accessed, so `tool.inputSchema` on a v2 Tool object crashes the
  backend the first time a tool-using turn runs.

  Rather than pin the SDK version, we accept both. The helper tries
  `input_schema` first, then falls back to `inputSchema`, then to a
  well-formed empty schema. That way the same code works against
  any currently-installed SDK.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any

from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import settings
from .retry import retry_async


# ══════════════════════════════════════════════════════════════════════
#  Bare interpreter resolution
# ══════════════════════════════════════════════════════════════════════

_BARE_INTERPRETERS = {
    "python", "python3", "py",
    "python.exe", "python3.exe", "py.exe",
    "python3.10", "python3.11", "python3.12", "python3.13",
    "python3.10.exe", "python3.11.exe",
    "python3.12.exe", "python3.13.exe",
}


def _resolve_command(command: str) -> str:
    """Replace bare interpreter names with sys.executable."""
    if command.lower() in _BARE_INTERPRETERS and sys.executable:
        return sys.executable
    return command


# ══════════════════════════════════════════════════════════════════════
#  Tool schema compatibility helper
# ══════════════════════════════════════════════════════════════════════

def _tool_input_schema(tool: Any) -> dict:
    """
    Return a tool's JSON input schema, whatever the SDK calls it.

    Reads `input_schema` (MCP SDK 2.x) first, then `inputSchema`
    (MCP SDK 1.x). Falls back to a well-formed empty object schema
    if neither is present, which prevents a missing schema from
    crashing the whole tool list.
    """
    # SDK 2.x name.
    schema = getattr(tool, "input_schema", None)

    # SDK 1.x name.
    if schema is None:
        schema = getattr(tool, "inputSchema", None)

    # Neither present — return a minimal valid schema.
    if schema is None:
        return {"type": "object", "properties": {}}

    # A JSON schema is a dict. Some SDK versions wrap it in a
    # pydantic model with a `.model_dump()` method.
    if isinstance(schema, dict):
        return schema

    dump = getattr(schema, "model_dump", None)
    if callable(dump):
        try:
            return dump(exclude_none=True)
        except Exception:
            pass

    # Last resort: stringify. The LLM will at least see something.
    return {"type": "object", "properties": {}, "x-raw": str(schema)}


# ══════════════════════════════════════════════════════════════════════
#  MCPClient
# ══════════════════════════════════════════════════════════════════════

class MCPClient:
    """Manages a persistent stdio connection to one MCP server."""

    def __init__(self) -> None:
        self.session: ClientSession | None = None
        self.exit_stack = AsyncExitStack()
        self._connected = False
        self._effective_command: str = ""
        self._effective_args: list[str] = []

    # ══════════════════════════════════════════════════════════════════
    #  Connect
    # ══════════════════════════════════════════════════════════════════

    async def connect(
        self,
        command: str,
        args: list[str],
        retries: int = 1,
    ) -> None:
        """Spawn the MCP server and initialise the session."""
        original_command = command
        command = _resolve_command(command)
        if command != original_command:
            logger.info(
                "Resolved MCP command '{}' -> '{}'",
                original_command, command,
            )

        # Windows: npx and similar node tools are .ps1/.cmd shims
        # that cannot be spawned directly. Wrap in cmd /c.
        if sys.platform == "win32" and command.lower() in ("npx", "npx.ps1"):
            logger.info("Windows detected - wrapping '{}' in cmd /c", command)
            args = ["/c", command, *args]
            command = "cmd"

        # If the user gave an absolute path, verify it exists.
        cmd_path = Path(command)
        if cmd_path.is_absolute() and not cmd_path.exists():
            raise FileNotFoundError(
                f"MCP server command does not exist: {command}"
            )

        self._effective_command = command
        self._effective_args = list(args)

        logger.info("Spawning MCP server: {} {}", command, " ".join(args))

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            params = StdioServerParameters(command=command, args=args)
            try:
                self.exit_stack = AsyncExitStack()
                read, write = await self.exit_stack.enter_async_context(
                    stdio_client(params)
                )
                self.session = await self.exit_stack.enter_async_context(
                    ClientSession(read, write)
                )
                await self.session.initialize()
                self._connected = True
                logger.info("MCP session initialised")
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "MCP connect attempt {}/{} failed: {}",
                    attempt + 1, retries + 1, exc,
                )
                with suppress(Exception):
                    await self.exit_stack.aclose()
                self.exit_stack = AsyncExitStack()
                self.session = None
                self._connected = False
                if attempt < retries:
                    await asyncio.sleep(0.5)

        assert last_exc is not None
        hint_cmd = f"{self._effective_command} {' '.join(self._effective_args)}"
        logger.error(
            "MCP connection failed after {} attempt(s).\n"
            "  Interpreter: {}\n"
            "  Command:     {}\n"
            "  Hint: run this to see the real error:\n"
            "      {}",
            retries + 1, sys.executable, hint_cmd, hint_cmd,
        )
        raise last_exc

    # ══════════════════════════════════════════════════════════════════
    #  Close
    # ══════════════════════════════════════════════════════════════════

    async def close(self) -> None:
        """Gracefully close the MCP session."""
        if not self._connected:
            return
        # Windows raises ValueError when closing an already-closed
        # pipe. Suppressing it here is intentional.
        with suppress(ValueError, Exception):
            await self.exit_stack.aclose()
        self._connected = False
        logger.info("MCP session closed")

    # ══════════════════════════════════════════════════════════════════
    #  Tools
    # ══════════════════════════════════════════════════════════════════

    async def list_tools_openai(self) -> list[dict]:
        """
        Return all MCP tools in OpenAI function-calling schema.

        Reads the input schema via `_tool_input_schema()`, which
        works with both MCP SDK 1.x (`inputSchema`) and 2.x
        (`input_schema`).
        """
        if self.session is None:
            return []

        try:
            result = await self.session.list_tools()
        except Exception as exc:
            logger.error("Failed to list MCP tools: {}", exc)
            return []

        tools: list[dict] = []
        for t in result.tools:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": _tool_input_schema(t),
                },
            })
        return tools

    async def call_tool(self, name: str, args: dict) -> str:
        """
        Invoke an MCP tool and return its output as a string.

        Transport-level failures (timeout, connection reset, OS
        error) are retried up to `AGENT_RETRY_ATTEMPTS` times with
        exponential backoff. Business-level failures are returned as
        a string so the LLM can see the error and react.
        """
        if self.session is None:
            return "Error: MCP session not connected."

        async def _do_call() -> str:
            result = await asyncio.wait_for(
                self.session.call_tool(name, args),
                timeout=settings.tool_timeout,
            )
            parts: list[str] = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else "(empty result)"

        try:
            return await retry_async(
                _do_call,
                attempts=settings.retry_attempts,
                backoff=settings.retry_backoff,
                retry_on=(asyncio.TimeoutError, ConnectionError, OSError),
                label=f"tool '{name}'",
            )
        except asyncio.TimeoutError:
            return f"Error: tool '{name}' timed out after {settings.tool_timeout}s"
        except Exception as exc:
            logger.error("Tool '{}' failed: {}", name, exc)
            return f"Error calling tool '{name}': {exc}"