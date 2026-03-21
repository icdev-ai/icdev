#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Pillar 3: Competitive Intel — monitor competitors and identify new ones.

Delegates to existing competitive_intel scanner and cross-references
trending findings to identify potential new competitors.

Usage:
    python tools/scout/pillars/competitive.py --scan --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finding(category: str, title: str, description: str,
             url: str = "", severity: str = "medium",
             action: str = "", score: float = 0.5, meta: dict = None) -> dict:
    return {
        "id": f"scout-comp-{uuid.uuid4().hex[:12]}",
        "pillar": "competitive",
        "category": category,
        "title": title,
        "description": description,
        "url": url,
        "severity": severity,
        "actionable": bool(action),
        "suggested_action": action,
        "source": "competitive_intel",
        "relevance_score": score,
        "metadata": meta or {},
        "discovered_at": _now(),
    }


def _scan_existing_competitors() -> List[dict]:
    """Delegate to existing competitive intel scanner."""
    findings = []
    try:
        from tools.innovation.competitive_intel import scan_all_competitors
        results = scan_all_competitors()
        if isinstance(results, dict):
            for comp_name, comp_data in results.items():
                if comp_name in ("scan_time", "totals", "check_time", "bodies_checked"):
                    continue
                if not isinstance(comp_data, dict):
                    continue

                features = comp_data.get("features", [])
                releases = comp_data.get("releases_found", 0)
                issues = comp_data.get("issues_found", 0)

                for feat in features[:5]:
                    feat_str = str(feat)
                    findings.append(_finding(
                        category="competitor_update",
                        title=f"[{comp_name}] {feat_str[:100]}",
                        description=feat_str[:300],
                        severity="medium",
                        score=0.6,
                        action=f"Evaluate if ICDEV should match this {comp_name} feature",
                        meta={
                            "competitor": comp_name,
                            "releases": releases,
                            "issues": issues,
                            "source_type": "competitive",
                        },
                    ))

                if releases > 0 and not features:
                    findings.append(_finding(
                        category="competitor_release",
                        title=f"[{comp_name}] {releases} new release(s)",
                        description=f"Competitor '{comp_name}' has {releases} new releases",
                        severity="low",
                        score=0.4,
                        meta={"competitor": comp_name, "releases": releases},
                    ))
    except Exception as exc:
        findings.append(_finding(
            category="competitor_error",
            title="Competitive intel scan failed",
            description=str(exc),
            severity="low",
            score=0.0,
        ))
    return findings


def identify_new_competitors(trending_findings: List[dict], config: dict) -> List[dict]:
    """From trending findings, identify repos that could be competitors.

    A trending repo is a potential competitor if it:
    - Has >= min_stars (default 500)
    - Has relevance_score >= threshold (default 0.7)
    - Is a GitHub repo (has github in source or url)
    """
    findings = []
    comp_cfg = config.get("competitive", {})
    threshold = comp_cfg.get("auto_add_threshold", {})
    min_stars = threshold.get("min_stars", 500)
    min_score = threshold.get("relevance_score", 0.7)
    max_add = comp_cfg.get("max_auto_add_per_day", 5)

    candidates = []
    for tf in trending_findings:
        if tf.get("relevance_score", 0) < min_score:
            continue
        stars = tf.get("metadata", {}).get("stars", 0)
        if stars < min_stars:
            continue
        source = tf.get("metadata", {}).get("source_type", "")
        if source != "github":
            continue
        candidates.append(tf)

    # Sort by stars * relevance
    candidates.sort(
        key=lambda c: c.get("metadata", {}).get("stars", 0) * c.get("relevance_score", 0),
        reverse=True,
    )

    for cand in candidates[:max_add]:
        url = cand.get("url", "")
        # Extract org/repo from GitHub URL
        repo_name = ""
        if "github.com/" in url:
            parts = url.split("github.com/")[1].split("/")
            if len(parts) >= 2:
                repo_name = f"{parts[0]}/{parts[1]}"

        findings.append(_finding(
            category="new_competitor",
            title=f"New competitor candidate: {cand['title']}",
            description=(
                f"Trending repo with {cand.get('metadata', {}).get('stars', 0)} stars "
                f"and {cand.get('relevance_score', 0):.2f} relevance score. "
                f"{cand.get('description', '')}"
            ),
            url=url,
            score=cand.get("relevance_score", 0.7),
            severity="medium",
            action=f"Auto-add '{repo_name}' to competitive tracking" if repo_name else "",
            meta={
                "repo_name": repo_name,
                "stars": cand.get("metadata", {}).get("stars", 0),
                "trending_id": cand.get("id", ""),
                "auto_add_candidate": True,
            },
        ))

    return findings


def scan(config: dict = None, trending_findings: List[dict] = None) -> dict:
    """Run competitive intel scan. Returns structured results."""
    config = config or {}
    trending_findings = trending_findings or []
    result = {"pillar": "competitive", "started_at": _now(), "findings": []}

    # Scan existing competitors
    result["findings"].extend(_scan_existing_competitors())

    # Identify new competitors from trending data
    if trending_findings:
        new_comps = identify_new_competitors(trending_findings, config)
        result["findings"].extend(new_comps)
        result["new_competitors_identified"] = len(new_comps)

    result["finding_count"] = len(result["findings"])
    result["completed_at"] = _now()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Pillar 3: Competitive Intel")
    parser.add_argument("--scan", action="store_true", help="Run competitive scan")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if not args.scan:
        parser.error("Specify --scan")

    try:
        import yaml
        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    result = scan(config)
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Competitive: {result['finding_count']} findings")
        for f in result["findings"][:10]:
            print(f"  [{f['severity']}] {f['title']}")


if __name__ == "__main__":
    main()
