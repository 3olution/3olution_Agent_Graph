"""
Native PyQt6 dialog for installing llama-cpp-python[server].
https://3olution.com

The web settings page has its own installer UI. This dialog exists
for two cases:

    1. Users of desktop.native_chat, who never load the web UI.

    2. Users who prefer a native file dialog for pip's wheel choice.

The dialog detects the local GPU, recommends a wheel index, and
streams pip's output into a read-only text area. It talks to the
same /api/llama/install/* endpoints the web settings page uses, so
there is no duplicated backend logic — only duplicated presentation.

================================================================
  SSE STREAMING WITHOUT A BROWSER
================================================================

  The install endpoint streams Server-Sent Events. Python's httpx
  can consume the same stream via `client.stream(...)` and
  `iter_text()`. That is what the QProcess-like worker below does;
  the wording "QProcess" in the docstring is a holdover from an
  earlier implementation that has been replaced with httpx for
  cross-platform reliability.

================================================================
  WINDOWS RESTART CAVEAT
================================================================

  On Windows, pip cannot replace llama-cpp-python while the backend
  process has the native DLLs mapped. If the user is upgrading or
  switching wheels, they must stop the backend first. The dialog
  shows a prominent warning when the install is starting and
  another when it completes.
"""

from __future__ import annotations

import json
import threading

import httpx
from PyQt6.QtCore import QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


# ══════════════════════════════════════════════════════════════════════
#  Constants
# ══════════════════════════════════════════════════════════════════════

BACKEND = "http://127.0.0.1:8000"

WHEEL_LABELS = [
    ("cpu",   "CPU only (no GPU acceleration)"),
    ("cu124", "CUDA 12.4 (RTX 30/40, Ada/Hopper)"),
    ("cu121", "CUDA 12.1 (older 12.x drivers)"),
    ("cu118", "CUDA 11.8 (GTX 10/16, RTX 20)"),
]


# ══════════════════════════════════════════════════════════════════════
#  SSE consumer thread
# ══════════════════════════════════════════════════════════════════════

class SSEWorker(QThread):
    """
    Reads a Server-Sent Events stream from the backend and emits each
    parsed JSON event as a Qt signal.

    Used for both install and uninstall streaming.
    """

    event = pyqtSignal(dict)

    def __init__(
        self,
        url: str,
        payload: dict | None,
        label: str = "operation",
    ) -> None:
        super().__init__()
        self.url = url
        self.payload = payload
        self.label = label

    def run(self) -> None:
        try:
            with httpx.Client(timeout=None) as client:
                method = "POST" if self.payload is not None else "POST"
                with client.stream(
                    method,
                    self.url,
                    json=self.payload if self.payload is not None else None,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        self.event.emit({
                            "type": "error",
                            "message": f"HTTP {response.status_code}",
                        })
                        return

                    buffer = ""
                    for chunk in response.iter_text():
                        if not chunk:
                            continue
                        buffer += chunk
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
        except Exception as exc:
            self.event.emit({
                "type": "error",
                "message": f"{self.label} stream failed: {exc}",
            })


# ══════════════════════════════════════════════════════════════════════
#  Main dialog
# ══════════════════════════════════════════════════════════════════════

class InstallDialog(QDialog):
    """Native installer for llama-cpp-python[server]."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Install LLM Runtime — llama-cpp-python[server]"
        )
        self.resize(720, 620)
        self.setModal(True)

        self.worker: SSEWorker | None = None
        self._is_uninstall = False

        self._build_ui()
        self._load_status()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Status header.
        self.status_label = QLabel("Checking…")
        self.status_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; color: #e6e6e6;"
        )
        layout.addWidget(self.status_label)

        # Meta info.
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(
            "font-size: 11px; color: #888; "
            "font-family: ui-monospace, 'Consolas', monospace;"
        )
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        # Recommendation banner.
        self.recommendation_label = QLabel("")
        self.recommendation_label.setStyleSheet(
            "font-size: 12px; color: #6cffdb; padding: 8px 12px; "
            "background: rgba(108, 255, 219, 0.06); "
            "border-left: 3px solid #6cffdb; border-radius: 4px;"
        )
        self.recommendation_label.setWordWrap(True)
        layout.addWidget(self.recommendation_label)

        # Wheel selector row.
        row = QHBoxLayout()
        row.addWidget(QLabel("Wheel:"))

        self.wheel_combo = QComboBox()
        for value, label in WHEEL_LABELS:
            self.wheel_combo.addItem(label, value)
        self.wheel_combo.setStyleSheet(
            "QComboBox { padding: 6px 10px; font-size: 13px; "
            "background: #1a1d24; color: #e6e6e6; "
            "border: 1px solid #333; border-radius: 6px; "
            "min-width: 320px; }"
            "QComboBox::drop-down { border: none; }"
        )
        row.addWidget(self.wheel_combo, stretch=1)

        self.btn_install = QPushButton("Install")
        self.btn_install.setStyleSheet(
            "QPushButton { padding: 8px 20px; background: #2563eb; "
            "color: white; border: none; border-radius: 6px; "
            "font-size: 13px; font-weight: 500; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:disabled { opacity: 0.5; }"
        )
        self.btn_install.clicked.connect(self._on_install)
        row.addWidget(self.btn_install)

        self.btn_uninstall = QPushButton("Uninstall")
        self.btn_uninstall.setStyleSheet(
            "QPushButton { padding: 8px 20px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 6px; "
            "font-size: 13px; }"
            "QPushButton:hover { background: #2a3542; }"
            "QPushButton:disabled { opacity: 0.5; }"
        )
        self.btn_uninstall.clicked.connect(self._on_uninstall)
        row.addWidget(self.btn_uninstall)
        layout.addLayout(row)

        # Upgrade checkbox.
        self.chk_upgrade = QCheckBox(
            "Upgrade to the latest version "
            "(Windows: stop the backend first so DLLs are not locked)"
        )
        self.chk_upgrade.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.chk_upgrade)

        # Progress bar (indeterminate).
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { background: #1a1d24; border: 1px solid #222; "
            "border-radius: 4px; height: 6px; }"
            "QProgressBar::chunk { background: #2563eb; }"
        )
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Log output area.
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #0a0c10; color: #b8c2cc;"
            "  border: 1px solid #222; border-radius: 6px;"
            "  font-family: ui-monospace, 'Consolas', monospace;"
            "  font-size: 11px; padding: 8px;"
            "}"
        )
        layout.addWidget(self.log, stretch=1)

        # Notice banner (success / error).
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        self.notice.setStyleSheet("font-size: 12px; padding: 8px 12px;")
        layout.addWidget(self.notice)

        # Bottom buttons.
        bottom = QHBoxLayout()
        bottom.addStretch(1)

        self.btn_open_browser = QPushButton("Open in browser")
        self.btn_open_browser.setStyleSheet(
            "QPushButton { padding: 6px 14px; background: transparent; "
            "color: #888; border: 1px solid #333; border-radius: 6px; "
            "font-size: 12px; }"
            "QPushButton:hover { color: #e6e6e6; border-color: #555; }"
        )
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        bottom.addWidget(self.btn_open_browser)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(
            "QPushButton { padding: 6px 20px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 6px; "
            "font-size: 13px; }"
            "QPushButton:hover { background: #2a3542; }"
        )
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_close)

        layout.addLayout(bottom)

    # ── Status detection ─────────────────────────────────────────────

    def _load_status(self) -> None:
        """Query /api/llama/install/status and update the UI."""
        try:
            r = httpx.get(
                f"{BACKEND}/api/llama/install/status",
                timeout=5.0,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            self.status_label.setText("Backend not reachable")
            self.status_label.setStyleSheet(
                "font-size: 14px; font-weight: 600; color: #ff6c6c;"
            )
            self.meta_label.setText(str(exc))
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(False)
            return

        # Status line.
        if data.get("installed"):
            version = data.get("version") or "unknown"
            gpu = data.get("gpu_offload")
            gpu_str = (
                "GPU" if gpu is True
                else "CPU" if gpu is False
                else "unknown"
            )
            self.status_label.setText(
                f"✓ Installed — llama-cpp-python {version} ({gpu_str})"
            )
            self.status_label.setStyleSheet(
                "font-size: 14px; font-weight: 600; color: #6cff9b;"
            )
        else:
            self.status_label.setText("Not installed")
            self.status_label.setStyleSheet(
                "font-size: 14px; font-weight: 600; color: #ff6c6c;"
            )

        # Meta line.
        parts = [f"Python: {data.get('python', '?')}"]
        if data.get("in_venv"):
            parts.append(f"Venv: {data.get('venv_path')}")
        else:
            parts.append("⚠ Not running in a virtualenv")
        if data.get("cuda_version"):
            parts.append(f"CUDA: {data['cuda_version']}")
        else:
            parts.append("CUDA: not detected")
        self.meta_label.setText("   ·   ".join(parts))

        # Recommendation.
        rec = data.get("recommended_wheel", "cpu")
        reason = data.get("recommended_reason", "")
        self.recommendation_label.setText(
            f"Recommended: {rec} — {reason}"
        )

        # Preselect the recommended wheel.
        idx = self.wheel_combo.findData(rec)
        if idx >= 0:
            self.wheel_combo.setCurrentIndex(idx)

        # Button states.
        self.btn_install.setEnabled(True)
        self.btn_install.setText(
            "Install" if not data.get("installed") else "Reinstall"
        )
        self.btn_uninstall.setEnabled(bool(data.get("installed")))

    # ── Install / uninstall ──────────────────────────────────────────

    def _on_install(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        wheel = self.wheel_combo.currentData()
        upgrade = self.chk_upgrade.isChecked()

        self.log.clear()
        self._append_log(
            f"▶ Installing llama-cpp-python[server] with wheel '{wheel}'…"
        )
        self._append_log(f"  Upgrade: {upgrade}")
        self._append_log("")
        self._set_running(True)
        self._hide_notice()
        self._is_uninstall = False

        self.worker = SSEWorker(
            f"{BACKEND}/api/llama/install/install",
            {"wheel_index": wheel, "upgrade": upgrade},
            label="install",
        )
        self.worker.event.connect(self._on_sse_event)
        self.worker.start()

    def _on_uninstall(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        reply = QMessageBox.question(
            self,
            "Uninstall llama-cpp-python",
            "Remove llama-cpp-python from the virtual environment?\n\n"
            "On Windows, the backend may need to be stopped first "
            "so the native DLLs are released.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.log.clear()
        self._append_log("▶ Uninstalling llama-cpp-python…")
        self._append_log("")
        self._set_running(True)
        self._hide_notice()
        self._is_uninstall = True

        self.worker = SSEWorker(
            f"{BACKEND}/api/llama/install/uninstall",
            None,
            label="uninstall",
        )
        self.worker.event.connect(self._on_sse_event)
        self.worker.start()

    # ── SSE event handling ───────────────────────────────────────────

    def _on_sse_event(self, ev: dict) -> None:
        t = ev.get("type")

        if t == "start":
            self._append_log(f"▶ {ev.get('command', '')}")
            if ev.get("venv"):
                self._append_log(f"  venv: {ev['venv']}")
            self._append_log("")

        elif t == "line":
            self._append_log(ev.get("text", ""))

        elif t == "success":
            if self._is_uninstall:
                self._show_notice(
                    "success",
                    "✓ Uninstalled llama-cpp-python. Restart the "
                    "backend to fully release the library.",
                )
            else:
                version = ev.get("version") or ""
                gpu = ev.get("gpu_offload")
                gpu_str = (
                    "GPU" if gpu is True
                    else "CPU" if gpu is False
                    else "unknown"
                )
                self._show_notice(
                    "success",
                    f"✓ Installed llama-cpp-python {version} ({gpu_str}). "
                    f"Restart the backend to use it.",
                )
            self._set_running(False)
            self._load_status()

        elif t == "error":
            self._show_notice("error", f"✗ {ev.get('message', '')}")
            self._set_running(False)

        elif t == "done":
            self._set_running(False)

    # ── UI helpers ───────────────────────────────────────────────────

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_running(self, running: bool) -> None:
        self.btn_install.setEnabled(not running)
        self.btn_uninstall.setEnabled(not running)
        self.wheel_combo.setEnabled(not running)
        self.chk_upgrade.setEnabled(not running)
        self.progress.setVisible(running)

    def _show_notice(self, kind: str, message: str) -> None:
        colors = {
            "success": ("#6cff9b", "rgba(108, 255, 155, 0.08)"),
            "error": ("#ff9494", "rgba(239, 68, 68, 0.08)"),
            "warn": ("#ffcb6c", "rgba(255, 203, 108, 0.08)"),
        }
        fg, bg = colors.get(kind, colors["warn"])
        self.notice.setStyleSheet(
            f"font-size: 12px; padding: 8px 12px; "
            f"color: {fg}; background: {bg}; "
            f"border-left: 3px solid {fg}; border-radius: 4px;"
        )
        self.notice.setText(message)
        self.notice.setVisible(True)

    def _hide_notice(self) -> None:
        self.notice.setVisible(False)
        self.notice.setText("")

    def _open_in_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(f"{BACKEND}/settings"))

    # ── Close event ──────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Installation in progress",
                "An installation is still running. Close anyway?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # Do not kill the SSE reader thread — let it finish.
            # The QThread will outlive the dialog briefly, which is
            # acceptable; the pip subprocess on the backend will
            # continue to completion regardless.
        event.accept()