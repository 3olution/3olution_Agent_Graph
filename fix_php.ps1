<#
.SYNOPSIS
    Diagnose and fix PHP execution permissions for 3olution Agent Graph
    on Windows.

.DESCRIPTION
    Resolves the most common causes of the "WinError 5: Access is denied"
    error that appears when the backend tries to spawn php.exe:

        1. Missing or truncated php.exe        -> reports, suggests fix
        2. Mark of the Web (Zone.Identifier)   -> Unblock-File
        3. Incorrect ACLs on runtime\php       -> icacls reset + grant
        4. Windows Defender blocking           -> Add-MpPreference
        5. Third-party antivirus blocking      -> vendor detection + hints

    The script self-elevates via UAC when needed. Every step is
    independent: if one fails, the others still run.

.PARAMETER SkipDefender
    Do not attempt to add Windows Defender exclusions.

.PARAMETER SkipAcl
    Do not attempt to reset file ACLs.

.PARAMETER SkipUnblock
    Do not attempt to remove Mark of the Web.

.PARAMETER NoElevate
    Do not self-elevate. Run in the current user context.
    Useful for testing or when elevation is not available.

.EXAMPLE
    .\fix_php.ps1

.EXAMPLE
    .\fix_php.ps1 -SkipDefender -NoElevate
#>

[CmdletBinding()]
param(
    [switch]$SkipDefender,
    [switch]$SkipAcl,
    [switch]$SkipUnblock,
    [switch]$NoElevate
)

# =====================================================================
#  Strict mode
# =====================================================================
# Version 2.0 throws on uninitialized variables and bad function
# calls, without the extra edge-case rules of Latest that break on
# untyped parameters.

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Continue"

# =====================================================================
#  Output helpers
# =====================================================================
# ASCII-only output. Windows console code pages vary; box-drawing
# characters from earlier versions broke on PS 5.1 with codepage 437.

function Write-Head  { param([string]$m = "") Write-Host $m -ForegroundColor Cyan }
function Write-Step  { param([string]$m = "") Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m = "") Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn  { param([string]$m = "") Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Err   { param([string]$m = "") Write-Host "[x] $m" -ForegroundColor Red }
function Write-Note  { param([string]$m = "") Write-Host "    $m" -ForegroundColor Gray }

# =====================================================================
#  Admin check + self-elevation
# =====================================================================

function Test-Admin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $pr = New-Object Security.Principal.WindowsPrincipal($id)
        return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

if (-not (Test-Admin) -and -not $NoElevate) {
    Write-Host ""
    Write-Warn "Administrator privileges are required to modify ACLs and"
    Write-Warn "Windows Defender settings."
    Write-Host ""
    $answer = Read-Host "Relaunch with elevation? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[Yy]") {
        $childArgs = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$PSCommandPath`"",
            "-NoElevate"
        )
        if ($SkipDefender) { $childArgs += "-SkipDefender" }
        if ($SkipAcl)      { $childArgs += "-SkipAcl" }
        if ($SkipUnblock)  { $childArgs += "-SkipUnblock" }

        try {
            Start-Process -FilePath "powershell.exe" `
                -ArgumentList $childArgs -Verb RunAs -Wait
        } catch {
            Write-Err "Elevation was refused or failed: $($_.Exception.Message)"
            Write-Note "Rerun this script from an Administrator PowerShell to try again."
            exit 1
        }
        exit 0
    } else {
        Write-Warn "Continuing without elevation. Some fixes will be skipped."
        Write-Host ""
    }
}

$IsAdmin = Test-Admin

# =====================================================================
#  Resolve project paths
# =====================================================================

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PhpDir     = Join-Path $ProjectRoot "runtime\php"
$PhpExe     = Join-Path $PhpDir "php.exe"
$PhpAppDir  = Join-Path $ProjectRoot "php_app"
$PhpDataDir = Join-Path $PhpAppDir "data"

# =====================================================================
#  Banner
# =====================================================================

Write-Host ""
Write-Head "================================================================="
Write-Head "  3olution Agent Graph - PHP Permission Fix (Windows)"
Write-Head "  https://3olution.com"
Write-Head "================================================================="
Write-Host ""
Write-Note "Project root: $ProjectRoot"
Write-Note "PHP binary:   $PhpExe"
if ($IsAdmin) {
    Write-Note "Elevation:    administrator"
} else {
    Write-Note "Elevation:    standard user (some fixes will be skipped)"
}
Write-Host ""

# Track outcomes for the final summary.
$Result = [ordered]@{
    BinaryPresent   = $false
    BinaryRuns      = $false
    UnblockOk       = $null
    AclOk           = $null
    DefenderOk      = $null
    ThirdPartyAv    = @()
    ErrorDetail     = ""
}

# =====================================================================
#  Step 1 - Verify the PHP binary exists
# =====================================================================

Write-Step "Step 1 - Verify the PHP binary exists"

if (-not (Test-Path $PhpExe)) {
    Write-Err "Not found: $PhpExe"
    Write-Note "The portable PHP was never extracted, or extraction failed."
    Write-Note "Fix: rerun the bootstrap for PHP only."
    Write-Host ""
    Write-Note "    python bootstrap\setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor"
    Write-Host ""
    $Result.ErrorDetail = "PHP binary missing"
} else {
    $PhpSize = (Get-Item $PhpExe).Length
    $PhpSizeMb = [math]::Round($PhpSize / 1MB, 2)

    if ($PhpSize -lt 1MB) {
        Write-Warn "Present but suspiciously small: $PhpSizeMb MB"
        Write-Note "A working PHP binary is 7-10 MB. This file is probably"
        Write-Note "a partial download or a placeholder. Re-extract it:"
        Write-Host ""
        Write-Note "    rmdir /s /q runtime\php runtime\_downloads"
        Write-Note "    python bootstrap\setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor"
        Write-Host ""
        $Result.ErrorDetail = "PHP binary too small"
    } else {
        Write-Ok "Found: $PhpSizeMb MB"
        $Result.BinaryPresent = $true
    }
}

# =====================================================================
#  Step 2 - Remove Mark of the Web
# =====================================================================

Write-Host ""
Write-Step "Step 2 - Remove Mark of the Web"

if ($SkipUnblock) {
    Write-Warn "Skipped (-SkipUnblock)"
    $Result.UnblockOk = $null
} elseif (-not (Test-Path $PhpDir)) {
    Write-Warn "PHP directory does not exist; nothing to unblock."
    $Result.UnblockOk = $null
} else {
    $blocked = 0
    Get-ChildItem -Path $PhpDir -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $hasMotw = Get-Item -LiteralPath $_.FullName -Stream "Zone.Identifier" `
                -ErrorAction SilentlyContinue
            if ($hasMotw) { $blocked++ }
        } catch {
            # no alternate data stream; nothing to do
        }
    }

    if ($blocked -eq 0) {
        Write-Ok "No Mark of the Web on any file"
    } else {
        Write-Warn "$blocked file(s) carry a Zone.Identifier stream"
        try {
            Get-ChildItem -Path $PhpDir -Recurse -File -ErrorAction SilentlyContinue |
                Unblock-File -ErrorAction Stop
            Write-Ok "Unblocked successfully"
            $Result.UnblockOk = $true
        } catch {
            Write-Err "Unblock-File failed: $($_.Exception.Message)"
            $Result.UnblockOk = $false
        }
    }
}

# =====================================================================
#  Step 3 - Reset ACLs on the PHP directories
# =====================================================================

Write-Host ""
Write-Step "Step 3 - Reset ACLs on runtime\php and php_app"

if ($SkipAcl) {
    Write-Warn "Skipped (-SkipAcl)"
    $Result.AclOk = $null
} elseif (-not $IsAdmin) {
    Write-Warn "Skipped (administrator privileges required)"
    Write-Note "Rerun with elevation to apply ACL fixes."
    $Result.AclOk = $null
} else {
    $targets = @()
    if (Test-Path $PhpDir)     { $targets += $PhpDir }
    if (Test-Path $PhpAppDir)  { $targets += $PhpAppDir }

    if ($targets.Count -eq 0) {
        Write-Warn "Neither runtime\php nor php_app exists."
        $Result.AclOk = $null
    } else {
        $aclOk = $true
        foreach ($target in $targets) {
            try {
                # Reset inheritable permissions to defaults for this
                # tree. This removes any stray DENY entries or
                # restricted inherited lists.
                & icacls $target /reset /T /C /Q 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "icacls /reset returned $LASTEXITCODE"
                }

                # Grant the current user full control recursively.
                & icacls $target /grant "${env:USERNAME}:(OI)(CI)F" /T /C /Q 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "icacls /grant returned $LASTEXITCODE"
                }

                Write-Ok "Reset + granted on $target"
            } catch {
                Write-Err "ACL fix failed for ${target}: $($_.Exception.Message)"
                $aclOk = $false
            }
        }
        $Result.AclOk = $aclOk
    }
}

# =====================================================================
#  Step 4 - Windows Defender exclusions
# =====================================================================

Write-Host ""
Write-Step "Step 4 - Windows Defender exclusions"

if ($SkipDefender) {
    Write-Warn "Skipped (-SkipDefender)"
    $Result.DefenderOk = $null
} elseif (-not $IsAdmin) {
    Write-Warn "Skipped (administrator privileges required)"
    $Result.DefenderOk = $null
} else {
    # Is Defender actually active? If a third-party AV is running,
    # Defender is disabled and Add-MpPreference returns 0x800106ba.
    $defenderActive = $false
    try {
        $mpStatus = Get-MpComputerStatus -ErrorAction Stop
        if ($mpStatus.AntivirusEnabled -eq $true) {
            $defenderActive = $true
        }
    } catch {
        # Get-MpComputerStatus not available or Defender not installed.
        $defenderActive = $false
    }

    if (-not $defenderActive) {
        Write-Warn "Windows Defender is not the active antivirus."
        Write-Note "Skipping Defender exclusions. See Step 5 for the"
        Write-Note "third-party antivirus detection."
        $Result.DefenderOk = $null
    } else {
        $paths = @()
        if (Test-Path $PhpDir)     { $paths += $PhpDir }
        if (Test-Path $PhpAppDir)  { $paths += $PhpAppDir }

        $defenderOk = $true
        foreach ($p in $paths) {
            try {
                Add-MpPreference -ExclusionPath $p -ErrorAction Stop
                Write-Ok "Excluded: $p"
            } catch {
                $msg = $_.Exception.Message
                if ($msg -match "0x800106ba") {
                    Write-Warn "Defender rejected the exclusion (0x800106ba)"
                    Write-Note "This usually means a third-party AV is active."
                    $defenderOk = $false
                } else {
                    Write-Err "Failed to exclude ${p}: $msg"
                    $defenderOk = $false
                }
            }
        }

        try {
            Add-MpPreference -ExclusionProcess "php.exe" -ErrorAction Stop
            Write-Ok "Excluded process: php.exe"
        } catch {
            # Not fatal; path exclusion is usually sufficient.
            Write-Note "Process exclusion not applied (non-fatal)"
        }

        $Result.DefenderOk = $defenderOk
    }
}

# =====================================================================
#  Step 5 - Detect third-party antivirus
# =====================================================================

Write-Host ""
Write-Step "Step 5 - Detect installed antivirus products"

$avProducts = @()
try {
    $raw = Get-CimInstance -Namespace "root/SecurityCenter2" `
        -ClassName "AntiVirusProduct" -ErrorAction Stop
    foreach ($p in $raw) {
        if ($p.displayName) {
            $avProducts += $p.displayName
        }
    }
} catch {
    Write-Note "Could not query the Security Center WMI namespace."
    Write-Note "This is normal on Windows Server or in restricted environments."
}

$thirdParty = @()
foreach ($name in $avProducts) {
    if ($name -notmatch "Defender" -and $name -notmatch "Microsoft") {
        $thirdParty += $name
    }
}

$Result.ThirdPartyAv = $thirdParty

if ($avProducts.Count -eq 0) {
    Write-Note "No antivirus products reported."
} else {
    foreach ($name in $avProducts) {
        if ($name -match "Defender" -or $name -match "Microsoft") {
            Write-Ok "Windows Defender: $name"
        } else {
            Write-Warn "Third-party AV: $name"
        }
    }
}

if ($thirdParty.Count -gt 0) {
    Write-Host ""
    Write-Note "The following third-party antivirus products are active:"
    foreach ($name in $thirdParty) {
        Write-Note "    - $name"
    }
    Write-Note ""
    Write-Note "These products ignore Windows Defender exclusions. To"
    Write-Note "allow php.exe to run, add an exclusion in the product's"
    Write-Note "own settings using the instructions below."

    foreach ($name in $thirdParty) {
        Write-Host ""
        Write-Note "--- $name ---"
        if     ($name -match "Avast")          { Write-Note "  Menu > Settings > General > Exceptions > Add Exception" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "AVG")            { Write-Note "  Menu > Settings > General > Exceptions > Add Exception" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Norton|Symantec|NortonLifeLock") { Write-Note "  Settings > Antivirus > Scans and Risks > Exclusions/Low Risks" ; Write-Note "  > Items to Exclude from Scans > Add Folders" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "McAfee")         { Write-Note "  PC Security > Real-Time Scanning > Excluded Files > Add File" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Kaspersky")      { Write-Note "  Settings > Security > Threats and Exclusions > Manage exclusions" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Bitdefender")    { Write-Note "  Protection > Antivirus > Settings > Manage Exclusions" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Malwarebytes")   { Write-Note "  Settings > Allow List > Add" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "ESET")           { Write-Note "  Setup > Computer protection > Exclusions > Edit" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Sophos")         { Write-Note "  Global Settings > Allow list > Add" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        elseif ($name -match "Trend")          { Write-Note "  Settings > Exceptions > Add" ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
        else                                    { Write-Note "  Open the product's Exclusions or Allow List setting." ; Write-Note "  Add folder: $PhpDir" ; Write-Note "  Add folder: $PhpAppDir" }
    }
}

# =====================================================================
#  Step 6 - Test execution
# =====================================================================

Write-Host ""
Write-Step "Step 6 - Test php.exe execution"

if (-not (Test-Path $PhpExe)) {
    Write-Err "Cannot test; php.exe is missing."
} else {
    try {
        $output = & $PhpExe --version 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0) {
            $firstLine = ($output | Select-Object -First 1)
            Write-Ok "php.exe runs successfully"
            Write-Note "  $firstLine"
            $Result.BinaryRuns = $true
        } else {
            Write-Err "php.exe exited with code $exitCode"
            Write-Note ($output -join "`n    ")
            $Result.ErrorDetail = "exit code $exitCode"
        }
    } catch {
        $msg = $_.Exception.Message
        Write-Err "php.exe could not be launched"
        Write-Note $msg
        $Result.ErrorDetail = $msg
    }
}

# =====================================================================
#  Summary
# =====================================================================

Write-Host ""
Write-Head "================================================================="
Write-Head "  Summary"
Write-Head "================================================================="
Write-Host ""

if ($Result.BinaryRuns) {
    Write-Ok "PHP is fully operational."
    Write-Host ""
    Write-Note "Next steps:"
    Write-Note "  1. If the backend is running, stop it (Ctrl+C in its window)."
    Write-Note "  2. Start it again with:  start.bat"
    Write-Note "  3. Open in a browser:    http://127.0.0.1:8000/php/"
    Write-Host ""
} else {
    Write-Err "PHP could not be executed."
    Write-Host ""

    if (-not $Result.BinaryPresent) {
        Write-Note "The PHP binary is missing or corrupted."
        Write-Note "Re-extract it:"
        Write-Note "    rmdir /s /q runtime\php runtime\_downloads"
        Write-Note "    python bootstrap\setup.py --force --skip-python --skip-sqlite --skip-deps --skip-vendor"
    } elseif ($Result.ThirdPartyAv.Count -gt 0) {
        Write-Note "The likely cause is a third-party antivirus blocking"
        Write-Note "php.exe. Apply the exclusions listed in Step 5, then"
        Write-Note "rerun this script."
        Write-Host ""
        Write-Note "If you cannot find the antivirus exclusion setting,"
        Write-Note "temporarily disable the product's real-time protection,"
        Write-Note "test the /php/ page, and re-enable it afterward."
    } elseif (-not $IsAdmin) {
        Write-Note "Rerun this script from an Administrator PowerShell"
        Write-Note "to apply the ACL and Defender fixes."
    } else {
        Write-Note "No further automated fix is available. Two options:"
        Write-Host ""
        Write-Note "Option A: use a system-installed PHP. Set"
        Write-Note "    AGENT_PHP_BINARY=C:\\path\\to\\php.exe"
        Write-Note "in the .env file, then restart the backend."
        Write-Host ""
        Write-Note "Option B: disable the PHP notes app. Set"
        Write-Note "    AGENT_PHP_ENABLED=false"
        Write-Note "in the .env file. The rest of the app is unaffected."
    }

    if ($Result.ErrorDetail) {
        Write-Host ""
        Write-Note "Last error: $($Result.ErrorDetail)"
    }
    Write-Host ""
}

# =====================================================================
#  Exit code
# =====================================================================
# 0 if PHP works, 1 if it does not, 2 if the script could not determine
# the state (for example, missing elevation).

if ($Result.BinaryRuns) { exit 0 }
if (-not $Result.BinaryPresent) { exit 1 }
if (-not $IsAdmin) { exit 2 }
exit 1