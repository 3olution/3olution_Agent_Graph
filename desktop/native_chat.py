"""
3olution Agent Graph — native PyQt6 chat client.
https://3olution.com

A pure QtWidgets chat client that does not depend on
PyQt6-WebEngine. It talks to the same backend HTTP API the web UI
uses, including the SSE token streaming endpoint.

================================================================
  WHEN TO USE THIS
================================================================

  This module exists for three situations:

    1. PyQt6-WebEngine could not be installed (minimal Linux
       distributions, some CI environments, older macOS).

    2. The user prefers native widgets over the embedded Chromium
       view (subjective, but some do).

    3. Debugging: the native client surfaces every event as text,
       which makes it easy to see exactly what the backend is
       emitting.

================================================================
  FEATURE PARITY
================================================================

  Supported:
    - Streaming token display
    - Reasoning (thinking) display
    - Tool call and tool result display
    - Retry and fallback notices
    - Save conversation as Markdown
    - Stop all processes

  Not supported (relative to the web UI):
    - Graph editor
    - Settings page
    - Ollama model management
    - Model picker (uses AGENT_LLM_MODEL)

  For the missing features, launch the main desktop app or open the
  web UI in a browser.

================================================================
  THREADING
================================================================

  The backend call runs on a QThread worker. Events are delivered
  to the main thread via Qt signals. This keeps the UI responsive
  even during a long multi-tool turn.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

import httpx
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ══════════════════════════════════════════════════════════════════════
#  Configuration
# ══════════════════════════════════════════════════════════════════════

BACKEND = os.environ.get("AGENT_DESKTOP_BACKEND", "http://127.0.0.1:8000")


# ══════════════════════════════════════════════════════════════════════
#  Worker thread
# ══════════════════════════════════════════════════════════════════════

class AgentWorker(QThread):
    """
    Runs one agent turn off the UI thread.

    Emits each event as a dict via the `event` signal. Prefers the
    SSE streaming endpoint so tokens arrive live; falls back to the
    non-streaming /api/chat endpoint if SSE is unavailable.
    """

    event = pyqtSignal(dict)

    def __init__(
        self,
        message: str,
        history: list[dict],
        session_id: str | None = None,
    ) -> None:
        super().__init__()
        self.message = message
        self.history = history
        self.session_id = session_id

    def run(self) -> None:
        payload = {
            "message": self.message,
            "history": self.history,
            "session_id": self.session_id,
        }

        # ── Primary path: SSE streaming ─────────────────────────────
        try:
            with httpx.Client(timeout=None) as client:
                with client.stream(
                    "POST",
                    f"{BACKEND}/api/chat/stream",
                    json=payload,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        raise RuntimeError(f"HTTP {response.status_code}")

                    buffer = ""
                    for chunk in response.iter_text():
                        if not chunk:
                            continue
                        buffer += chunk

                        # SSE frames are separated by a blank line.
                        while "\n\n" in buffer:
                            frame, buffer = buffer.split("\n\n", 1)
                            for line in frame.split("\n"):
                                if not line.startswith("data: "):
                                    continue
                                data = line[6:]
                                if data == "[DONE]":
                                    return
                                try:
                                    self.event.emit(json.loads(data))
                                except json.JSONDecodeError:
                                    continue
                    return
        except Exception:
            pass

        # ── Fallback path: non-streaming POST ───────────────────────
        try:
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(f"{BACKEND}/api/chat", json=payload)
                resp.raise_for_status()
                for ev in resp.json():
                    self.event.emit(ev)
        except Exception as exc2:
            self.event.emit({
                "type": "final",
                "content": f"Connection error: {exc2}",
                "messages": self.history,
            })


# ══════════════════════════════════════════════════════════════════════
#  Main window
# ══════════════════════════════════════════════════════════════════════

class ChatWindow(QMainWindow):
    """Native PyQt6 chat window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("3olution Agent Graph — Native Chat")
        self.resize(820, 620)

        self.history: list[dict] = []
        self.session_id: str | None = None
        self._streaming_started = False
        self._thinking_started = False
        self.worker: AgentWorker | None = None
        self.message_log: list[dict] = []

        # ── Layout ───────────────────────────────────────────────────
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header with buttons.
        header = QHBoxLayout()
        header.addStretch(1)

        self.btn_save = QPushButton("Save Chat")
        self.btn_save.setStyleSheet(
            "QPushButton { padding: 6px 12px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 6px; }"
            "QPushButton:hover { background: #2a3542; }"
        )
        self.btn_save.clicked.connect(self._save_chat)
        header.addWidget(self.btn_save)

        self.btn_stop_all = QPushButton("Stop All")
        self.btn_stop_all.setStyleSheet(
            "QPushButton { padding: 6px 12px; background: transparent; "
            "color: #ffb1b1; border: 1px solid rgba(239, 68, 68, 0.4); "
            "border-radius: 6px; }"
            "QPushButton:hover { background: rgba(239, 68, 68, 0.15); }"
        )
        self.btn_stop_all.clicked.connect(self._stop_all)
        header.addWidget(self.btn_stop_all)
        layout.addLayout(header)

        # Transcript.
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setStyleSheet(
            "QTextEdit { background: #0f1116; color: #e6e6e6; "
            "border: 1px solid #222; border-radius: 8px; padding: 10px; "
            "font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; "
            "font-size: 13px; }"
        )
        layout.addWidget(self.transcript, stretch=1)

        # Input row.
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask the agent…")
        self.input.setStyleSheet(
            "QLineEdit { background: #1a1d24; color: #e6e6e6; "
            "border: 1px solid #333; border-radius: 6px; "
            "padding: 8px 12px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #2563eb; }"
        )
        self.input.returnPressed.connect(self.send)

        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: #fff; "
            "border: none; border-radius: 6px; "
            "padding: 8px 18px; font-size: 13px; font-weight: 500; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:disabled { opacity: 0.5; }"
        )
        self.send_btn.clicked.connect(self.send)

        row.addWidget(self.input, stretch=1)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        self.setCentralWidget(central)

    # ── Transcript helpers ───────────────────────────────────────────

    def _append(self, text: str) -> None:
        """Append text and scroll to bottom."""
        self.transcript.append(text)
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_inline(self, text: str) -> None:
        """Insert text at the end without a newline."""
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.transcript.setTextCursor(cursor)
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Send ─────────────────────────────────────────────────────────

    def send(self) -> None:
        message = self.input.text().strip()
        if not message or (self.worker and self.worker.isRunning()):
            return

        self.input.clear()
        self._append(f"\n🧑  You: {message}")
        self.message_log.append({"role": "user", "content": message})

        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self._streaming_started = False
        self._thinking_started = False

        self.worker = AgentWorker(message, self.history, self.session_id)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_finished(self) -> None:
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.focusWidget()
        self.input.setFocus()

    # ── Event handling ───────────────────────────────────────────────

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")

        if t == "thinking":
            if not self._thinking_started:
                self._append("\n💭  Thinking:")
                self._thinking_started = True
            self._append_inline(ev.get("content", ""))

        elif t == "token":
            if not self._streaming_started:
                self._append("\n🤖  Agent: ")
                self._streaming_started = True
            self._append_inline(ev.get("content", ""))

        elif t == "retry":
            self._append(f"\n↻  {ev.get('message', 'Retrying…')}")
            self._streaming_started = False
            self._thinking_started = False

        elif t == "fallback":
            self._append(
                f"\n⚡  {ev.get('message', 'Using fallback LLM')}"
            )
            self._streaming_started = False

        elif t == "info":
            self._append(f"\nℹ  {ev.get('message', '')}")

        elif t == "tool_call":
            self._append(
                f"\n🔧  Tool call: {ev['name']}({ev['args']})"
            )

        elif t == "tool_result":
            self._append(f"\n📊  Result: {ev['result']}")

        elif t == "final":
            if not self._streaming_started:
                self._append(f"\n🤖  Agent: {ev['content']}\n")
            else:
                self._append("")
            self.history = ev.get("messages", [])
            if ev.get("content"):
                self.message_log.append({
                    "role": "assistant",
                    "content": ev["content"],
                })

    # ── Save conversation ────────────────────────────────────────────

    def _save_chat(self) -> None:
        """Save the transcript as Markdown."""
        if not self.message_log:
            QMessageBox.information(self, "Save", "Nothing to save yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Conversation",
            f"3olution-chat-{self.session_id or 'session'}.md",
            "Markdown (*.md);;All files (*)",
        )
        if not path:
            return

        lines = [
            "# 3olution Agent Graph — Conversation",
            "",
            f"Session: {self.session_id or '(none)'}",
            f"Saved: {datetime.datetime.now().isoformat()}",
            "",
            "---",
            "",
        ]
        for m in self.message_log:
            role = "🧑 You" if m["role"] == "user" else "🤖 Agent"
            lines.append(f"## {role}")
            lines.append("")
            lines.append(m["content"])
            lines.append("")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._append(f"\n💾 Saved to {path}")
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    # ── Stop all ─────────────────────────────────────────────────────

    def _stop_all(self) -> None:
        """Call the backend's stop-all endpoint."""
        reply = QMessageBox.question(
            self,
            "Stop all processes",
            "Stop the backend and all subprocesses?\n\n"
            "Restart with:  python run.py backend",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            httpx.post(f"{BACKEND}/api/system/stop-all", timeout=3.0)
        except Exception:
            pass
        self._append(
            "\n🛑 Stop-all requested. Backend is shutting down."
        )


# ══════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("3olution Agent Graph — Native Chat")
    app.setOrganizationName("3olution")
    app.setStyle("Fusion")

    # Dark palette (same as desktop.app).
    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0f1116"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e6e6e6"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1a1d24"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#14161b"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6e6e6"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1f2937"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e6e6"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = ChatWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()