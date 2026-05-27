
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Tech Radar source scanner (D352 pattern).

SOURCE_SCANNERS maps source name → scan function.  Each function fetches
technology signals from external sources and returns dicts with fields:
    {name, source, ecosystem_maturity_signal, description}

Air-gap safe: every network call is wrapped; failures return [] with a
warning log.  No external deps beyond stdlib — PyYAML is optional (needed
only for cncf_landscape).

CLI:
    python source_scanner.py --scan --all --json
    python source_scanner.py --scan --source thoughtworks_radar --json
    python source_scanner.py --scan --source cncf_landscape --json
    python source_scanner.py --scan --source github_trending --json
"""

import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10  # seconds; kept short for air-gap safety


# ── helpers ───────────────────────────────────────────────────────────────────

def _http_get(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[bytes]:
    """Best-effort HTTP GET → raw bytes.  Returns None on any failure."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # nosec B310
            return resp.read()
    except urllib.error.URLError as exc:
        logger.warning("source_scanner: network error fetching %s: %s", url, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_scanner: failed to process %s: %s", url, exc)
    return None


def _signal(
    *,
    name: str,
    source: str,
    ecosystem_maturity_signal: str,
    description: str,
) -> dict:
    return {
        "name": name,
        "source": source,
        "ecosystem_maturity_signal": ecosystem_maturity_signal,
        "description": description,
    }


# ── Thoughtworks Technology Radar ─────────────────────────────────────────────

# Public JSON published per radar volume; no auth required.
_TW_RADAR_URL = (
    "https://www.thoughtworks.com/content/dam/thoughtworks/documents/radar/"
    "2024/09/tr_technology_radar_vol_31_en.json"
)


def scan_thoughtworks_radar() -> List[dict]:
    """Fetch Thoughtworks Tech Radar JSON and parse blips.

    Returns one signal per blip; ring (ADOPT/TRIAL/ASSESS/HOLD) is used as
    the ecosystem_maturity_signal.
    """
    raw = _http_get(_TW_RADAR_URL, headers={"Accept": "application/json"})
    if not raw:
        return []

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("source_scanner: thoughtworks_radar JSON parse error: %s", exc)
        return []

    # Radar JSON shape varies by volume; handle common structures.
    if isinstance(data, list):
        blips = data
    elif isinstance(data, dict):
        blips = data.get("blips", data.get("items", data.get("entries", [])))
    else:
        return []

    signals = []
    for blip in blips:
        if not isinstance(blip, dict):
            continue
        name = (blip.get("name") or blip.get("title") or "").strip()
        ring = (blip.get("ring") or blip.get("ringName") or "").strip().upper()
        desc = (
            blip.get("description")
            or blip.get("blurb")
            or blip.get("body")
            or ""
        ).strip()
        if not name:
            continue
        signals.append(
            _signal(
                name=name,
                source="thoughtworks_radar",
                ecosystem_maturity_signal=ring or "UNKNOWN",
                description=desc[:500],
            )
        )

    return signals


# ── CNCF Landscape ────────────────────────────────────────────────────────────

_CNCF_LANDSCAPE_URL = (
    "https://raw.githubusercontent.com/cncf/landscape/master/landscape.yml"
)


def scan_cncf_landscape() -> List[dict]:
    """Fetch CNCF landscape.yml and extract CNCF project maturity signals.

    Requires PyYAML.  Returns [] without warning if not installed so that
    air-gap environments without PyYAML degrade silently.

    Emits only items that carry an explicit 'project' maturity label
    (graduated / incubating / sandbox), skipping non-CNCF entries.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "source_scanner: PyYAML not installed — skipping cncf_landscape"
        )
        return []

    raw = _http_get(_CNCF_LANDSCAPE_URL)
    if not raw:
        return []

    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_scanner: cncf_landscape YAML parse error: %s", exc)
        return []

    if not isinstance(data, dict):
        return []

    signals = []
    for category in data.get("landscape", []):
        if not isinstance(category, dict):
            continue
        for subcategory in category.get("subcategories", []):
            if not isinstance(subcategory, dict):
                continue
            for item in subcategory.get("items", []):
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip()
                maturity = (item.get("project") or "").strip()
                if not name or not maturity:
                    continue

                desc_raw = item.get("description") or item.get("homepage_url") or ""
                description = desc_raw.strip() if isinstance(desc_raw, str) else ""

                signals.append(
                    _signal(
                        name=name,
                        source="cncf_landscape",
                        ecosystem_maturity_signal=maturity.upper(),
                        description=description[:500],
                    )
                )

    return signals


# ── GitHub Trending ───────────────────────────────────────────────────────────

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

_VELOCITY_THRESHOLDS = (
    (100.0, "VIRAL"),
    (20.0, "HIGH_ADOPTION"),
    (5.0, "GROWING"),
    (0.0, "EMERGING"),
)


def _star_velocity_signal(stars: int, created_at: str, now: datetime) -> str:
    """Classify adoption signal from star velocity (stars / age_days)."""
    velocity = 0.0
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_days = max((now - created).days, 1)
            velocity = stars / age_days
        except (ValueError, TypeError):
            pass
    for threshold, label in _VELOCITY_THRESHOLDS:
        if velocity >= threshold:
            return label
    return "EMERGING"


def scan_github_trending() -> List[dict]:
    """Fetch recently starred GitHub repos and compute adoption proxy from star velocity.

    Uses the GitHub search API (no auth required; 60 req/hr unauthenticated).
    Set GITHUB_TOKEN or GH_TOKEN in the environment for 5 000 req/hr.

    Searches repos created in the last 30 days with ≥100 stars, sorted by
    stars descending.  Star velocity (stars / age_days) determines the
    ecosystem_maturity_signal bucket.
    """
    import os

    headers: Dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    params = urllib.parse.urlencode(
        {
            "q": f"created:>{since} stars:>=100",
            "sort": "stars",
            "order": "desc",
            "per_page": "50",
        }
    )

    raw = _http_get(f"{_GITHUB_SEARCH_URL}?{params}", headers=headers)
    if not raw:
        return []

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("source_scanner: github_trending JSON parse error: %s", exc)
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    now = datetime.now(timezone.utc)
    signals = []

    for repo in items:
        if not isinstance(repo, dict):
            continue
        name = (repo.get("full_name") or "").strip()
        if not name:
            continue
        stars = repo.get("stargazers_count", 0)
        created_at = repo.get("created_at", "")
        desc = (repo.get("description") or "").strip()
        velocity_label = _star_velocity_signal(stars, created_at, now)

        velocity_display = ""
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_days = max((now - created).days, 1)
                vel = round(stars / age_days, 1)
                velocity_display = f"stars/day: {vel} | "
            except (ValueError, TypeError):
                pass

        signals.append(
            _signal(
                name=name,
                source="github_trending",
                ecosystem_maturity_signal=velocity_label,
                description=f"Stars: {stars} | {velocity_display}{desc[:400]}".strip(" |"),
            )
        )

    return signals


# ── Registry (D352 pattern) ───────────────────────────────────────────────────

SOURCE_SCANNERS: Dict[str, Callable[[], List[dict]]] = {
    "thoughtworks_radar": scan_thoughtworks_radar,
    "cncf_landscape": scan_cncf_landscape,
    "github_trending": scan_github_trending,
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Tech Radar source scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python source_scanner.py --scan --all --json\n"
            "  python source_scanner.py --scan --source thoughtworks_radar --json\n"
            "  python source_scanner.py --scan --source cncf_landscape --json\n"
            "  python source_scanner.py --scan --source github_trending --json\n"
        ),
    )
    parser.add_argument("--scan", action="store_true", help="Run scanner(s)")
    parser.add_argument("--all", action="store_true", help="Scan all sources")
    parser.add_argument("--source", metavar="NAME", help="Single source to scan")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if not args.scan:
        parser.print_help()
        sys.exit(0)

    if args.all:
        sources = list(SOURCE_SCANNERS)
    elif args.source:
        if args.source not in SOURCE_SCANNERS:
            out = {
                "error": f"Unknown source: {args.source}",
                "available": list(SOURCE_SCANNERS),
            }
            print(json.dumps(out))
            sys.exit(1)
        sources = [args.source]
    else:
        print(json.dumps({"error": "Specify --all or --source <name>"}))
        sys.exit(1)

    signals: List[dict] = []
    for src in sources:
        signals.extend(SOURCE_SCANNERS[src]())

    if args.as_json:
        print(json.dumps({"signals": signals, "count": len(signals)}, indent=2))
    else:
        for sig in signals:
            label = sig["ecosystem_maturity_signal"]
            print(f"[{sig['source']}] [{label:14s}] {sig['name'][:60]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _cli()
