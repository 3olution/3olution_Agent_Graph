# -*- mode: python ; coding: utf-8 -*-
"""
3olution Agent Graph — PyInstaller spec.
https://3olution.com

Builds a single-folder distribution of the desktop client. The
backend is bundled as source code and spawned as a subprocess by
backend_launcher.py at runtime.

Build:
    pip install pyinstaller
    pyinstaller pyinstaller.spec

Output:
    dist/3olutionAgentGraph/3olutionAgentGraph        (Unix)
    dist/3olutionAgentGraph/3olutionAgentGraph.exe    (Windows)

================================================================
  WHAT GETS BUNDLED
================================================================

  - desktop/       — the PyQt6 application (entry point)
  - backend/       — the FastAPI backend, spawned at runtime
  - mcp_server/    — the MCP tool server, spawned by the backend
  - web/           — the chat UI, graph editor, settings page
  - scripts/       — serve.py and download_vendor.py
  - php_app/       — the PHP notes app (optional at runtime)
  - models/        — empty; the user drops GGUF files here
  - bootstrap/     — kept for reference
  - .env.example   — for the user to copy

================================================================
  WHAT GETS EXCLUDED
================================================================

  Development-only packages and unused Qt modules are excluded to
  cut roughly 150 MB off the bundle:

    tkinter, matplotlib, numpy, scipy, pandas, pytest, IPython
    PyQt6.QtQuick, PyQt6.QtQml, PyQt6.Qt3DCore, PyQt6.QtBluetooth

  PyQt6-WebEngine is optional. Comment it out if you do not need
  the embedded Chromium view (the native_chat client does not).

================================================================
  WINDOWS NOTES
================================================================

  UPX compression is disabled. It corrupts PyQt6 DLLs on Windows.

  The output is a single folder, not a single file. A one-file
  build with PyQt6-WebEngine is fragile and slow to start (every
  launch extracts ~280 MB of Chromium to a temp folder).

================================================================
  macOS NOTES
================================================================

  Uncomment the BUNDLE() block at the bottom to produce a
  double-clickable .app. Gatekeeper will block the unsigned bundle
  until you run:

      xattr -dr com.apple.quarantine dist/3olutionAgentGraph.app
"""

from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve()

# ── Analysis ─────────────────────────────────────────────────────────

a = Analysis(
    ["desktop/app.py"],

    pathex=[str(PROJECT_ROOT)],

    binaries=[],

    # Everything the packaged app needs at runtime. Paths are
    # (source, dest_dir_inside_bundle).
    datas=[
        ("web", "web"),
        ("backend", "backend"),
        ("mcp_server", "mcp_server"),
        ("scripts", "scripts"),
        ("php_app", "php_app"),
        ("bootstrap", "bootstrap"),
        ("requirements.txt", "."),
        (".env.example", "."),
    ],

    # Modules that PyInstaller cannot discover by static analysis.
    hiddenimports=[
        # Backend
        "backend",
        "backend.main",
        "backend.config",
        "backend.agent",
        "backend.llm",
        "backend.mcp_client",
        "backend.storage",
        "backend.thinking",
        "backend.retry",
        "backend.ollama",
        "backend.models_setup",
        "backend.llama_installer",
        "backend.llama_server",
        "backend.php_server",
        "backend.routes",
        "backend.routes.ollama_routes",
        "backend.routes.llama_routes",
        "backend.routes.php_routes",
        "backend.routes.system_routes",
        "backend.graph",
        "backend.graph.engine",
        "backend.graph.model",
        "backend.graph.nodes",
        "backend.graph.registry",
        "backend.graph.routes",
        "backend.graph.custom_nodes",
        "backend.graph.custom_nodes.example_nodes",

        # MCP
        "mcp_server",
        "mcp_server.server",
        "mcp_server._server",
        "mcp_server.tools",
        "mcp_server.tools.math_tools",
        "mcp_server.tools.time_tools",
        "mcp_server.tools.filesystem_tools",
        "mcp_server.tools.text_tools",

        # uvicorn's default loop and protocol implementations
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",

        # WebSocket support
        "websockets",
        "websockets.legacy",
        "websockets.legacy.server",

        # pydantic v2
        "pydantic",
        "pydantic_core",
        "pydantic_settings",

        # MCP SDK internals
        "mcp.client.stdio",

        # loguru
        "loguru",

        # PyQt6-WebEngine — comment out to skip the embedded browser
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],

    # Excluded to reduce bundle size.
    excludes=[
        # Unused Qt modules
        "PyQt6.QtQuick",
        "PyQt6.QtQml",
        "PyQt6.QtQuickWidgets",
        "PyQt6.Qt3DCore",
        "PyQt6.Qt3DRender",
        "PyQt6.Qt3DInput",
        "PyQt6.Qt3DLogic",
        "PyQt6.Qt3DAnimation",
        "PyQt6.Qt3DExtras",
        "PyQt6.QtBluetooth",
        "PyQt6.QtNfc",
        "PyQt6.QtPositioning",
        "PyQt6.QtSerialPort",
        "PyQt6.QtSql",
        "PyQt6.QtTest",
        "PyQt6.QtDesigner",
        "PyQt6.QtHelp",

        # Dev / scientific — not used at runtime
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PIL",
        "pytest",
        "pytest_asyncio",
        "IPython",
        "jupyter",
        "notebook",
        "setuptools",
        "pip",
    ],

    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# ── Executable ───────────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="3olutionAgentGraph",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # UPX corrupts PyQt6 DLLs — leave off
    console=False,         # GUI app; no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# ── Collection ───────────────────────────────────────────────────────

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="3olutionAgentGraph",
)


# ── macOS .app bundle (uncomment when building on macOS) ─────────────
#
# app = BUNDLE(
#     coll,
#     name="3olution Agent Graph.app",
#     icon=None,
#     bundle_identifier="com.3olution.agentgraph",
#     info_plist={
#         "NSHighResolutionCapable": True,
#         "LSBackgroundOnly": False,
#         "NSRequiresAquaSystemAppearance": False,
#         "LSMinimumSystemVersion": "12.0",
#     },
# )