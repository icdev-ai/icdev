#!/usr/bin/env python3

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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.security.injection_scanner import scan_text  # noqa: E402

logger = get_logger(__name__)

_REFLEX_DEFAULTS: Dict[str, Any] = {
    "api_timeout_seconds": 15,
    "description_max_chars": 300,
    "release_body_max_chars": 500,
    "notes_preview_max_chars": 200,
    "top_repos_n": 5,
    "anomaly_detection": {
        "enabled": True,
        "z_score_threshold": 2.0,
        "min_samples": 3,
        "metrics": ["stars", "forks", "open_issues"],
    },
    # Turn benchmark findings into suggested kanban cards after each scout
    # pass. Off by default: the brief is the reflex's contract, and writing
    # to the board is an escalation an operator opts into. Rate limits and
    # the gap-verdict gate live in args/innovation_promoter.yaml.
    "promotion": {
        "enabled": False,
        "dry_run": False,
    },
}


def _load_reflex_config() -> Dict[str, Any]:
    """Load genesis_reflex section from args/scout_config.yaml, merging with defaults."""
    try:
        import yaml

        path = BASE_DIR / "args" / "scout_config.yaml"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            file_cfg = data.get("genesis_reflex", {})
            merged = {**_REFLEX_DEFAULTS, **file_cfg}
            ad_file = file_cfg.get("anomaly_detection", {})
            merged["anomaly_detection"] = {**_REFLEX_DEFAULTS["anomaly_detection"], **ad_file}
            promo_file = file_cfg.get("promotion", {})
            merged["promotion"] = {**_REFLEX_DEFAULTS["promotion"], **promo_file}
            return merged
    except (ImportError, Exception):
        pass
    return dict(_REFLEX_DEFAULTS)


def _detect_anomalies(targets_data: List[Dict], reflex_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """Flag repos whose metrics are statistical outliers (z-score method).

    Returns a mapping of repo name -> list of anomaly descriptions.
    Only runs when ≥ min_samples valid repos are present.
    """
    ad_cfg = reflex_cfg.get("anomaly_detection", {})
    if not ad_cfg.get("enabled", True):
        return {}

    z_threshold = float(ad_cfg.get("z_score_threshold", 2.0))
    min_samples = int(ad_cfg.get("min_samples", 3))
    metrics: List[str] = ad_cfg.get("metrics", ["stars", "forks", "open_issues"])

    valid = [t for t in targets_data if t.get("info") and not t.get("error")]
    if len(valid) < min_samples:
        return {}

    anomalies: Dict[str, List[str]] = {}
    metric_labels = {"stars": "star count", "forks": "fork count", "open_issues": "open issue count"}

    for metric in metrics:
        values = [float(t["info"].get(metric, 0)) for t in valid]
        n = len(values)
        if n < min_samples:
            continue
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = variance ** 0.5
        if std == 0:
            continue
        for t, val in zip(valid, values):
            z = (val - mean) / std
            if abs(z) >= z_threshold:
                direction = "unusually high" if z > 0 else "unusually low"
                label = metric_labels.get(metric, metric)
                anomalies.setdefault(t["repo"], []).append(
                    f"{direction} {label}: {int(val):,} (z={z:.1f})"
                )

    return anomalies


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


def _github_api(
    endpoint: str,
    timeout: int = _REFLEX_DEFAULTS["api_timeout_seconds"],
    error_sink: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Call GitHub public API.  Returns None on failure.

    Failures append a short machine-readable reason to *error_sink* so the caller
    can report WHY the scan came up empty (xbm-wake-01). Previously every failure
    mode collapsed to a bare ``None`` plus a ``print()`` that no log captured, so a
    total outage was indistinguishable from a healthy scan that found nothing new —
    and the daemon then filed it as a metric-threshold miss.

    The distinction this draws is load-bearing, because the two live failure modes
    need opposite responses: 401 means the configured GITHUB_TOKEN is stale and must
    be replaced, while 403/429 means we are running ANONYMOUSLY and have exhausted
    the 60-request/hour per-IP budget — which a token would fix and a retry would
    not. Scouting the full watchlist costs 16-32 calls, so anonymous runs exhaust
    that budget routinely and the reflex fails for a reason no operator could see.
    """
    url = f"https://api.github.com{endpoint}"
    headers = {
        "User-Agent": "ICDEV-Genesis/2.0",
        "Accept": "application/vnd.github.v3+json",
    }
    # Use token if available (higher rate limit)
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"

    def _record(reason: str, detail: str) -> None:
        logger.warning("GitHub API failed for %s: %s (%s)", endpoint, reason, detail)
        if error_sink is not None:
            error_sink.append(reason)

    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        # 401 → the configured GITHUB_TOKEN is stale/revoked (an ANONYMOUS call
        # would have succeeded). 403/429 → rate limited. 404 → repo gone/renamed.
        if e.code == 401:
            reason = "http_401_bad_github_token"
        elif e.code in (403, 429):
            reason = f"http_{e.code}_rate_limited"
        else:
            reason = f"http_{e.code}"
        _record(reason, str(e))
        return None
    except (URLError, OSError, json.JSONDecodeError) as e:
        _record(f"network_{type(e).__name__}", str(e))
        return None


def _get_repo_info(
    owner_repo: str,
    description_max_chars: int = _REFLEX_DEFAULTS["description_max_chars"],
    timeout: int = _REFLEX_DEFAULTS["api_timeout_seconds"],
    error_sink: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Fetch repo metadata: stars, description, language, updated_at."""
    data = _github_api(f"/repos/{owner_repo}", timeout=timeout, error_sink=error_sink)
    if not data:
        return None
    return {
        "full_name": data.get("full_name", owner_repo),
        "description": (data.get("description") or "")[:description_max_chars],
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "language": data.get("language", ""),
        "updated_at": data.get("updated_at", ""),
        "open_issues": data.get("open_issues_count", 0),
        "topics": data.get("topics", []),
        "archived": data.get("archived", False),
    }


def _get_latest_release(
    owner_repo: str,
    body_max_chars: int = _REFLEX_DEFAULTS["release_body_max_chars"],
    timeout: int = _REFLEX_DEFAULTS["api_timeout_seconds"],
    error_sink: Optional[List[str]] = None,
) -> Optional[Dict]:
    """Fetch latest release info."""
    data = _github_api(f"/repos/{owner_repo}/releases/latest", timeout=timeout, error_sink=error_sink)
    if not data:
        return None
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "published_at": data.get("published_at", ""),
        "body": (data.get("body") or "")[:body_max_chars],
    }


def _generate_brief(
    targets_data: List[Dict],
    anomalies: Optional[Dict[str, List[str]]] = None,
    notes_preview_max_chars: int = _REFLEX_DEFAULTS["notes_preview_max_chars"],
    manual_targets: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Generate a markdown intel brief from collected data."""
    now = _utcnow()
    anomalies = anomalies or {}
    manual_targets = manual_targets or []
    anomaly_count = sum(len(v) for v in anomalies.values())
    lines = [
        "# Genesis Scout Brief",
        "",
        f"**Date:** {now.strftime('%Y-%m-%d')}",
        f"**Repos Monitored:** {len(targets_data)}",
        f"**Anomalies Detected:** {anomaly_count}",
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
            repo_anomalies = anomalies.get(repo, [])

            lines.append(f"### {repo}")
            if item.get("subsystem"):
                lines.append(f"- **Subsystem:** {item['subsystem']}")
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
                    lines.append(f"- **Notes:** {release['body'][:notes_preview_max_chars]}...")
            if repo_anomalies:
                lines.append(f"- **Anomalies:** {'; '.join(repo_anomalies)}")
            if info.get("archived"):
                lines.append("- **STATUS: ARCHIVED**")
            lines.append("")

    # Targets with no public repo. The scout cannot poll a marketing site, so
    # these are listed for a human rather than dropped — an entry nobody ever
    # sees is indistinguishable from one that was never added.
    if manual_targets:
        lines.append("## Manual Review — No Public Repo")
        lines.append("")
        for item in manual_targets:
            lines.append(f"### {item.get('name', 'unknown')}")
            if item.get("subsystem"):
                lines.append(f"- **Subsystem:** {item['subsystem']}")
            if item.get("url"):
                lines.append(f"- **Docs:** {item['url']}")
            if item.get("notes"):
                lines.append(f"- **Notes:** {item['notes'][:notes_preview_max_chars]}")
            lines.append("")

    lines.extend(["---", "", "*Generated by Genesis Scout Reflex*"])
    return "\n".join(lines)


def _promote_findings(reflex_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Turn benchmark findings with a gap verdict into suggested kanban cards.

    This is the step that makes the scout more than a report generator: a
    finding on a subsystem the benchmark map judged deficient becomes a card
    an operator can confirm. Everything that bounds it — the gap-verdict
    gate, ``max_per_run``, ``max_per_subsystem`` — lives in the promoter and
    its config, not here.

    Never raises: a promotion failure must not fail the scout pass or wedge
    the reflex loop behind it.
    """
    promo_cfg = reflex_cfg.get("promotion") or {}
    if not promo_cfg.get("enabled", False):
        return {"enabled": False}

    try:
        from tools.innovation.kanban_promoter import run_promotion

        result = run_promotion(dry_run=bool(promo_cfg.get("dry_run", False)))
        return {
            "enabled": True,
            "dry_run": result.get("dry_run"),
            "candidates": result.get("candidates", 0),
            "gap_verdict_eligible": result.get("gap_verdict_eligible", 0),
            "created": result.get("created", 0),
            "would_create": result.get("would_create", 0),
            "truncated": result.get("truncated", False),
        }
    except Exception as exc:  # noqa: BLE001 - promotion is additive to the brief
        logger.warning("scout: promotion step failed (non-blocking): %s", exc)
        return {"enabled": True, "error": str(exc)[:200]}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Scout Reflex."""
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped"},
        }

    reflex_cfg = _load_reflex_config()
    api_timeout = int(reflex_cfg["api_timeout_seconds"])
    desc_max = int(reflex_cfg["description_max_chars"])
    body_max = int(reflex_cfg["release_body_max_chars"])
    notes_max = int(reflex_cfg["notes_preview_max_chars"])
    top_n = int(reflex_cfg["top_repos_n"])

    targets = _load_targets()
    if not targets:
        return {
            "success": False,
            "metric_value": 0,
            "details": {"error": "No targets in context/genesis/competitors.yaml"},
        }

    targets_data = []
    manual_targets = []
    errors = 0
    api_errors: List[str] = []

    for target in targets:
        repo = target.get("repo", "")
        if not repo:
            # No repo => nothing to poll. Commercial products with no public
            # source (Cortex.io, Port, OpsLevel) are carried as manual-review
            # entries and surfaced in the brief instead of being skipped.
            manual_targets.append(target)
            continue

        print(f"  Scouting: {repo}")
        info = _get_repo_info(
            repo, description_max_chars=desc_max, timeout=api_timeout, error_sink=api_errors
        )
        if not info:
            errors += 1
            targets_data.append(
                {
                    "repo": repo,
                    "category": target.get("category", ""),
                    "subsystem": target.get("subsystem", ""),
                    "info": {},
                    "error": True,
                }
            )
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
                targets_data.append(
                {
                    "repo": repo,
                    "category": target.get("category", ""),
                    "subsystem": target.get("subsystem", ""),
                    "info": {},
                    "error": True,
                }
            )
                continue

        release = None
        watch = target.get("watch", [])
        if "releases" in watch:
            release = _get_latest_release(
                repo, body_max_chars=body_max, timeout=api_timeout, error_sink=api_errors
            )
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
                "subsystem": target.get("subsystem", ""),
                "info": info,
                "release": release,
            }
        )

    valid_targets = [t for t in targets_data if not t.get("error")]

    # Anomaly detection — flag repos with statistically unusual metrics
    anomalies = _detect_anomalies(targets_data, reflex_cfg)

    # Generate brief
    brief_md = _generate_brief(
        valid_targets,
        anomalies=anomalies,
        notes_preview_max_chars=notes_max,
        manual_targets=manual_targets,
    )

    # Write brief
    reports_dir = BASE_DIR / "data" / "genesis" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = _utcnow().strftime("%Y-%m-%d")
    brief_file = reports_dir / f"scout-{date_str}.md"
    brief_file.write_text(brief_md, encoding="utf-8", newline="")

    successful = len(valid_targets)
    anomaly_repos = list(anomalies.keys())

    promotion = _promote_findings(reflex_cfg)

    # xbm-wake-01: when NOTHING was scouted the reflex is reporting failure, so it
    # must say why. Without this the daemon fell back to a generic
    # "metric_threshold_not_met" — the exact string every silently-dead reflex on
    # this platform was carrying. The dominant reason is enough to route the fix
    # (bad token vs rate limit vs network) without dumping 16 near-identical
    # strings into last_error.
    details: Dict[str, Any] = {
        "repos_scouted": successful,
        "repos_failed": errors,
        "manual_review_entries": len(manual_targets),
        "brief_file": str(brief_file),
    }
    if successful == 0 and errors > 0:
        top_reason = max(set(api_errors), key=api_errors.count) if api_errors else "unknown"
        details["error"] = (
            f"github_api_unavailable: {top_reason} ({errors}/{len(targets_data)} repos failed)"
        )
        details["api_error_reasons"] = sorted(set(api_errors))

    return {
        "success": successful > 0,
        "metric_value": float(successful),
        "details": {
            **details,
            "anomalies_detected": len(anomaly_repos),
            "anomaly_repos": anomaly_repos,
            "promotion": promotion,
            "top_by_stars": sorted(
                [{"repo": t["repo"], "stars": t["info"].get("stars", 0)} for t in targets_data if t.get("info")],
                key=lambda x: x["stars"],
                reverse=True,
            )[:top_n],
        },
    }
