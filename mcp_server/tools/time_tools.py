"""
Time tools.
https://3olution.com

Provides the current time in a format the LLM can parse reliably.
The model has no internal clock; without this tool it will refuse
or hallucinate a timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .._server import mcp


@mcp.tool()
def current_time() -> str:
    """
    Return the current UTC time in ISO 8601 format.

    The returned string looks like:
        2025-01-15T12:34:56.789012+00:00

    Use this whenever the user asks "what time is it", "what's the
    date", "how long until X", or any question that requires
    knowing the current moment. Always report the result to the
    user in a friendly format — do not paste the raw ISO string
    unless they asked for it.
    """
    return datetime.now(timezone.utc).isoformat()