"""
3olution Agent Graph — main desktop application.
https://3olution.com

A PyQt6 application that embeds the web UI in a QWebEngineView,
wraps it in a native window with menu bar, status bar, and a
collapsible log dock, and manages the backend subprocess via
BackendLauncher.

================================================================
  WHY EMBED THE WEB UI?
================================================================

  The web UI already implements every feature: chat with streaming,
  graph editor, settings, Ollama model management. Rebuilding all
  of that in native QtWidgets would double the maintenance burden.

  QWebEngineView gives us a Chromium renderer inside a native
  window. JavaScript runs normally, WebSockets work, localStorage
  works. The result is a native-looking app with a single source of
  truth for the UI.

================================================================
  STARTUP SEQUENCE
================================================================

  1. Configure logging.
  2. Create QApplication and apply the dark palette.
  3. Create the main window. It shows a loading HTML page while
     the backend warms up.
  4. Start the BackendLauncher. When it emits `started`, navigate
     the WebView to the backend URL.
  5. Poll /api/health every 10 seconds to keep the status bar
     current.

================================================================
  WHAT HAPPENS WITHOUT PYQT6-WEBENGINE?
================================================================

  If `PyQt6.QtWebEngineWidgets` cannot be imported, the app falls
  back to a friendly message with a button to open the UI in the
  system browser. Users in this situation can also launch
  desktop.native_chat for a full native experience.

  The fallback is graceful: the window still opens, the menu bar
  still works, the status bar still shows backend health.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import sys
from pathlib import Path

# ── Third-party HTTP client ──────────────────────────────────────────
# httpx is used by _refresh_health (periodic status polling) and by
# stop-all (POST to /api/system/stop-all). It must be imported at
# module scope so that any function in this module can use it
# without a runtime import dance.
import httpx

from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# ── WebEngine is optional ────────────────────────────────────────────
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineSettings,
        QWebEngineProfile,
    )
    WEBENGINE_AVAILABLE = True
except ImportError:
    QWebEngineView = None  # type: ignore[assignment]
    QWebEngineSettings = None  # type: ignore[assignment]
    QWebEngineProfile = None  # type: ignore[assignment]
    WEBENGINE_AVAILABLE = False

from loguru import logger

from .backend_launcher import BackendLauncher
from .logging_config import setup_desktop_logging


# ══════════════════════════════════════════════════════════════════════
#  Paths and constants
# ══════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
LOG_DIR = PROJECT_ROOT / "logs"
DOWNLOAD_DIR = Path.home() / "Downloads"

SETTINGS_ORG = "3olution"
SETTINGS_APP = "AgentGraph"

# Backend defaults. The desktop client can point at a remote backend
# by setting AGENT_HOST / AGENT_PORT before launching.
DEFAULT_HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("AGENT_PORT", "8000"))
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

# Log dock limits.
MAX_LOG_LINES = 2000
HEALTH_REFRESH_MS = 10_000


# ══════════════════════════════════════════════════════════════════════
#  Logo data URI helper
# ══════════════════════════════════════════════════════════════════════
#
#  The loading screen is shown BEFORE the backend is up, so it cannot
#  reference http://127.0.0.1:8000/logo.png. Instead, we read the logo
#  off disk at import time and inline it as a base64 data URI.
#
#  Search order: web/logo.png, then web/favicon.png, then web/logo.svg,
#  then web/favicon.ico. The first one that exists wins. If none exists
#  (fresh checkout without the asset), an inline SVG with the original
#  gradient "3" is used as a graceful fallback.

def _logo_data_uri() -> str:
    """Return a data URI for the project logo, or an inline fallback."""
    for name, media in (
        ("logo.png",    "image/png"),
        ("favicon.png", "image/png"),
        ("logo.svg",    "image/svg+xml"),
        ("favicon.ico", "image/x-icon"),
    ):
        p = WEB_DIR / name
        try:
            if p.is_file() and p.stat().st_size > 0:
                data = p.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                return f"data:{media};base64,{b64}"
        except Exception as exc:
            logger.debug("Could not load logo {}: {}", p, exc)

    # Fallback: the same gradient "3" the app used before. Keeps the
    # loading screen looking intentional on a checkout without assets.
    logger.warning(
        "No logo found in {}. Using the built-in SVG fallback. "
        "Drop a logo.png into web/ to customise.",
        WEB_DIR,
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#2563eb"/>'
        '<stop offset="1" stop-color="#7c3aed"/>'
        '</linearGradient></defs>'
        '<rect width="32" height="32" rx="8" fill="url(#g)"/>'
        '<text x="16" y="23" font-family="system-ui,sans-serif" '
        'font-size="20" font-weight="700" fill="#fff" '
        'text-anchor="middle">3</text></svg>'
    )
    import urllib.parse
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


_LOGO_DATA_URI = _logo_data_uri()


# ══════════════════════════════════════════════════════════════════════
#  Access log filtering
# ══════════════════════════════════════════════════════════════════════
#
#  The web UI polls /api/ollama/ps, /api/llama/runtime/status, and
#  /api/health every few seconds. Without filtering, those requests
#  flood the desktop log dock and the backend log file.
#
#  We filter out access-log lines whose status code is 2xx or 3xx.
#  4xx and 5xx lines still pass through — those are real problems.

_ACCESS_LOG_RE = re.compile(
    r'^INFO:\s+\S+\s+-\s+"[A-Z]+\s+\S+\s+HTTP/[\d.]+"\s+(\d{3})\s',
)


def _is_routine_access_log(line: str) -> bool:
    """True if the line is a successful HTTP access log entry."""
    m = _ACCESS_LOG_RE.match(line)
    if not m:
        return False
    try:
        code = int(m.group(1))
    except ValueError:
        return False
    return 200 <= code < 400


# ══════════════════════════════════════════════════════════════════════
#  Download handling
# ══════════════════════════════════════════════════════════════════════

def _handle_download(request) -> None:
    """
    Handle a download initiated by the embedded browser.

    Shows a Save As dialog with a sensible default filename. If the
    user cancels, the download is cancelled.
    """
    try:
        suggested = request.downloadFileName() or "download"
    except Exception:
        suggested = "download"

    target, _ = QFileDialog.getSaveFileName(
        None,
        "Save File",
        str(DOWNLOAD_DIR / suggested),
        "All Files (*.*)",
    )
    if not target:
        request.cancel()
        logger.info("Download cancelled by user: {}", suggested)
        return

    try:
        request.setDownloadDirectory(str(Path(target).parent))
        request.setDownloadFileName(Path(target).name)
        request.accept()
        logger.info("Download accepted: {} → {}", suggested, target)
    except Exception as exc:
        logger.warning("Could not accept download: {}", exc)
        try:
            request.cancel()
        except Exception:
            pass


def _connect_download_handler() -> None:
    """
    Wire the download handler into QtWebEngine's default profile.

    QtWebEngine has no "enable downloads" flag — downloads are
    always enabled. The only requirement is connecting to the
    profile's downloadRequested signal.
    """
    if not WEBENGINE_AVAILABLE:
        return
    try:
        profile = QWebEngineProfile.defaultProfile()
        profile.downloadRequested.connect(_handle_download)
        logger.debug("Download handler connected")
    except Exception as exc:
        logger.warning("Could not connect download handler: {}", exc)


# ══════════════════════════════════════════════════════════════════════
#  Loading screen
# ══════════════════════════════════════════════════════════════════════

LOADING_HTML = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0; padding: 0;
    height: 100%;
    background: #0f1116;
    color: #e6e6e6;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  body {{ display: flex; align-items: center; justify-content: center; }}
  .box {{ text-align: center; }}
  .logo {{
    display: inline-block;
    width: 168px; height: 168px;
    object-fit: contain;
    vertical-align: middle;
    margin-bottom: 20px;
  }}
  h1 {{ font-size: 22px; margin: 0 0 8px; font-weight: 600; }}
  p {{ color: #888; margin: 0; font-size: 14px; }}
  .spinner {{
    margin-top: 28px;
    display: inline-block;
    width: 24px; height: 24px;
    border: 2px solid #333;
    border-top-color: #2563eb;
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
  <div class="box">
    <img src="{_LOGO_DATA_URI}" class="logo" alt="3olution">
    <h1>Starting 3olution Agent Graph…</h1>
    <p id="msg">Waiting for the backend to become ready.</p>
    <div class="spinner"></div>
  </div>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════
#  Log viewer dialog
# ══════════════════════════════════════════════════════════════════════

class LogViewerDialog(QDialog):
    """
    Modal dialog showing the tail of the most recent log file.

    Reads logs/backend-*.log and logs/desktop-*.log, letting the
    user choose which source and how many lines. Refreshes on
    demand — no live tailing, which keeps the implementation
    simple.
    """

    def __init__(self, log_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("3olution Agent Graph — Log Viewer")
        self.resize(1000, 640)
        self.log_dir = log_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── Controls row ─────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("Source:"))

        self.source_combo = QComboBox()
        self.source_combo.addItem("Backend + Desktop", "all")
        self.source_combo.addItem("Backend only", "backend")
        self.source_combo.addItem("Desktop only", "desktop")
        self.source_combo.setStyleSheet(
            "QComboBox { padding: 4px 8px; background: #1a1d24; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 4px; }"
        )
        self.source_combo.currentIndexChanged.connect(self._reload)
        top.addWidget(self.source_combo)

        top.addSpacing(16)
        top.addWidget(QLabel("Lines:"))

        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(100, 10000)
        self.lines_spin.setValue(1000)
        self.lines_spin.setSingleStep(100)
        self.lines_spin.setStyleSheet(
            "QSpinBox { padding: 4px 8px; background: #1a1d24; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 4px; }"
        )
        self.lines_spin.valueChanged.connect(self._reload)
        top.addWidget(self.lines_spin)

        top.addStretch(1)

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._reload)
        refresh_btn.setStyleSheet(
            "QPushButton { padding: 6px 14px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 4px; }"
            "QPushButton:hover { background: #2a3542; }"
        )
        top.addWidget(refresh_btn)

        open_folder_btn = QPushButton("Open Log Folder")
        open_folder_btn.clicked.connect(self._open_folder)
        open_folder_btn.setStyleSheet(
            "QPushButton { padding: 6px 14px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 4px; }"
            "QPushButton:hover { background: #2a3542; }"
        )
        top.addWidget(open_folder_btn)

        layout.addLayout(top)

        # ── Info label ───────────────────────────────────────────────
        self.info_label = QLabel(f"Log directory: {self.log_dir}")
        self.info_label.setStyleSheet(
            "color: #6b7280; font-size: 11px; "
            "font-family: ui-monospace, monospace;"
        )
        layout.addWidget(self.info_label)

        # ── Text area ────────────────────────────────────────────────
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "QPlainTextEdit {"
            "  background: #0a0c10; color: #b8c2cc;"
            "  border: 1px solid #222; border-radius: 6px;"
            "  font-family: ui-monospace, 'Consolas', monospace;"
            "  font-size: 11px; padding: 8px;"
            "}"
        )
        layout.addWidget(self.text, stretch=1)

        # ── Close button ─────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(
            "QPushButton { padding: 6px 20px; background: #1f2937; "
            "color: #e6e6e6; border: 1px solid #333; border-radius: 4px; }"
            "QPushButton:hover { background: #2a3542; }"
        )
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self._reload()

    def _reload(self) -> None:
        """Read the tail of the newest matching log file."""
        source = self.source_combo.currentData()
        n_lines = self.lines_spin.value()

        if source == "backend":
            patterns = ["backend-*.log"]
        elif source == "desktop":
            patterns = ["desktop-*.log"]
        else:
            patterns = ["backend-*.log", "desktop-*.log"]

        files: list[Path] = []
        for pat in patterns:
            files.extend(self.log_dir.glob(pat))

        if not files:
            self.text.setPlainText(
                f"(no log files found in {self.log_dir})"
            )
            return

        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest = files[0]

        try:
            with latest.open("r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            tail = all_lines[-n_lines:]
            self.text.setPlainText("".join(tail))
            self.info_label.setText(
                f"File: {latest.name}   |   "
                f"showing last {len(tail)} of {len(all_lines)} lines   |   "
                f"Log directory: {self.log_dir}"
            )
        except Exception as exc:
            self.text.setPlainText(f"Error reading {latest}: {exc}")

        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _open_folder(self) -> None:
        """Open the logs folder in the system file manager."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))


# ══════════════════════════════════════════════════════════════════════
#  Fallback widget when WebEngine is not available
# ══════════════════════════════════════════════════════════════════════

class NoWebEngineWidget(QWidget):
    """
    Widget shown when PyQt6-WebEngine is not installed.

    Provides a clear explanation and a button to open the UI in
    the system browser.
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        title = QLabel("Embedded browser not available")
        title.setStyleSheet(
            "font-size: 18px; font-weight: 600; color: #e6e6e6;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        msg = QLabel(
            "The desktop app normally embeds the web UI in a Chromium "
            "view. That requires the <code>PyQt6-WebEngine</code> "
            "package.<br><br>"
            "Install it with:<br>"
            "<code style='background:#1a1d24;padding:2px 8px;"
            "border-radius:4px;'>pip install PyQt6-WebEngine</code>"
            "<br><br>"
            "Or click below to open in your system browser."
        )
        msg.setStyleSheet("color: #b8c2cc; font-size: 14px;")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)

        open_btn = QPushButton("Open in browser")
        open_btn.setStyleSheet(
            "QPushButton {"
            "  background: #2563eb; color: white; border: none;"
            "  border-radius: 8px; padding: 10px 24px;"
            "  font-size: 14px; font-weight: 500;"
            "}"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        open_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        open_btn.clicked.connect(self._open_in_browser)
        open_btn.setMaximumWidth(240)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(msg)
        layout.addSpacing(12)

        btn_row = QWidget()
        btn_layout = QVBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addWidget(
            open_btn, alignment=Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(btn_row)
        layout.addStretch(2)

    def _open_in_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(self.url))


# ══════════════════════════════════════════════════════════════════════
#  Main window
# ══════════════════════════════════════════════════════════════════════

class DesktopApp(QMainWindow):
    """The main PyQt6 window."""

    def __init__(self) -> None:
        super().__init__()

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        # Backend launcher.
        self.backend = BackendLauncher(
            project_root=PROJECT_ROOT,
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
        )
        self.backend.started.connect(self._on_backend_started)
        self.backend.stopped.connect(self._on_backend_stopped)
        self.backend.failed.connect(self._on_backend_failed)
        self.backend.log.connect(self._on_backend_log)

        # UI state.
        self._current_path = "/"
        self._health_timer: QTimer | None = None
        self._suppress_close_prompt = False
        self._backend_lines: list[str] = []

        self._setup_window()
        self._setup_central()
        self._setup_log_dock()
        self._setup_actions()
        self._setup_toolbar()
        self._setup_menu()
        self._setup_status_bar()

        logger.info("Desktop app started")

        # Start the backend after the event loop begins processing.
        QTimer.singleShot(50, self.backend.start)

    # ── Setup ────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        """Restore saved geometry or pick a reasonable default."""
        self.setWindowTitle("3olution Agent Graph")
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1400, 900)

    def _setup_central(self) -> None:
        """
        Create the central widget.

        With WebEngine: a QWebEngineView showing the loading HTML.
        Without WebEngine: a NoWebEngineWidget.
        """
        if WEBENGINE_AVAILABLE:
            _connect_download_handler()

            self.web_view = QWebEngineView()
            s = self.web_view.settings()
            s.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled, True
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.LocalStorageEnabled, True
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
                True,
            )
            s.setAttribute(
                QWebEngineSettings.WebAttribute.PluginsEnabled, False
            )
            self.web_view.setHtml(LOADING_HTML)
            self.setCentralWidget(self.web_view)
        else:
            self.web_view = None
            self.setCentralWidget(NoWebEngineWidget(DEFAULT_URL))

    def _setup_log_dock(self) -> None:
        """Create the collapsible backend log dock at the bottom."""
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "QTextEdit {"
            "  background: #0f1116;"
            "  color: #b8c2cc;"
            "  border: none;"
            "  font-family: ui-monospace, 'Consolas', monospace;"
            "  font-size: 11px;"
            "  padding: 8px;"
            "}"
        )

        self.log_dock = QDockWidget("Backend Log", self)
        self.log_dock.setObjectName("backend_log_dock")
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock
        )
        self.log_dock.hide()

    def _setup_actions(self) -> None:
        """Create every QAction used by the toolbar and menus."""

        self.act_home = QAction("Chat", self)
        self.act_home.setShortcut(QKeySequence("Ctrl+1"))
        self.act_home.triggered.connect(lambda: self._navigate("/"))

        self.act_graph = QAction("Graph", self)
        self.act_graph.setShortcut(QKeySequence("Ctrl+2"))
        self.act_graph.triggered.connect(lambda: self._navigate("/graph"))

        self.act_settings = QAction("Settings", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+3"))
        self.act_settings.triggered.connect(
            lambda: self._navigate("/settings")
        )

        self.act_start_backend = QAction("Start Backend", self)
        self.act_start_backend.triggered.connect(self._start_backend)

        self.act_stop_backend = QAction("Stop Backend", self)
        self.act_stop_backend.triggered.connect(self._stop_backend)
        self.act_stop_backend.setEnabled(False)

        self.act_stop_all = QAction("Stop All Processes…", self)
        self.act_stop_all.setShortcut(QKeySequence("Ctrl+Shift+Q"))
        self.act_stop_all.triggered.connect(self._stop_all)
        self.act_stop_all.setEnabled(False)

        self.act_install_runtime = QAction("Install LLM Runtime…", self)
        self.act_install_runtime.triggered.connect(
            self._open_install_dialog
        )

        self.act_view_logs = QAction("View Logs…", self)
        self.act_view_logs.setShortcut(QKeySequence("Ctrl+L"))
        self.act_view_logs.triggered.connect(self._open_log_viewer)

        self.act_open_log_folder = QAction("Open Log Folder", self)
        self.act_open_log_folder.triggered.connect(self._open_log_folder)

        self.act_reload = QAction("Reload Page", self)
        self.act_reload.setShortcut(QKeySequence("F5"))
        self.act_reload.triggered.connect(self._reload)

        self.act_open_browser = QAction("Open in Browser", self)
        self.act_open_browser.setShortcut(QKeySequence("Ctrl+B"))
        self.act_open_browser.triggered.connect(self._open_in_browser)

        self.act_toggle_log = QAction("Show Backend Log", self)
        self.act_toggle_log.setCheckable(True)
        self.act_toggle_log.toggled.connect(self._toggle_log_dock)

        self.act_about = QAction("About", self)
        self.act_about.triggered.connect(self._show_about)

        self.act_quit = QAction("Quit", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)

    def _setup_toolbar(self) -> None:
        """Create the top toolbar."""
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setStyleSheet(
            "QToolBar {"
            "  background: #14161b;"
            "  border-bottom: 1px solid #222;"
            "  padding: 6px;"
            "  spacing: 4px;"
            "}"
            "QToolButton {"
            "  color: #e6e6e6;"
            "  background: #1f2937;"
            "  border: 1px solid #333;"
            "  border-radius: 6px;"
            "  padding: 5px 12px;"
            "  font-size: 13px;"
            "}"
            "QToolButton:hover { background: #2a3542; }"
            "QToolButton:disabled { opacity: 0.5; color: #666; }"
            "QToolButton:checked { background: #2563eb; "
            "border-color: #2563eb; }"
        )
        tb.addAction(self.act_start_backend)
        tb.addAction(self.act_stop_backend)
        tb.addSeparator()
        tb.addAction(self.act_home)
        tb.addAction(self.act_graph)
        tb.addAction(self.act_settings)
        tb.addSeparator()
        tb.addAction(self.act_reload)
        tb.addAction(self.act_open_browser)
        self.addToolBar(tb)

    def _setup_menu(self) -> None:
        """Create the menu bar."""
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction(self.act_open_browser)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self.act_home)
        view_menu.addAction(self.act_graph)
        view_menu.addAction(self.act_settings)
        view_menu.addSeparator()
        view_menu.addAction(self.act_reload)
        view_menu.addSeparator()
        view_menu.addAction(self.act_toggle_log)

        backend_menu = mb.addMenu("&Backend")
        backend_menu.addAction(self.act_start_backend)
        backend_menu.addAction(self.act_stop_backend)
        backend_menu.addSeparator()
        backend_menu.addAction(self.act_stop_all)
        backend_menu.addSeparator()
        backend_menu.addAction(self.act_install_runtime)

        logs_menu = mb.addMenu("&Logs")
        logs_menu.addAction(self.act_view_logs)
        logs_menu.addAction(self.act_open_log_folder)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _setup_status_bar(self) -> None:
        """Create the status bar with the health label and URL label."""
        self.status_label = QLabel("Backend: starting…")
        self.status_label.setStyleSheet(
            "color: #b8c2cc; padding: 2px 8px;"
        )
        self.url_label = QLabel(DEFAULT_URL)
        self.url_label.setStyleSheet(
            "color: #888; padding: 2px 8px; "
            "font-family: ui-monospace, monospace; font-size: 11px;"
        )
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.url_label)
        self.statusBar().setStyleSheet(
            "QStatusBar { background: #14161b; color: #b8c2cc;"
            " border-top: 1px solid #222; }"
        )

    # ── Backend callbacks ────────────────────────────────────────────

    def _on_backend_started(self) -> None:
        """Called when the backend answers /api/health."""
        logger.info("Backend started at {}", DEFAULT_URL)
        self.status_label.setText("Backend: running")
        self.act_start_backend.setEnabled(False)
        self.act_stop_backend.setEnabled(self.backend.spawned_by_us)
        self.act_stop_all.setEnabled(True)

        if self.web_view is not None:
            self.web_view.setUrl(QUrl(f"{DEFAULT_URL}{self._current_path}"))

        # Start the periodic health refresh.
        if self._health_timer is None:
            self._health_timer = QTimer(self)
            self._health_timer.timeout.connect(self._refresh_health)
            self._health_timer.start(HEALTH_REFRESH_MS)
        QTimer.singleShot(300, self._refresh_health)

    def _on_backend_stopped(self) -> None:
        """Called when the backend subprocess has exited."""
        logger.info("Backend stopped")
        self.status_label.setText("Backend: stopped")
        self.act_start_backend.setEnabled(True)
        self.act_stop_backend.setEnabled(False)
        self.act_stop_all.setEnabled(False)

        if self._health_timer is not None:
            self._health_timer.stop()
            self._health_timer = None

        if self.web_view is not None:
            self.web_view.setHtml(LOADING_HTML)

    def _on_backend_failed(self, message: str) -> None:
        """Called when the backend fails to start or exits unexpectedly."""
        logger.error("Backend failed: {}", message)
        self.status_label.setText("Backend: failed")
        self._on_backend_log(f"❌ {message}")

        # Show the last 40 lines of backend output in a details box.
        tail = self._backend_lines[-40:]
        tail_text = (
            "\n".join(tail) if tail else "(no backend output captured)"
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Backend failed")
        box.setText(f"The backend did not start.\n\n{message}")
        box.setDetailedText(tail_text)
        box.setInformativeText(
            "Open the Backend Log (View menu) for the full log, "
            "or run:\n"
            "    python run.py backend\n"
            "in a terminal to see the full traceback."
        )
        box.exec()

    def _on_backend_log(self, line: str) -> None:
        """
        Receive a line of backend stdout/stderr.

        Filters routine access logs (successful HTTP requests to
        status endpoints) so the log file does not flood, but still
        appends them to the GUI dock so users can see live activity.
        """
        if _is_routine_access_log(line):
            # GUI-only, no file write.
            self._append_to_log_dock(line)
            return

        # Route to the appropriate loguru level.
        upper = line.upper()
        if (
            "ERROR" in upper
            or "TRACEBACK" in upper
            or "EXCEPTION" in upper
            or "CRITICAL" in upper
        ):
            logger.error("[backend] {}", line)
        elif "WARNING" in upper or " WARN " in upper:
            logger.warning("[backend] {}", line)
        else:
            logger.info("[backend] {}", line)

        # Ring buffer for the failure dialog.
        self._backend_lines.append(line)
        if len(self._backend_lines) > 500:
            self._backend_lines = self._backend_lines[-500:]

        self._append_to_log_dock(line)

    def _append_to_log_dock(self, line: str) -> None:
        """Append a line to the log dock, trimming old lines."""
        self.log_view.append(line)
        doc = self.log_view.document()
        if doc.blockCount() > MAX_LOG_LINES:
            cursor = self.log_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(
                cursor.MoveOperation.Down,
                cursor.MoveMode.KeepAnchor,
                doc.blockCount() - MAX_LOG_LINES,
            )
            cursor.removeSelectedText()
            cursor.deleteChar()
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_health(self) -> None:
        """
        Periodic /api/health probe to update the status bar.

        Uses the top-level httpx import. The status text shows the
        loaded model, MCP connection state, and whether tools are
        disabled for this session.
        """
        try:
            r = httpx.get(f"{DEFAULT_URL}/api/health", timeout=1.5)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()

            model = data.get("primary_llm", {}).get("model", "?")
            mcp = "connected" if data.get("mcp_connected") else "not connected"
            version = data.get("version", "?")
            disable_tools = data.get("disable_tools", False)
            tools_str = " (no tools)" if disable_tools else ""

            self.status_label.setText(
                f"Backend: running (v{version}){tools_str}   "
                f"Model: {model}   MCP: {mcp}"
            )
        except Exception:
            self.status_label.setText("Backend: not responding")

    # ── Navigation and actions ───────────────────────────────────────

    def _navigate(self, path: str) -> None:
        """Navigate the WebView (or note the path for browser open)."""
        self._current_path = path
        self.url_label.setText(f"{DEFAULT_URL}{path}")
        if self.web_view is not None:
            self.web_view.setUrl(QUrl(f"{DEFAULT_URL}{path}"))

    def _reload(self) -> None:
        """Reload the current page."""
        if self.web_view is not None:
            self.web_view.reload()

    def _open_in_browser(self) -> None:
        """Open the current page in the system browser."""
        QDesktopServices.openUrl(QUrl(f"{DEFAULT_URL}{self._current_path}"))

    def _start_backend(self) -> None:
        """Manually start the backend."""
        self.status_label.setText("Backend: starting…")
        self.act_start_backend.setEnabled(False)
        self.backend.start()

    def _stop_backend(self) -> None:
        """Stop only the subprocess (not the whole system)."""
        if self.backend.spawned_by_us:
            self.backend.stop()
        else:
            self.status_label.setText("Backend: external (not stopping)")
            QMessageBox.information(
                self,
                "External backend",
                "The backend was started outside the desktop app "
                "(for example, from a terminal). It will not be "
                "stopped when this window closes. Use Ctrl+C in "
                "its terminal.",
            )

    def _stop_all(self) -> None:
        """
        Stop the backend, MCP, and llama-cpp via the backend's
        /api/system/stop-all endpoint.
        """
        reply = QMessageBox.question(
            self,
            "Stop all processes",
            "Stop the backend, the MCP server, and any llama-cpp "
            "server?\n\n"
            "The backend will terminate and you will need to restart "
            "it with 'python run.py backend' (or via the Start "
            "Backend menu action).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.status_label.setText("Backend: stopping all…")
        self.act_stop_all.setEnabled(False)
        self.act_stop_backend.setEnabled(False)
        logger.info("Stop-all requested")
        self._on_backend_log("🛑 Stop-all requested. Terminating…")

        try:
            asyncio.run(self.backend.stop_all_async())
            self._on_backend_log("🛑 All processes stopped.")
        except Exception as exc:
            logger.exception("Stop-all failed")
            self._on_backend_log(f"Stop-all failed: {exc}")
            try:
                self.backend.stop()
            except Exception:
                pass

    def _toggle_log_dock(self, checked: bool) -> None:
        """Show or hide the log dock."""
        self.log_dock.setVisible(checked)

    def _open_install_dialog(self) -> None:
        """Open the native llama-cpp install dialog."""
        from .install_dialog import InstallDialog
        dlg = InstallDialog(self)
        dlg.exec()

    def _open_log_viewer(self) -> None:
        """Open the log viewer dialog."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        dlg = LogViewerDialog(LOG_DIR, self)
        dlg.exec()

    def _open_log_folder(self) -> None:
        """Open the logs folder in the system file manager."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))

    def _show_about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(
            self,
            "About 3olution Agent Graph",
            "<h3>3olution Agent Graph</h3>"
            "<p>Dynamic operating graph for AI agents.</p>"
            "<p>Web UI: "
            f"<a href='{DEFAULT_URL}'>{DEFAULT_URL}</a><br>"
            "Project: "
            "<a href='https://3olution.com'>https://3olution.com</a></p>"
            f"<p style='color:#888'>Embedded browser: "
            f"{'Chromium (QtWebEngine)' if WEBENGINE_AVAILABLE else 'not available'}<br>"
            f"Logs: {LOG_DIR}</p>",
        )

    # ── Close ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """
        Save window state and stop the backend if we started it.

        An externally-started backend is deliberately left running,
        because the user started it in a terminal and expects it to
        continue.
        """
        logger.info("Desktop app closing")
        self.settings.setValue("window/geometry", self.saveGeometry())

        if self.backend.spawned_by_us and self.backend.is_running():
            if not self._suppress_close_prompt:
                reply = QMessageBox.question(
                    self,
                    "Quit",
                    "Stop the backend and quit?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self.backend.stop()

        event.accept()


# ══════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    """Application entry point."""
    setup_desktop_logging(
        log_dir=LOG_DIR,
        console_level="INFO",
        file_level="DEBUG",
        retention_days=14,
    )

    # QtWebEngine honors a handful of environment variables. These
    # disable the translate prompt (annoying) and background
    # networking (a minor privacy concern in some environments).
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-features=TranslateUI --disable-background-networking",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("3olution Agent Graph")
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationDisplayName("3olution Agent Graph")
    app.setStyle("Fusion")

    # Apply the dark palette.
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
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#14161b"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e6e6"))
    app.setPalette(palette)

    # Set the window icon from the project logo so the taskbar and
    # window manager pick it up. This is separate from the favicon
    # the browser UI uses.
    try:
        from PyQt6.QtGui import QIcon
        logo_path = WEB_DIR / "logo.png"
        if logo_path.is_file():
            app.setWindowIcon(QIcon(str(logo_path)))
        else:
            ico_path = WEB_DIR / "favicon.ico"
            if ico_path.is_file():
                app.setWindowIcon(QIcon(str(ico_path)))
    except Exception as exc:
        logger.debug("Could not set window icon: {}", exc)

    try:
        window = DesktopApp()
        window.show()
        sys.exit(app.exec())
    except Exception:
        logger.exception("Fatal desktop error")
        raise


if __name__ == "__main__":
    main()