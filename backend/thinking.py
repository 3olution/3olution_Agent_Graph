"""
Streaming parser for reasoning blocks.
https://3olution.com

Some language models emit their chain-of-thought wrapped in special
markers before the final answer. This module splits a token stream
into two lanes — thinking and text — so the UI can render them
separately.

================================================================
  SUPPORTED MARKERS
================================================================

  Long markers are matched first, so a stream that contains
  "<thinking>" is not mis-detected as " thinking" + "king".

      <thinking>...</thinking>       (Anthropic-style)
       thinking...                      (DeepSeek R1, Qwen 3)
      \n...               (some QwQ variants)
       Thinking: ...                   (rare, some finetunes)
      Thinking: ...                    (fallback)

  In addition, DeepSeek R1 and several OpenAI-compatible reasoning
  endpoints emit their reasoning in a SEPARATE field of the streaming
  delta object, `reasoning_content`, rather than inside `content`.
  The agent loop handles that case directly — this module only
  handles markers embedded in the content stream.

================================================================
  INCREMENTAL PARSING
================================================================

  The splitter is incremental. It accepts arbitrary chunks of text
  and emits (kind, content) tuples, where kind is "text" or
  "thinking". It buffers a few characters at the tail so a marker
  split across two chunks is still recognised.

  Example: if a chunk ends with "<thin" and the next chunk starts
  with "king>", the splitter does not emit "<thin" as text. It keeps
  it in the buffer until the next chunk arrives, then emits the
  correct opening marker.

================================================================
  USAGE
================================================================

    splitter = ThinkingSplitter(enabled=True)

    for chunk in stream:
        for kind, content in splitter.feed(chunk):
            if kind == "thinking":
                send_reasoning(content)
            else:
                send_answer(content)

    for kind, content in splitter.flush():
        # Handle any buffered tail at end-of-stream
        ...
"""

from __future__ import annotations

import re


# ══════════════════════════════════════════════════════════════════════
#  Marker table
# ══════════════════════════════════════════════════════════════════════
#
#  Ordered by descending open-marker length so that longer markers
#  match before their shorter prefixes. The close marker is the
#  string that ends the block; an empty close marker means the block
#  runs until the end of the stream or the next blank line.

_MARKER_PAIRS: list[tuple[str, str]] = [
    # Anthropic XML-style
    ("<thinking>", "</thinking>"),

    # DeepSeek R1 / Qwen 3 / Granite
    (" thinking", ""),
    (" thinking\n", ""),

    # QwQ variants (leading newline)
    ("\n", ""),

    # Rare prose-style markers
    (" Thinking:", "\n\n"),
    ("Thinking:", "\n\n"),
]


# ══════════════════════════════════════════════════════════════════════
#  ThinkingSplitter
# ══════════════════════════════════════════════════════════════════════

class ThinkingSplitter:
    """
    Incremental splitter for reasoning blocks in a token stream.

    The `enabled` flag controls whether the splitter does any work at
    all. When False, every chunk is emitted as text with no parsing —
    the fast path when the user has turned reasoning off.
    """

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.pending = ""
        self.inside = False
        # The specific close marker for the currently-open block.
        self._open = ""
        self._close = ""

    # ── Internal helpers ──────────────────────────────────────────────

    def _max_open_len(self) -> int:
        """Length of the longest opening marker, minus one."""
        return max(len(m) for m, _ in _MARKER_PAIRS) - 1

    def _max_close_len(self) -> int:
        """Length of the longest closing marker, minus one."""
        return max(len(m) for _, m in _MARKER_PAIRS) - 1

    def _find_opening(self) -> tuple[int, str, str] | None:
        """
        Find the earliest opening marker in self.pending.

        Returns (index, open_marker, close_marker) or None if no
        marker is present.
        """
        best: tuple[int, str, str] | None = None
        for open_m, close_m in _MARKER_PAIRS:
            idx = self.pending.find(open_m)
            if idx == -1:
                continue
            if best is None or idx < best[0]:
                best = (idx, open_m, close_m)
        return best

    # ── Public API ────────────────────────────────────────────────────

    def feed(self, text: str) -> list[tuple[str, str]]:
        """
        Feed a chunk of text.

        Returns a list of (kind, content) tuples. kind is "text" or
        "thinking". May return an empty list if the chunk is entirely
        buffered awaiting more input.
        """
        if not text:
            return []

        if not self.enabled:
            return [("text", text)]

        self.pending += text
        results: list[tuple[str, str]] = []

        while True:
            if not self.inside:
                # ── Looking for an opening marker ────────────────────
                found = self._find_opening()
                if found is None:
                    # No opening marker in the buffer. Emit everything
                    # except the last few characters, in case a marker
                    # straddles the chunk boundary.
                    keep = self._max_open_len()
                    if len(self.pending) > keep:
                        emit = self.pending[:-keep]
                        self.pending = self.pending[-keep:]
                        if emit:
                            results.append(("text", emit))
                    break

                idx, open_m, close_m = found
                if idx > 0:
                    results.append(("text", self.pending[:idx]))
                self.pending = self.pending[idx + len(open_m):]
                self._open = open_m
                self._close = close_m
                self.inside = True
            else:
                # ── Looking for the matching closing marker ──────────
                if self._close:
                    idx = self.pending.find(self._close)
                else:
                    # Empty close marker: block ends at a blank line
                    # or at end-of-stream.
                    idx = self.pending.find("\n\n")
                    if idx == -1:
                        # No blank line yet — emit everything except
                        # the last character to catch a "\n" split
                        # across chunks.
                        keep = 1
                        if len(self.pending) > keep:
                            emit = self.pending[:-keep]
                            self.pending = self.pending[-keep:]
                            if emit:
                                results.append(("thinking", emit))
                        break

                if idx == -1:
                    # No closing marker yet. Buffer the tail so a
                    # split closing marker is still matched.
                    keep = self._max_close_len() if self._close else 1
                    if len(self.pending) > keep:
                        emit = self.pending[:-keep]
                        self.pending = self.pending[-keep:]
                        if emit:
                            results.append(("thinking", emit))
                    break

                if idx > 0:
                    results.append(("thinking", self.pending[:idx]))
                self.pending = self.pending[idx + len(self._close):]
                self.inside = False

        return results

    def flush(self) -> list[tuple[str, str]]:
        """
        Flush any remaining buffered content.

        Call this at end-of-stream. Any text still in the buffer is
        emitted as either "thinking" (if we never saw a closing
        marker) or "text".
        """
        if not self.pending:
            return []

        kind = "thinking" if self.inside else "text"
        content = self.pending
        self.pending = ""
        return [(kind, content)]


# ══════════════════════════════════════════════════════════════════════
#  Convenience function for finished strings
# ══════════════════════════════════════════════════════════════════════

def strip_thinking(text: str) -> str:
    """
    Remove every reasoning block from a finished string.

    Use this when you need the plain answer without any of the
    reasoning text — for example, when displaying a stored
    conversation whose history was saved before the splitter was
    enabled, or when searching the answer for a keyword.

    The function is conservative: it only removes text between
    matched marker pairs, and never touches content that looks like
    normal prose.
    """
    if not text:
        return text

    out = text
    for open_m, close_m in _MARKER_PAIRS:
        if close_m:
            pattern = re.escape(open_m) + r".*?" + re.escape(close_m)
            out = re.sub(pattern, "", out, flags=re.DOTALL)
        else:
            # Empty close marker: strip from the opening marker to the
            # next blank line or end of string.
            pattern = re.escape(open_m) + r".*?(?=$|\n\n|\Z)"
            out = re.sub(pattern, "", out, flags=re.DOTALL)

    return out.strip()