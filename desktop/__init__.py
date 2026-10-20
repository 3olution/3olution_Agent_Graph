"""
3olution Agent Graph — PyQt6 desktop package.
https://3olution.com

Two frontends live here, both backed by the same HTTP/WebSocket API:

    desktop.app          — QtWidgets + QWebEngineView
                            The primary client. Embeds the same web UI
                            the browser sees, wrapped in a native
                            window with menu bar, status bar, log
                            dock, and a Stop All action.

    desktop.native_chat  — QtWidgets only
                            A fallback for environments without
                            PyQt6-WebEngine (headless build machines,
                            minimal Linux installs, macOS installs
                            that skip the WebEngine wheel).

  Plus three support modules:

    backend_launcher     — spawns and monitors the FastAPI backend
                            as a subprocess. Detects an
                            already-running backend and attaches to
                            it instead of starting a duplicate.

    install_dialog       — native installer UI for llama-cpp-python.
                            Detects the local CUDA version and picks
                            the correct wheel automatically.

    logging_config       — loguru sink writing to
                            logs/desktop-YYYY-MM-DD.log with the
                            same rotation and retention rules as
                            the backend.

================================================================
  ENTRY POINTS
================================================================

  From the command line:

      python -m desktop.app          # embedded Chromium
      python -m desktop.native_chat  # native widgets only

  Or via run.py:

      python run.py desktop
      python run.py native

  Or via the packaged executable:

      dist/3olutionAgentGraph/3olutionAgentGraph
"""

__all__ = []