# 3olution Agent Graph — download LiteGraph.js vendor files (Windows).
# https://3olution.com
#
# Thin wrapper around scripts/download_vendor.py. This script exists
# for users who prefer PowerShell to invoking Python directly.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\download_vendor.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\download_vendor.ps1 -Force
#
# Exit codes:
#   0   both files present
#   1   download failed or file too small
#
# For a cross-platform downloader with better validation and mirror
# fallback, use scripts\download_vendor.py instead.

#Requires -Version 5.0

param(
    [switch]$Force = $false
)

$ErrorActionPreference = "Stop"

# ──────────────────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────────────────

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$VendorDir  = Join-Path $ScriptDir "..\web\vendor"
$VendorDir  = [System.IO.Path]::GetFullPath($VendorDir)

New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null

Write-Host "Vendor directory: $VendorDir"

# ──────────────────────────────────────────────────────────────────────
#  URLs
# ──────────────────────────────────────────────────────────────────────
#
# jsDelivr is preferred. raw.githubusercontent.com is the fallback.

$jsPrimary  = "https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/build/litegraph.min.js"
$jsFallback = "https://raw.githubusercontent.com/jagenjo/litegraph.js/master/build/litegraph.min.js"

$cssPrimary  = "https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/css/litegraph.css"
$cssFallback = "https://raw.githubusercontent.com/jagenjo/litegraph.js/master/css/litegraph.css"

# ──────────────────────────────────────────────────────────────────────
#  Helper: download with fallback and validation
# ──────────────────────────────────────────────────────────────────────

function Download-WithFallback {
    param(
        [string]$Dest,
        [string]$Primary,
        [string]$Fallback,
        [int]$MinSize
    )

    $name = Split-Path -Leaf $Dest

    # Skip if present and sufficiently large.
    if ((Test-Path $Dest) -and (-not $Force)) {
        $existingSize = (Get-Item $Dest).Length
        if ($existingSize -ge $MinSize) {
            Write-Host "  ✓ $name already present ($existingSize bytes) — skipping"
            return
        }
        Write-Host "  ! $name too small ($existingSize bytes) — re-downloading"
    }

    Write-Host "  → Downloading $name"

    $downloaded = $false

    try {
        Invoke-WebRequest -Uri $Primary -OutFile $Dest -UseBasicParsing
        $downloaded = $true
    } catch {
        Write-Host "     primary mirror failed: $_"
    }

    if (-not $downloaded) {
        try {
            Invoke-WebRequest -Uri $Fallback -OutFile $Dest -UseBasicParsing
            Write-Host "     (from fallback mirror)"
            $downloaded = $true
        } catch {
            Write-Host "  ✗ both mirrors failed for $name"
            throw
        }
    }

    $size = (Get-Item $Dest).Length
    if ($size -lt $MinSize) {
        throw "$name is too small ($size bytes < $MinSize)"
    }

    Write-Host "  ✓ $name ($size bytes)"
}

# ──────────────────────────────────────────────────────────────────────
#  Download both files
# ──────────────────────────────────────────────────────────────────────

Download-WithFallback `
    -Dest (Join-Path $VendorDir "litegraph.min.js") `
    -Primary $jsPrimary `
    -Fallback $jsFallback `
    -MinSize 50000

Download-WithFallback `
    -Dest (Join-Path $VendorDir "litegraph.css") `
    -Primary $cssPrimary `
    -Fallback $cssFallback `
    -MinSize 1000

# ──────────────────────────────────────────────────────────────────────
#  Summary
# ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "Done. Files in ${VendorDir}:"
Get-ChildItem $VendorDir | Format-Table Name, Length -AutoSize