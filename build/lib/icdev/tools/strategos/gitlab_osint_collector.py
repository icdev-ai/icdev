# CUI // SP-CTI
"""GitLab OSINT Collector — internet-facing GitLab CI runner script.

Reads internet-tier sources from args/strategos_config.yaml
(or args/strategos_osint_sources.yaml if present). Collects RSS feeds,
normalizes signals, extracts entity mentions, and writes two JSON artifacts:

  osint_signals.json — {"sg_raw_signals": [...], "count": N, "run_at": "..."}
  kg_delta.json      — {"entities": [...], "run_at": "..."}

Any per-source failure is logged as a warning; collection continues.
Always exits 0 — empty output is a valid artifact.
Max 500 signals per run.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import logging
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_SIGNALS = 500
_USER_AGENT = "ICDEV-Strategos-GitLab-Collector/1.0"

BASE_DIR = Path(__file__).resolve().parents[2]

logger = get_logger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ── utilities ─────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── config loading ─────────────────────────────────────────────────────────────

def _load_sources() -> List[Dict[str, Any]]:
    """Load OSINT sources from YAML config.

    Tries strategos_osint_sources.yaml first; falls back to strategos_config.yaml.
    Filters to internet-tier sources (internet_required=True or tier='internet')
    that are not explicitly disabled (enabled != False).
    """
    candidates = [
        BASE_DIR / "args" / "strategos_osint_sources.yaml",
        BASE_DIR / "args" / "strategos_config.yaml",
    ]

    raw_sources: List[Dict[str, Any]] = []

    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            try:
                import yaml
            except ImportError:
                logger.warning("PyYAML not available — cannot load %s", cfg_path.name)
                continue

            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Two schema layouts:
            # 1. strategos_osint_sources.yaml: top-level list OR {"sources": [...]}
            # 2. strategos_config.yaml:        {"osint": {"sources": [...]}}
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

    def _is_internet(src: Dict[str, Any]) -> bool:
        if src.get("enabled") is False:
            return False
        return src.get("internet_required", True) or src.get("tier", "internet") == "internet"

    return [s for s in raw_sources if _is_internet(s)]


# ── HTTP / RSS ─────────────────────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 30) -> Optional[bytes]:
    if not url.startswith(("http://", "https://")):
        logger.debug("Rejected non-http URL: %s", url[:100])
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
            root = ET.fromstring(content)  # nosec B314 — content from admin-configured feeds

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


# ── entity extraction ──────────────────────────────────────────────────────────

_UNIT_RE = re.compile(
    r"\b(\d{1,3}(?:st|nd|rd|th)?\s+"
    r"(?:Battalion|Brigade|Division|Corps|Regiment|Army|Company|Fleet|Wing|Squadron|"
    r"Group|Task\s+Force|Airborne|Infantry|Armored|Armoured|Marine|Naval|Air\s+Force|"
    r"Mechanized|Mechanised|Guards|Separate))\b",
    re.IGNORECASE,
)

_EQUIPMENT_TERMS = [
    "Javelin", "HIMARS", "M1 Abrams", "Abrams", "T-72", "T-80", "T-90", "T-14 Armata",
    "Su-27", "Su-35", "Su-25", "Su-34", "MiG-29", "F-16", "F-35", "F-15",
    "Patriot", "S-300", "S-400", "ATACMS", "Storm Shadow", "SCALP",
    "Bayraktar", "TB2", "Shahed", "Lancet", "Orlan",
    "Challenger 2", "Leopard 2", "Leopard", "Bradley", "BMP-2", "BMP-3",
    "BTR-80", "BTR-82", "Starlink", "MANPADS", "Stinger",
    "MLRS", "howitzer", "CAESAR", "PzH 2000", "Excalibur",
    "Switchblade", "UAV", "FPV drone",
]
_EQUIPMENT_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _EQUIPMENT_TERMS) + r")\b",
    re.IGNORECASE,
)

_KNOWN_LOCATIONS = [
    "Kyiv", "Kiev", "Kharkiv", "Kherson", "Mariupol", "Zaporizhzhia", "Zaporizhia",
    "Odessa", "Odesa", "Lviv", "Dnipro", "Donetsk", "Luhansk", "Crimea",
    "Bakhmut", "Avdiivka",
    "Moscow", "St. Petersburg", "Belgorod", "Kursk", "Rostov",
    "Taiwan", "Taipei", "Beijing", "Shanghai", "South China Sea",
    "Gaza", "Rafah", "West Bank", "Jerusalem", "Tel Aviv",
    "Syria", "Aleppo", "Damascus", "Idlib",
    "Yemen", "Sanaa",
    "NATO", "Brussels", "Warsaw", "Vilnius", "Tallinn", "Riga",
    "Washington", "Pentagon", "Kremlin",
    "Red Sea", "Black Sea", "Mediterranean", "Baltic Sea",
    "Taiwan Strait",
]
_LOCATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(loc) for loc in _KNOWN_LOCATIONS) + r")\b",
    re.IGNORECASE,
)


def _extract_entities(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract unit names, locations, and equipment mentions from signal text."""
    counts: Dict[str, Dict[str, Any]] = {}

    def _record(etype: str, label: str, source: str) -> None:
        key = f"{etype}:{label.lower()}"
        if key not in counts:
            counts[key] = {"type": etype, "label": label, "mentions": 0, "signal_count": 0, "sources": []}
        counts[key]["mentions"] += 1
        if source and source not in counts[key]["sources"]:
            counts[key]["sources"].append(source)

    for sig in signals:
        text = f"{sig.get('title', '')} {sig.get('body', '')}"
        source = sig.get("source", "")
        seen_this_signal: set = set()

        for m in _UNIT_RE.finditer(text):
            label = " ".join(m.group(0).split())
            _record("unit", label, source)
            seen_this_signal.add(f"unit:{label.lower()}")

        for m in _EQUIPMENT_RE.finditer(text):
            label = m.group(1)
            _record("equipment", label, source)
            seen_this_signal.add(f"equipment:{label.lower()}")

        for m in _LOCATION_RE.finditer(text):
            label = m.group(1)
            _record("location", label, source)
            seen_this_signal.add(f"location:{label.lower()}")

        for key in seen_this_signal:
            counts[key]["signal_count"] += 1

    return sorted(counts.values(), key=lambda e: e["mentions"], reverse=True)


# ── collection ─────────────────────────────────────────────────────────────────

def _collect_from_source(
    src: Dict[str, Any],
    seen_hashes: set,
    run_at: str,
) -> List[Dict[str, Any]]:
    name = src.get("name", src.get("url", "unknown"))
    url = src.get("url", "")
    feed_type = src.get("type", "rss").lower()

    if not url:
        logger.warning("Source '%s' has no url — skipping", name)
        return []

    if feed_type != "rss":
        logger.debug("Source '%s' type '%s' not supported — skipping", name, feed_type)
        return []

    raw = _fetch_url(url)
    if raw is None:
        logger.warning("Failed to fetch '%s' (%s) — continuing", name, url)
        return []

    try:
        entries = _parse_rss(raw)
    except Exception as exc:
        logger.warning("RSS parse error for '%s': %s — continuing", name, exc)
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
            "url_hash": url_hash,
            "title": title[:1000],
            "body": body,
            "source": name,
            "signal_date": date,
            "geo_hint": None,
            "tier_used": "INTERNET",
            "created_at": run_at,
            "url": entry_url,
        })

    logger.info("Source '%s': %d signals", name, len(signals))
    return signals


# ── output ─────────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Wrote %s (%d bytes)", path.name, path.stat().st_size)


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ Strategos GitLab OSINT Collector")
    parser.add_argument(
        "--output-dir", default=".",
        help="Directory to write osint_signals.json and kg_delta.json (default: cwd)",
    )
    parser.add_argument("--json", action="store_true", help="Print run summary as JSON to stdout")
    cli = parser.parse_args()

    output_dir = Path(cli.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_at = _utcnow_iso()
    sources = _load_sources()

    if not sources:
        logger.warning("No internet-tier sources found — writing empty artifacts")
        _write_json(output_dir / "osint_signals.json", {"sg_raw_signals": [], "count": 0, "run_at": run_at})
        _write_json(output_dir / "kg_delta.json", {"entities": [], "run_at": run_at})
        if cli.json:
            print(json.dumps({"success": True, "signals": 0, "entities": 0, "run_at": run_at}))
        sys.exit(0)

    logger.info("Collecting from %d internet-tier source(s)", len(sources))

    seen_hashes: set = set()
    all_signals: List[Dict[str, Any]] = []

    for src in sources:
        if len(all_signals) >= MAX_SIGNALS:
            break
        collected = _collect_from_source(src, seen_hashes, run_at)
        remaining = MAX_SIGNALS - len(all_signals)
        all_signals.extend(collected[:remaining])

    logger.info("Total collected: %d signal(s)", len(all_signals))

    _write_json(
        output_dir / "osint_signals.json",
        {"sg_raw_signals": all_signals, "count": len(all_signals), "run_at": run_at},
    )

    entities = _extract_entities(all_signals)
    _write_json(
        output_dir / "kg_delta.json",
        {"entities": entities, "run_at": run_at},
    )

    summary = {"success": True, "signals": len(all_signals), "entities": len(entities), "run_at": run_at}
    if cli.json:
        print(json.dumps(summary))
    else:
        print(f"  Strategos GitLab Collector: signals={len(all_signals)} entities={len(entities)} run_at={run_at}")

    sys.exit(0)


if __name__ == "__main__":
    main()
