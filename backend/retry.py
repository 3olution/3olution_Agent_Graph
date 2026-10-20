"""
Async retry helper with exponential backoff.
https://3olution.com

Callers can specify which exceptions to retry in one of two ways:

    retry_on=(ValueError, KeyError, ...)
        A tuple of exception classes. Any exception that is an
        instance of one of these is retried. This is the simple form
        used by backend/mcp_client.py for its transport-level retries.

    should_retry=callable
        A predicate that takes the caught exception and returns True
        if the operation should be retried. This is the flexible form
        used by backend/llm.py, where whether to retry depends on the
        HTTP status code and the error message.

If both are supplied, `should_retry` wins. If neither is supplied,
every exception is retried up to `attempts` times.

================================================================
  LOGGING
================================================================

  Each failed attempt logs at WARNING level with the attempt number
  and the wait time. The final failure logs at ERROR level. The
  `label` argument lets the caller identify the operation in logs —
  for example "LLM auto stream (model=llama3.2)".
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

from loguru import logger

T = TypeVar("T")

# A predicate that takes an exception and returns True if the
# operation should be retried.
ShouldRetryFn = Callable[[BaseException], bool]


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    attempts: int = 3,
    backoff: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    should_retry: Optional[ShouldRetryFn] = None,
    label: str = "operation",
) -> T:
    """
    Call `fn()` up to `attempts` times with exponential backoff.

    Parameters
    ----------
    fn
        A zero-argument coroutine function. Each call produces a
        fresh awaitable.
    attempts
        Maximum number of attempts. Clamped to at least 1.
    backoff
        Base delay in seconds. The wait before attempt N is
        `backoff * 2**(N-1)`.
    retry_on
        Tuple of exception classes. Only used when `should_retry`
        is None.
    should_retry
        Optional predicate. When supplied, replaces `retry_on`. The
        predicate receives the caught exception and returns True if
        the operation should be retried.
    label
        Human-readable name for logs.

    Returns
    -------
    The value returned by `fn()` on success.

    Raises
    ------
    The last exception raised by `fn()` if every attempt fails, or
    the first non-retryable exception if `should_retry` returns False.
    """
    attempts = max(1, attempts)
    last_exc: Optional[BaseException] = None

    # Resolve the retry decision function once, before the loop.
    # Two paths:
    #
    #   1. should_retry is provided → use it directly.
    #   2. otherwise → build a closure over `retry_on` that does an
    #      isinstance check.
    #
    # Building a single callable lets the loop body call
    # `decide(exc)` without branching on every iteration.
    if should_retry is not None:
        decide: ShouldRetryFn = should_retry
    else:
        def decide(exc: BaseException) -> bool:
            return isinstance(exc, retry_on)

    for i in range(attempts):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 — intentionally broad
            # Never swallow CancelledError or KeyboardInterrupt —
            # those are control flow, not operational failures.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, asyncio.CancelledError):
                raise

            last_exc = exc

            # Ask the predicate whether this exception is retryable.
            try:
                retryable = bool(decide(exc))
            except Exception:
                # A misbehaving predicate should not mask the real
                # error. Treat it as "do not retry".
                retryable = False

            if not retryable:
                logger.debug(
                    "{} raised non-retryable {}: {}",
                    label, type(exc).__name__, exc,
                )
                raise

            if i < attempts - 1:
                wait = backoff * (2 ** i)
                logger.warning(
                    "{} failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                    label, i + 1, attempts, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "{} failed after {} attempts: {}",
                    label, attempts, exc,
                )

    assert last_exc is not None
    raise last_exc