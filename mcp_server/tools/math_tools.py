"""
Math tools.
https://3olution.com

Basic arithmetic operations. All accept float arguments and return
a float. The LLM calls these for explicit "what is X op Y" questions
where the model wants deterministic arithmetic rather than its own
learned approximations.
"""

from __future__ import annotations

import math

from .._server import mcp


# ══════════════════════════════════════════════════════════════════════
#  Binary operations
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def add(a: float, b: float) -> float:
    """
    Add two numbers together.

    Use this for any arithmetic addition the user asks for. Prefer
    this over computing in your head — the result is exact for
    floating-point values.
    """
    return a + b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """
    Multiply two numbers together.

    Use this for any multiplication the user asks for. The result
    is exact within IEEE 754 double precision.
    """
    return a * b


@mcp.tool()
def power(base: float, exponent: float) -> float:
    """
    Raise `base` to the power of `exponent`.

    Supports fractional and negative exponents (e.g. power(2, 0.5)
    returns sqrt(2), power(2, -1) returns 0.5).
    """
    return base ** exponent


# ══════════════════════════════════════════════════════════════════════
#  Unary operations
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def sqrt(x: float) -> float:
    """
    Return the square root of a non-negative number.

    Raises a ValueError for negative inputs — imaginary results are
    not supported. The agent loop turns the raise into an error
    string in the conversation so the model can react.
    """
    if x < 0:
        raise ValueError("sqrt requires a non-negative number")
    return math.sqrt(x)