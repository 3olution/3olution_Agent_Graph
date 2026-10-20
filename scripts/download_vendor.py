#!/usr/bin/env python3
"""
3olution Agent Graph — cross-platform LiteGraph.js vendor downloader.
https://3olution.com

Downloads the two LiteGraph.js files the graph editor needs into
`web/vendor/`. Uses only the Python standard library, so it runs on
any Python 3.8+ installation without extra dependencies.

================================================================
  WHAT IT DOWNLOADS
================================================================

  Two files from the jagenjo/litegraph.js repository:

      build/litegraph.min.js    → web/vendor/litegraph.min.js
      css/litegraph.css         → web/vendor/litegraph.css

  These files are NOT committed to git (see .gitignore). They are
  fetched on demand by:

      - scripts/serve.py          (on startup, if missing)
      - backend/main.py           (in lifespan, if missing)
      - run.py setup              (developer workflow)
      - run.py vendor             (force refresh)
      - start.sh / start.bat      (portable workflow)

================================================================
  MIRRORS
================================================================

  Two mirrors are tried for each file, in order:

      1. jsDelivr (fast global CDN, generous cache)
      2. raw.githubusercontent.com (GitHub direct)

  The first mirror usually succeeds. The second exists for
  environments where jsDelivr is blocked or slow.

================================================================
  VALIDATION
================================================================

  Each downloaded file is validated before being written to disk:

    - Minimum size: a 404 HTML page is only a few KB, so a size
      floor rejects error pages. The JS minimum is 50 KB; the CSS
      minimum is 1 KB.
    - Required substring: the JS file must contain "LiteGraph"; the
      CSS file must contain "litegraph". This catches cases where
      a proxy returns a generic 200 page instead of the real file.

  Only after both checks pass is the file moved into place. A
  partial or corrupted download never overwrites a working file.

  The write itself is atomic: data goes to `<name>.part` first, and
  is renamed into place with `Path.replace()` only after the write
  completes. A crash mid-write leaves the previous file untouched.

================================================================
  USAGE
================================================================

    python scripts/download_vendor.py
        Download both files into web/vendor/.

    python scripts/download_vendor.py --force
        Re-download even if the files already exist.

    python scripts/download_vendor.py --quiet
        Suppress per-file progress output.

  The exit code is 0 if both files are present after the run, and
  1 if any file could not be downloaded.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
#  Paths and constants
# ══════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "web" / "vendor"

# Each entry: filename -> list of mirror URLs (tried in order).
FILES: dict[str, list[str]] = {
    "litegraph.min.js": [
        "https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/build/litegraph.min.js",
        "https://raw.githubusercontent.com/jagenjo/litegraph.js/master/build/litegraph.min.js",
    ],
    "litegraph.css": [
        "https://cdn.jsdelivr.net/gh/jagenjo/litegraph.js@master/css/litegraph.css",
        "https://raw.githubusercontent.com/jagenjo/litegraph.js/master/css/litegraph.css",
    ],
}

# Minimum plausible size in bytes; anything smaller is a failed download.
MIN_SIZE: dict[str, int] = {
    "litegraph.min.js": 50_000,   # ~85 KB in practice
    "litegraph.css": 1_000,       # ~4 KB in practice
}

# A sanity-check substring that must appear in the file's content.
REQUIRED_SUBSTRING: dict[str, str] = {
    "litegraph.min.js": "LiteGraph",
    "litegraph.css": "litegraph",
}

# Network timeout for individual requests.
TIMEOUT_SECONDS = 30.0


# ══════════════════════════════════════════════════════════════════════
#  Quiet helper
# ══════════════════════════════════════════════════════════════════════

class _Logger:
    """Print messages unless --quiet is set."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def __call__(self, msg: str = "") -> None:
        if not self.quiet:
            print(msg)


# ══════════════════════════════════════════════════════════════════════
#  Atomic write helper
# ══════════════════════════════════════════════════════════════════════

def _atomic_write(dest: Path, data: bytes) -> None:
    """
    Write `data` to `dest` atomically.

    Writes to `<dest>.part` first, then renames into place. A crash
    mid-write leaves the previous file untouched — the caller never
    sees a half-written file where a valid one used to be.

    Raises OSError on failure. Cleans up the partial file.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    finally:
        # If replace() succeeded, tmp no longer exists; the unlink
        # is a no-op. If write_bytes() failed midway, this removes
        # the partial file so the next run does not see it.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════
#  Downloader
# ══════════════════════════════════════════════════════════════════════

def _download_one(
    name: str,
    urls: list[str],
    force: bool,
    log: _Logger,
) -> bool:
    """
    Download a single file, trying each mirror in order.

    Returns True on success, False on failure. Never raises.
    """
    dest = VENDOR / name

    # Skip if the file already looks good.
    if dest.exists() and not force:
        size = dest.stat().st_size
        if size >= MIN_SIZE[name]:
            log(f"  ✓ {name} already present ({size:,} bytes) — skipping")
            return True
        log(
            f"  ! {name} present but suspiciously small "
            f"({size} bytes) — re-downloading"
        )

    last_error: Exception | None = None

    for url in urls:
        try:
            log(f"  → Downloading {name}")
            log(f"      from {url}")

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "3olution-setup/1.0 (+https://3olution.com)"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                data = response.read()

            # Size check.
            if len(data) < MIN_SIZE[name]:
                raise ValueError(
                    f"downloaded file is too small "
                    f"({len(data)} bytes < {MIN_SIZE[name]} bytes)"
                )

            # Content check.
            text = data.decode("utf-8", errors="replace")
            if REQUIRED_SUBSTRING[name] not in text:
                raise ValueError(
                    f"downloaded file does not contain "
                    f"'{REQUIRED_SUBSTRING[name]}' — wrong URL?"
                )

            # Write atomically, only after both gates pass.
            _atomic_write(dest, data)
            log(f"  ✓ {name} ({len(data):,} bytes)")
            return True

        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            ValueError,
            TimeoutError,
            OSError,
        ) as exc:
            last_error = exc
            log(f"      ✗ failed: {exc}")
            continue

    log(f"  ✗ {name}: all mirrors failed ({last_error})")
    return False


def download_all(force: bool = False, quiet: bool = False) -> bool:
    """
    Download every vendor file.

    Returns True if all files were downloaded successfully, False
    otherwise.
    """
    log = _Logger(quiet)
    VENDOR.mkdir(parents=True, exist_ok=True)
    log(f"Vendor directory: {VENDOR}")

    ok = True
    for name, urls in FILES.items():
        if not _download_one(name, urls, force, log):
            ok = False
    return ok


# ══════════════════════════════════════════════════════════════════════
#  Manual instructions
# ══════════════════════════════════════════════════════════════════════

def _print_manual_instructions() -> None:
    """Print fallback instructions for a failed download."""
    print()
    print("Manual fallback: download these two files into web/vendor/:")
    for name, urls in FILES.items():
        print(f"  {name}:")
        for u in urls:
            print(f"    {u}")
    print()
    print("Place both files at:")
    print(f"  {VENDOR / 'litegraph.min.js'}")
    print(f"  {VENDOR / 'litegraph.css'}")


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Download LiteGraph.js vendor files for the 3olution Agent Graph."
        ),
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-download even if the files already exist.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-file progress output.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    ok = download_all(force=args.force, quiet=args.quiet)

    if not args.quiet:
        print()
        if ok:
            print("All vendor files present.")
        else:
            print("Some downloads failed.")
            _print_manual_instructions()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())