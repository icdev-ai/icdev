#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Genesis Scout Reflex — monitor competitor/adjacent GitHub repos.

Tracks stars, latest releases, and README changes for repos listed in
context/genesis/competitors.yaml.  Generates intel briefs as markdown.

Uses only GitHub's public API (no auth token required for basic info).
Scanner-tier only (zero Claude tokens).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.security.injection_scanner import scan_text  # noqa: E402

logger = get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


def _load_targets() -> List[Dict[str, Any]]:
    """Load scout targets from context/genesis/competitors.yaml."""
    try:
        import yaml

        path = BASE_DIR / "context" / "genesis" / "competitors.yaml"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("targets", [])
    except ImportError:
        pass
    return []


def _github_api(endpoint: str, timeout: int = 15) -> Optional[Dict]:
    """Call GitHub public API.  Returns None on failure."""
    url = f"https://api.github.com{endpoint}"
    headers = {
        "User-Agent": "ICDEV-Genesis/2.0",
        "Accept": "application/vnd.github.v3+json",
    }
    # Use token if available (higher rate limit)
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError) as e:
        print(f"  WARN: GitHub API failed for {endpoint}: {e}")
        return None


def _get_repo_info(owner_repo: str) -> Optional[Dict]:
    """Fetch repo metadata: stars, description, language, updated_at."""
    data = _github_api(f"/repos/{owner_repo}")
    if not data:
        return None
    return {
        "full_name": data.get("full_name", owner_repo),
        "description": (data.get("description") or "")[:300],
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "language": data.get("language", ""),
        "updated_at": data.get("updated_at", ""),
        "open_issues": data.get("open_issues_count", 0),
        "topics": data.get("topics", []),
        "archived": data.get("archived", False),
    }


def _get_latest_release(owner_repo: str) -> Optional[Dict]:
    """Fetch latest release info."""
    data = _github_api(f"/repos/{owner_repo}/releases/latest")
    if not data:
        return None
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "published_at": data.get("published_at", ""),
        "body": (data.get("body") or "")[:500],
    }


def _generate_brief(targets_data: List[Dict]) -> str:
    """Generate a markdown intel brief from collected data."""
    now = _utcnow()
    lines = [
        "# Genesis Scout Brief",
        "",
        f"**Date:** {now.strftime('%Y-%m-%d')}",
        f"**Repos Monitored:** {len(targets_data)}",
        "**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
    ]

    # Group by category
    by_category: Dict[str, List] = {}
    for t in targets_data:
        cat = t.get("category", "uncategorized")
        by_category.setdefault(cat, []).append(t)

    for category, items in sorted(by_category.items()):
        lines.append(f"## {category.replace('_', ' ').title()}")
        lines.append("")

        for item in items:
            repo = item.get("repo", "unknown")
            info = item.get("info", {})
            release = item.get("release")

            stars = info.get("stars", "?")
            desc = info.get("description", "")
            lang = info.get("language", "")

            lines.append(f"### {repo}")
            lines.append(f"- **Stars:** {stars:,}" if isinstance(stars, int) else f"- **Stars:** {stars}")
            if lang:
                lines.append(f"- **Language:** {lang}")
            if desc:
                lines.append(f"- **Description:** {desc}")
            if release:
                lines.append(
                    f"- **Latest Release:** {release.get('tag', '?')} ({release.get('published_at', '?')[:10]})"
                )
                if release.get("body"):
                    # First 200 chars of release notes
                    lines.append(f"- **Notes:** {release['body'][:200]}...")
            if info.get("archived"):
                lines.append("- **STATUS: ARCHIVED**")
            lines.append("")

    lines.extend(["---", "", "*Generated by Genesis Scout Reflex*"])
    return "\n".join(lines)


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Scout Reflex."""
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped"},
        }

    targets = _load_targets()
    if not targets:
        return {
            "success": False,
            "metric_value": 0,
            "details": {"error": "No targets in context/genesis/competitors.yaml"},
        }

    targets_data = []
    errors = 0

    for target in targets:
        repo = target.get("repo", "")
        if not repo:
            continue

        print(f"  Scouting: {repo}")
        info = _get_repo_info(repo)
        if not info:
            errors += 1
            targets_data.append({"repo": repo, "category": target.get("category", ""), "info": {}, "error": True})
            continue

        # Injection scan on external text fields (description, topics)
        scannable = " ".join(
            filter(
                None,
                [
                    info.get("description", ""),
                    " ".join(info.get("topics", [])),
                ],
            )
        )
        if scannable:
            findings = scan_text(scannable, source=f"github:{repo}")
            critical = [f for f in findings if f["severity"] == "critical"]
            if critical:
                logger.warning(
                    "Injection attempt blocked from github:%s: %s",
                    repo,
                    [f["category"] for f in critical],
                )
                errors += 1
                targets_data.append({"repo": repo, "category": target.get("category", ""), "info": {}, "error": True})
                continue

        release = None
        watch = target.get("watch", [])
        if "releases" in watch:
            release = _get_latest_release(repo)
            # Scan release notes for injection
            if release and release.get("body"):
                rel_findings = scan_text(release["body"], source=f"github:{repo}/releases")
                rel_critical = [f for f in rel_findings if f["severity"] == "critical"]
                if rel_critical:
                    logger.warning(
                        "Injection in release notes blocked from github:%s: %s",
                        repo,
                        [f["category"] for f in rel_critical],
                    )
                    release["body"] = "[REDACTED — injection detected]"

        targets_data.append(
            {
                "repo": repo,
                "category": target.get("category", ""),
                "info": info,
                "release": release,
            }
        )

    # Generate brief
    brief_md = _generate_brief([t for t in targets_data if not t.get("error")])

    # Write brief
    reports_dir = BASE_DIR / "data" / "genesis" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = _utcnow().strftime("%Y-%m-%d")
    brief_file = reports_dir / f"scout-{date_str}.md"
    brief_file.write_text(brief_md, encoding="utf-8")

    successful = len([t for t in targets_data if not t.get("error")])

    return {
        "success": successful > 0,
        "metric_value": float(successful),
        "details": {
            "repos_scouted": successful,
            "repos_failed": errors,
            "brief_file": str(brief_file),
            "top_by_stars": sorted(
                [{"repo": t["repo"], "stars": t["info"].get("stars", 0)} for t in targets_data if t.get("info")],
                key=lambda x: x["stars"],
                reverse=True,
            )[:5],
        },
    }