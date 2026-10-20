# ======================================================================
# 3olution Agent Graph - portable setup (Windows)
# https://3olution.com
#
# This script is a thin wrapper around bootstrap/setup.py. Its only
# job is to find a usable Python 3.8+ interpreter on PATH and hand
# off to the actual bootstrap script.
#
# Usage (PowerShell):
#   .\setup.ps1                     install everything
#   .\setup.ps1 --force             re-download everything
#   .\setup.ps1 --skip-php          skip the PHP runtime
#   .\setup.ps1 --check             report what's installed
#
# Usage (cmd.exe):
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#
# If PowerShell blocks this script with a security error, run it once
# with -ExecutionPolicy Bypass as shown above. Alternatively, relax
# the policy for your user account:
#
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#
# After setup completes, launch the application with start.bat.
#
# NOTE ON ENCODING
#   This file is deliberately ASCII-only. Every earlier version that
#   used bullet characters (U+2022) or box-drawing characters broke
#   on Windows PowerShell 5.1, which reads .ps1 files as Windows-1252
#   unless they carry a UTF-8 BOM. ASCII output sidesteps the issue
#   entirely.
# ======================================================================

#Requires -Version 5.0

# ----------------------------------------------------------------------
#  Strict mode
# ----------------------------------------------------------------------
# Version 2.0 is used instead of Latest because Latest enforces rules
# that trip up untyped positional parameters (the "$msg not set" error
# that appears when an unsupplied parameter is later read). Version 2.0
# still catches uninitialized variables and bad function calls.

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------
#  Resolve the project root
# ----------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ----------------------------------------------------------------------
#  Output helpers
# ----------------------------------------------------------------------
# Every helper takes an explicitly typed [string] parameter with a
# default value. That guarantees the parameter is bound even when the
# caller passes nothing, which prevents the StrictMode complaint that
# plagued the previous version.

function Write-Banner {
    Write-Host ""
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "  3olution Agent Graph - Portable Setup" -ForegroundColor Cyan
    Write-Host "  https://3olution.com" -ForegroundColor Cyan
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Info {
    param([string]$Message = "")
    Write-Host "  [info]  $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message = "")
    Write-Host "  [ok]    $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message = "")
    Write-Host "  [warn]  $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message = "")
    Write-Host "  [error] $Message" -ForegroundColor Red
}

# ----------------------------------------------------------------------
#  Print banner
# ----------------------------------------------------------------------

Write-Banner

# ----------------------------------------------------------------------
#  Verify the bootstrap script exists
# ----------------------------------------------------------------------

$Bootstrap = Join-Path $ScriptDir "bootstrap\setup.py"

if (-not (Test-Path $Bootstrap)) {
    Write-Err "Bootstrap script not found: $Bootstrap"
    Write-Err "Are you running this from the project root?"
    exit 1
}

# ----------------------------------------------------------------------
#  Find a Python 3.8+ interpreter
# ----------------------------------------------------------------------
# On Windows there are three common ways to get a Python:
#   - python.exe from python.org (installs on PATH)
#   - python.exe from the Microsoft Store
#   - py.exe, the Windows Python launcher, which picks the best
#     installed Python automatically
#
# The py launcher is the most reliable because both python.org and the
# Store register it. python.exe is the second choice. Anything else is
# a last resort.

$PythonCmd = $null
$PythonArgs = @()

# 1. Environment override
if ($env:PYTHON) {
    if (Get-Command $env:PYTHON -ErrorAction SilentlyContinue) {
        $PythonCmd = $env:PYTHON
        Write-Info "Using PYTHON=$($env:PYTHON) from environment"
    } else {
        Write-Warn "PYTHON=$($env:PYTHON) is set but not found on PATH; falling back"
    }
}

# 2. py launcher
if (-not $PythonCmd) {
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        $PythonCmd = "py"
        $PythonArgs = @("-3")
    }
}

# 3. python on PATH
if (-not $PythonCmd) {
    $py = Get-Command "python" -ErrorAction SilentlyContinue
    if ($py) {
        $PythonCmd = "python"
    }
}

# 4. python3 on PATH (rare on Windows, but possible with WSL-adjacent installs)
if (-not $PythonCmd) {
    $py = Get-Command "python3" -ErrorAction SilentlyContinue
    if ($py) {
        $PythonCmd = "python3"
    }
}

if (-not $PythonCmd) {
    Write-Err "No Python interpreter found on PATH."
    Write-Host ""
    Write-Host "  The bootstrap script needs Python 3.8+ to run."
    Write-Host "  It will then download a portable Python into runtime\."
    Write-Host ""
    Write-Host "  Install Python with one of:"
    Write-Host "    winget:           winget install Python.Python.3.12"
    Write-Host "    Microsoft Store:  search for 'Python 3.12'"
    Write-Host "    python.org:       https://www.python.org/downloads/"
    Write-Host ""
    Write-Host "  Be sure to tick 'Add Python to PATH' during installation."
    Write-Host ""
    exit 1
}

# ----------------------------------------------------------------------
#  Verify the version
# ----------------------------------------------------------------------
# Build a small helper that runs python with the resolved launcher args
# and captures the exit code. Using the call operator (&) with a splat
# array is the only way to reliably pass both the launcher args and the
# user's own args through.

function Invoke-Python {
    param([string[]]$Arguments = @())
    $allArgs = $PythonArgs + $Arguments
    & $PythonCmd @allArgs
}

# Version check: exit 0 if 3.8 or newer.
try {
    Invoke-Python @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)") | Out-Null
} catch {
    Write-Err "Failed to invoke Python: $_"
    exit 1
}

$versionExit = $LASTEXITCODE

if ($versionExit -ne 0) {
    # Try to read the actual version for the error message.
    $actual = (& $PythonCmd @($PythonArgs) -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))") 2>$null
    Write-Err "Python $actual found, but 3.8+ is required."
    Write-Host ""
    Write-Host "  Upgrade Python with:"
    Write-Host "    winget upgrade Python.Python.3.12"
    Write-Host "  Or download from: https://www.python.org/downloads/"
    Write-Host ""
    exit 2
}

$pyVersion = (& $PythonCmd @($PythonArgs) -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))") 2>$null
$pyPath = (Get-Command $PythonCmd).Source
Write-Info "Using Python: $pyPath  ($pyVersion)"

# ----------------------------------------------------------------------
#  Hand off to the bootstrap script
# ----------------------------------------------------------------------
# Every argument the user passed to setup.ps1 is forwarded to the
# bootstrap script. That includes --force, --skip-php, --check, and any
# future flags the bootstrap script may add.

$bootstrapArgs = $PythonArgs + @($Bootstrap)
if ($args) {
    $bootstrapArgs += $args
}

Write-Host ""

& $PythonCmd @bootstrapArgs
$exitCode = $LASTEXITCODE

Write-Host ""

if ($exitCode -eq 0) {
    Write-Ok "Setup finished successfully."
    Write-Host ""
    Write-Host "  Start the application with:  start.bat"
    Write-Host ""
} else {
    Write-Err "Setup finished with errors (exit code $exitCode)."
    Write-Host ""
    Write-Host "  To see more detail, run:"
    Write-Host "      python bootstrap\setup.py --verbose"
    Write-Host ""
}

exit $exitCode