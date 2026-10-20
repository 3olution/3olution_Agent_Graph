#!/usr/bin/env bash
#
# 3olution Agent Graph — download LiteGraph.js vendor files.
# https://3olution.com
#
# Thin wrapper around scripts/download_vendor.py. This script exists
# for users who prefer bash to invoking Python directly, or who need
# to vendor the LiteGraph files in an environment where only curl and
# tar are available.
#
# It downloads the two files directly via curl rather than shelling
# out to the Python downloader. That is deliberate: the whole point
# of this wrapper is to be usable without Python.
#
# Usage:
#   bash scripts/download_vendor.sh              # skip if present
#   bash scripts/download_vendor.sh --force      # force re-download
#
# Exit codes:
#   0   both files present
#   1   curl missing, download failed, or file too small
#
# For a cross-platform downloader with better validation and mirror
# fallback, use scripts/download_vendor.py instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$SCRIPT_DIR/../web/vendor"
FORCE=0

# ──────────────────────────────────────────────────────────────────────
#  Parse arguments
# ──────────────────────────────────────────────────────────────────────

for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --help|-h)
            cat <<EOF
Usage: bash scripts/download_vendor.sh [--force]

Options:
  --force, -f   Re-download even if files exist
  --help,  -h   Show this message
EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────
#  Verify curl is available
# ──────────────────────────────────────────────────────────────────────

if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is not installed." >&2
    echo "Install it with your package manager, or run:" >&2
    echo "    python scripts/download_vendor.py" >&2
    exit 1
fi

mkdir -p "$VENDOR_DIR"

echo "Vendor directory: $VENDOR_DIR"

# ──────────────────────────────────────────────────────────────────────
#  Mirror URLs
# ──────────────────────────────────────────────────────────────────────
#
# jsDelivr is preferred (fast, cached). raw.githubusercontent.com is
# the fallback.

JS_PRIMARY="https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/build/litegraph.min.js"
JS_FALLBACK="https://raw.githubusercontent.com/jagenjo/litegraph.js/master/build/litegraph.min.js"

CSS_PRIMARY="https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/css/litegraph.css"
CSS_FALLBACK="https://raw.githubusercontent.com/jagenjo/litegraph.js/master/css/litegraph.css"

# ──────────────────────────────────────────────────────────────────────
#  Download function with mirror fallback and size validation
# ──────────────────────────────────────────────────────────────────────

download_with_fallback() {
    local dest="$1"
    local primary="$2"
    local fallback="$3"
    local min_size="$4"

    # Skip if present, sufficiently large, and not forcing.
    if [ -f "$dest" ] && [ "$FORCE" -eq 0 ]; then
        local existing_size
        existing_size=$(wc -c < "$dest" | tr -d ' ')
        if [ "$existing_size" -ge "$min_size" ]; then
            echo "  ✓ $(basename "$dest") already present (${existing_size} bytes) — skipping"
            return 0
        fi
        echo "  ! $(basename "$dest") too small (${existing_size} bytes) — re-downloading"
    fi

    echo "  → Downloading $(basename "$dest")"

    if curl -fsSL --max-time 60 "$primary" -o "$dest" 2>/dev/null; then
        :
    elif curl -fsSL --max-time 60 "$fallback" -o "$dest" 2>/dev/null; then
        echo "     (from fallback mirror)"
    else
        echo "  ✗ both mirrors failed for $(basename "$dest")" >&2
        return 1
    fi

    local size
    size=$(wc -c < "$dest" | tr -d ' ')
    if [ "$size" -lt "$min_size" ]; then
        echo "  ✗ $(basename "$dest") is too small (${size} bytes < ${min_size})" >&2
        return 1
    fi

    echo "  ✓ $(basename "$dest") (${size} bytes)"
    return 0
}

# ──────────────────────────────────────────────────────────────────────
#  Run downloads
# ──────────────────────────────────────────────────────────────────────

download_with_fallback \
    "$VENDOR_DIR/litegraph.min.js" \
    "$JS_PRIMARY" \
    "$JS_FALLBACK" \
    50000

download_with_fallback \
    "$VENDOR_DIR/litegraph.css" \
    "$CSS_PRIMARY" \
    "$CSS_FALLBACK" \
    1000

echo ""
echo "Done. Files in $VENDOR_DIR:"
ls -lh "$VENDOR_DIR"