"""
Text manipulation tools.
https://3olution.com

Simple deterministic text operations. Each tool does one thing and
returns a primitive value, so the LLM can compose them.
"""

from __future__ import annotations

from .._server import mcp


@mcp.tool()
def word_count(text: str) -> dict:
    """
    Count words and characters in a string.

    Returns a dict with three fields:

        words         — number of whitespace-separated tokens
        characters    — total character count including whitespace
        unique_words  — number of distinct lowercased words

    Useful for essay-length checks, content analysis, and any
    question of the form "how many words is this?".
    """
    words = text.split()
    return {
        "words": len(words),
        "characters": len(text),
        "unique_words": len(set(w.lower() for w in words)),
    }


@mcp.tool()
def shout(text: str) -> str:
    """
    Convert the given text to uppercase.

    A trivial but useful demonstration tool: the LLM can call it
    when the user asks to "shout" a message, "yell" a string, or
    anything that maps to uppercasing. It also serves as the
    canonical example of a simple tool for anyone extending the
    server.
    """
    return text.upper()