# CUI // SP-CTI
"""OSINT Pre-Stager — internet-side collector that writes signals to data/osint_inbox/.

Run this script on an internet-connected machine **before** deploying to an
air-gapped enclave. It fetches OSINT signals from the configured RSS sources,
normalises them, and writes a timestamped JSON batch file to data/osint_inbox/.
The inbox directory can then be rsync'd / burnt to removable media and copied
into the enclave.

Once in the enclave the osint_harvester reflex reads from TIER_FILE_INBOX
and ingests the pre-staged signals automatically.

Usage:
    python tools/strategos/osint_prestage.py [--output-dir <path>] [--json]
    python tools/strategos/osint_prestage.py --dry-run  # print count, no write

Output format (each file in output-dir):
    osint_prestage_<UTC-timestamp>.json
    {"signals": [...], "count": N, "prestaged_at": "..."}

Each signal object is compatible with osint_harvester's _process_inbox_json:
    {"title": str, "body": str, "source": str, "date": str, "url": str, "geo_hint": null}
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import hashlib
import json
import logging
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_SIGNALS = 500
_USER_AGENT = "ICDEV-Strategos-Prestager/1.0"

BASE_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_INBOX = BASE_DIR / "data" / "osint_inbox"

logger = get_logger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ── utilities ──────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── config ─────────────────────────────────────────────────────────────────────

def _load_sources() -> List[Dict[str, Any]]:
    candidates = [
        BASE_DIR / "args" / "strategos_osint_sources.yaml",
        BASE_DIR / "args" / "strategos_config.yaml",
    ]
    raw_sources: List[Dict[str, Any]] = []
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not available — skipping %s", cfg_path.name)
            continue
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, list):
                raw_sources = data
            elif "sources" in data:
                raw_sources = data["sources"]
            elif "osint" in data and "sources" in data["osint"]:
                raw_sources = data["osint"]["sources"]
            else:
                continue
            if raw_sources:
                logger.info("Loaded %d sources from %s", len(raw_sources), cfg_path.name)
                break
        except Exception as exc:
            logger.warning("Could not load %s: %s", cfg_path.name, exc)

    def _include(src: Dict[str, Any]) -> bool:
        if src.get("enabled") is False:
            return False
        return src.get("internet_required", True) or src.get("tier", "internet") == "internet"

    return [s for s in raw_sources if _include(s)]


# ── HTTP / RSS ─────────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 30) -> Optional[bytes]:
    if not url.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.read()
    except Exception as exc:
        logger.debug("Fetch failed %s: %s", url, exc)
        return None


def _parse_rss(xml_bytes: bytes) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    try:
        content = xml_bytes.decode("utf-8", errors="replace")
        try:
            import defusedxml.ElementTree as _dxml  # type: ignore
            root = _dxml.fromstring(content)
        except ImportError:
            root = ET.fromstring(content)  # nosec B314

        # RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            entries.append({
                "title": title,
                "body": (item.findtext("description") or "")[:2000].strip(),
                "url": (item.findtext("link") or "").strip(),
                "date": (item.findtext("pubDate") or "").strip(),
            })

        # Atom fallback
        if not entries:
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//a:entry", ns):
                title = (entry.findtext("a:title", "", ns) or "").strip()
                if not title:
                    continue
                link_el = entry.find("a:link", ns)
                entries.append({
                    "title": title,
                    "body": (entry.findtext("a:summary", "", ns) or "")[:2000].strip(),
                    "url": link_el.get("href", "") if link_el is not None else "",
                    "date": (entry.findtext("a:updated", "", ns) or "").strip(),
                })
    except ET.ParseError:
        pass
    return entries


# ── collection ─────────────────────────────────────────────────────────────────

def _collect_from_source(
    src: Dict[str, Any],
    seen_hashes: set,
    run_at: str,
) -> List[Dict[str, Any]]:
    name = src.get("name", src.get("url", "unknown"))
    url = src.get("url", "")
    feed_type = src.get("type", "rss").lower()

    if not url or feed_type != "rss":
        return []

    raw = _fetch_url(url)
    if raw is None:
        logger.warning("Fetch failed: '%s' — continuing", name)
        return []

    try:
        entries = _parse_rss(raw)
    except Exception as exc:
        logger.warning("RSS parse error '%s': %s", name, exc)
        return []

    signals: List[Dict[str, Any]] = []
    for entry in entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        entry_url = (entry.get("url") or "").strip()
        date = (entry.get("date") or "").strip()
        body = (entry.get("body") or "")[:4000]

        url_hash = _sha256(entry_url) if entry_url else _sha256(title + date)
        if url_hash in seen_hashes:
            continue
        seen_hashes.add(url_hash)

        signals.append({
            "title": title[:1000],
            "body": body,
            "source": name,
            "date": date,
            "url": entry_url,
            "geo_hint": None,
        })

    logger.info("'%s': %d signals", name, len(signals))
    return signals


# ── output ─────────────────────────────────────────────────────────────────────

def prestage(output_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """Collect signals and write a prestage JSON file to output_dir."""
    sources = _load_sources()
    run_at = _utcnow_iso()

    if not sources:
        logger.warning("No internet-tier sources configured — nothing to pre-stage")
        return {"success": True, "signals": 0, "file": None, "run_at": run_at}

    seen_hashes: set = set()
    all_signals: List[Dict[str, Any]] = []

    for src in sources:
        if len(all_signals) >= MAX_SIGNALS:
            break
        collected = _collect_from_source(src, seen_hashes, run_at)
        remaining = MAX_SIGNALS - len(all_signals)
        all_signals.extend(collected[:remaining])

    logger.info("Pre-staged total: %d signal(s)", len(all_signals))

    if dry_run:
        return {"success": True, "signals": len(all_signals), "file": None, "run_at": run_at, "dry_run": True}

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = output_dir / f"osint_prestage_{ts}.json"

    payload = {"signals": all_signals, "count": len(all_signals), "prestaged_at": run_at}
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info("Written: %s (%d bytes)", out_file.name, out_file.stat().st_size)
    return {"success": True, "signals": len(all_signals), "file": str(out_file), "run_at": run_at}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ Strategos OSINT Pre-Stager")
    parser.add_argument(
        "--output-dir", default=str(_DEFAULT_INBOX),
        help="Directory to write prestage JSON file (default: data/osint_inbox/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count signals without writing")
    parser.add_argument("--json", action="store_true", help="Print result as JSON to stdout")
    cli = parser.parse_args()

    result = prestage(Path(cli.output_dir), dry_run=cli.dry_run)

    if cli.json:
        print(json.dumps(result))
    else:
        tag = " [DRY RUN]" if cli.dry_run else ""
        print(f"  Strategos Prestager: signals={result['signals']} file={result.get('file') or 'n/a'}{tag}")

    sys.exit(0)
