#!/usr/bin/env python3
# CUI // SP-CTI
"""STRATEGOS — Air-Gap Vendor Bundle Fetcher.

Downloads all required JS/CSS libraries for the STRATEGOS UI into
apps/strategos/static/js/ and apps/strategos/static/css/.

Run ONCE on a network-connected machine, then operate air-gapped.

Usage:
    python apps/strategos/static/fetch_vendor.py [--dry-run]
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

STATIC_ROOT = Path(__file__).parent
JS_DIR = STATIC_ROOT / "js"
CSS_DIR = STATIC_ROOT / "css"

# Format: (url, dest_path, sha256_prefix_8)
VENDOR_ASSETS = [
    # Leaflet 1.9.4 — interactive maps
    (
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.min.js",
        JS_DIR / "leaflet.min.js",
        "a1ad7b7c",
    ),
    (
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.min.css",
        CSS_DIR / "leaflet.min.css",
        None,
    ),
    # Protomaps-Leaflet (PMTiles) 4.0.0 — vector tile overlays
    (
        "https://unpkg.com/protomaps-leaflet@4.0.0/dist/protomaps-leaflet.min.js",
        JS_DIR / "pmtiles.js",
        None,
    ),
    # D3.js v7 — data-driven SVG visualizations
    (
        "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js",
        JS_DIR / "d3.min.js",
        None,
    ),
    # Plotly.js — interactive charts
    (
        "https://cdn.plot.ly/plotly-2.35.0.min.js",
        JS_DIR / "plotly.min.js",
        None,
    ),
    # Cytoscape.js — graph / network visualization
    (
        "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js",
        JS_DIR / "cytoscape.min.js",
        None,
    ),
    # Vis-Network — force-directed network graphs
    (
        "https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js",
        JS_DIR / "vis-network.min.js",
        None,
    ),
    # Vis-Timeline — temporal event timelines
    (
        "https://cdn.jsdelivr.net/npm/vis-timeline@7.7.3/standalone/umd/vis-timeline-graph2d.min.js",
        JS_DIR / "vis-timeline.min.js",
        None,
    ),
    (
        "https://cdn.jsdelivr.net/npm/vis-timeline@7.7.3/styles/vis-timeline-graph2d.min.css",
        CSS_DIR / "vis-timeline.min.css",
        None,
    ),
    # Chart.js 4 — canvas charts
    (
        "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js",
        JS_DIR / "chart.min.js",
        None,
    ),
]


def download(url: str, dest: Path, sha_prefix: str | None = None, dry_run: bool = False) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  SKIP  {dest.name} (already exists, {dest.stat().st_size:,} bytes)")
        return True
    if dry_run:
        print(f"  WOULD DOWNLOAD  {url} -> {dest.name}")
        return True
    print(f"  GET   {url} -> {dest.name} ...", end="", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        dest.write_bytes(data)
        if sha_prefix:
            actual = hashlib.sha256(data).hexdigest()[:8]
            if actual != sha_prefix:
                print(f" WARN: sha256 prefix {actual} != expected {sha_prefix}")
            else:
                print(f" OK ({len(data):,} bytes, sha256:{actual})")
        else:
            print(f" OK ({len(data):,} bytes)")
        return True
    except Exception as e:
        print(f" ERROR: {e}")
        return False


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[fetch_vendor] DRY RUN — no files will be downloaded")

    JS_DIR.mkdir(parents=True, exist_ok=True)
    CSS_DIR.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0
    for url, dest, sha in VENDOR_ASSETS:
        result = download(url, dest, sha, dry_run=dry_run)
        if result:
            ok += 1
        else:
            fail += 1

    print(f"\n[fetch_vendor] {ok} OK, {fail} failed")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
