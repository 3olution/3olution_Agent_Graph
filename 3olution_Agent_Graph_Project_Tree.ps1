<#
.SYNOPSIS
    Recreates the 3olution-agent-graph directory tree on Windows.

.DESCRIPTION
    Creates every directory shown in the project tree. Optionally also creates
    empty placeholder files (useful when you want the skeleton in git).

.PARAMETER Root
    Target directory. Defaults to the current directory.

.PARAMETER WithFiles
    Also create empty placeholder files.

.PARAMETER Force
    Allow writing into a non-empty existing directory without prompting.

.EXAMPLE
    .\New-ProjectTree.ps1 -Root C:\dev\3olution-agent-graph -WithFiles
#>

[CmdletBinding()]
param(
    [string]$Root = (Get-Location).Path,
    [switch]$WithFiles,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- directories
$directories = @(
    'bootstrap'
    'runtime'
    'runtime/python'
    'runtime/php'
    'runtime/sqlite'
    'runtime/_downloads'

    'backend'
    'backend/routes'
    'backend/graph'
    'backend/graph/custom_nodes'

    'mcp_server'
    'mcp_server/tools'

    'desktop'

    'web'
    'web/vendor'

    'php_app'
    'php_app/static'
    'php_app/data'

    'models'
    'logs'
    'tests'
    'scripts'
)

# ---------------------------------------------------------------- files
$files = @(
    # root
    '.env.example'
    '.gitignore'
    'LICENSE'
    'README.md'
    'CONTRIBUTING.md'
    'CHANGES.md'
    'HOW_TO_USE.md'
    'INSTALLATION.md'
    'STARTUP.md'
    'ERROR_HANDLING.md'
    'FAQ.md'
    'FUTURE_EXTENSIONS.md'
    'requirements.txt'
    'run.py'
    'pyinstaller.spec'
    'setup.sh'
    'setup.ps1'
    'start.sh'
    'start.bat'

    'bootstrap/setup.py'
    'runtime/runtime.env'

    # backend
    'backend/__init__.py'
    'backend/main.py'
    'backend/state.py'
    'backend/config.py'
    'backend/retry.py'
    'backend/storage.py'
    'backend/thinking.py'
    'backend/agent.py'
    'backend/llm.py'
    'backend/logging_config.py'
    'backend/mcp_client.py'
    'backend/ollama.py'
    'backend/models_setup.py'
    'backend/llama_installer.py'
    'backend/llama_server.py'
    'backend/php_server.py'

    'backend/routes/__init__.py'
    'backend/routes/ollama_routes.py'
    'backend/routes/llama_routes.py'
    'backend/routes/php_routes.py'
    'backend/routes/system_routes.py'

    # graph
    'backend/graph/__init__.py'
    'backend/graph/engine.py'
    'backend/graph/model.py'
    'backend/graph/nodes.py'
    'backend/graph/registry.py'
    'backend/graph/routes.py'

    'backend/graph/custom_nodes/__init__.py'
    'backend/graph/custom_nodes/README.md'
    'backend/graph/custom_nodes/example_nodes.py'

    # mcp server
    'mcp_server/__init__.py'
    'mcp_server/server.py'
    'mcp_server/_server.py'

    'mcp_server/tools/__init__.py'
    'mcp_server/tools/README.md'
    'mcp_server/tools/math_tools.py'
    'mcp_server/tools/time_tools.py'
    'mcp_server/tools/filesystem_tools.py'
    'mcp_server/tools/text_tools.py'

    # desktop
    'desktop/__init__.py'
    'desktop/app.py'
    'desktop/native_chat.py'
    'desktop/backend_launcher.py'
    'desktop/install_dialog.py'
    'desktop/logging_config.py'

    # web
    'web/index.html'
    'web/styles.css'
    'web/app.js'
    'web/graph.html'
    'web/graph.css'
    'web/graph.js'
    'web/settings.html'
    'web/settings.css'
    'web/settings.js'
    'web/vendor/litegraph.min.js'
    'web/vendor/litegraph.css'

    # php app
    'php_app/index.php'
    'php_app/demo.php'
    'php_app/db.php'
    'php_app/style.css'
    'php_app/static/style.css'
    'php_app/data/app.db'

    # misc
    'models/README.md'
    'tests/__init__.py'
    'tests/test_engine.py'
    'tests/test_nodes.py'
    'scripts/serve.py'
    'scripts/download_vendor.py'
    'scripts/download_vendor.sh'
    'scripts/download_vendor.ps1'
)

# ---------------------------------------------------------------- run
if (-not (Test-Path -LiteralPath $Root)) {
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    Write-Host "Created root: $Root"
}

$existing = Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    Write-Warning "Target directory '$Root' is not empty."
    $answer = Read-Host 'Continue and merge into it? [y/N]'
    if ($answer -notmatch '^[Yy]') {
        Write-Host 'Aborted.'
        return
    }
}

$createdDirs  = 0
$createdFiles = 0
$skipped      = 0

foreach ($rel in $directories) {
    $path = Join-Path $Root $rel
    if (Test-Path -LiteralPath $path) {
        $skipped++
    } else {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Write-Host "  [dir]  $rel"
        $createdDirs++
    }
}

if ($WithFiles) {
    foreach ($rel in $files) {
        $path = Join-Path $Root $rel
        $parent = Split-Path -Path $path -Parent
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        if (Test-Path -LiteralPath $path) {
            $skipped++
        } else {
            New-Item -ItemType File -Path $path -Force | Out-Null
            Write-Host "  [file] $rel"
            $createdFiles++
        }
    }
}

Write-Host ''
Write-Host "Done. Root: $Root"
Write-Host ("  Directories created : {0}" -f $createdDirs)
if ($WithFiles) {
    Write-Host ("  Files created       : {0}" -f $createdFiles)
}
Write-Host ("  Already existed     : {0}" -f $skipped)