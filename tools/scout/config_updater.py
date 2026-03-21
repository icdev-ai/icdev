#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Config Updater — auto-add trending repos to innovation/creative configs.

When Scout discovers high-value trending repos, this module adds them to:
- args/innovation_config.yaml (competitive_intel.competitors + github.targets.issues.repos)
- args/creative_config.yaml (github_issues.repos)

All changes are idempotent — repos already tracked are skipped.

Usage:
    python tools/scout/config_updater.py --update --repos "org/repo1,org/repo2" --json
    python tools/scout/config_updater.py --status --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

INNOVATION_CONFIG = BASE_DIR / "args" / "innovation_config.yaml"
CREATIVE_CONFIG = BASE_DIR / "args" / "creative_config.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_yaml(path: Path, data: dict) -> None:
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True,
                       sort_keys=False, width=120)


def _get_tracked_repos(config: dict) -> set:
    """Extract all currently tracked repo names from innovation config."""
    repos = set()
    for target in config.get("sources", {}).get("github", {}).get("targets", []):
        if isinstance(target, dict) and target.get("type") == "issues":
            for r in target.get("repos", []):
                repos.add(r.lower())
    return repos


def update_innovation_config(repo_names: List[str]) -> Dict:
    """Add repos to innovation_config.yaml GitHub issues tracking list."""
    if not INNOVATION_CONFIG.exists():
        return {"error": "innovation_config.yaml not found", "added": []}

    config = _load_yaml(INNOVATION_CONFIG)
    tracked = _get_tracked_repos(config)
    added = []

    for target in config.get("sources", {}).get("github", {}).get("targets", []):
        if isinstance(target, dict) and target.get("type") == "issues":
            repos_list = target.get("repos", [])
            for repo in repo_names:
                if repo.lower() not in tracked:
                    repos_list.append(repo)
                    added.append(repo)
                    tracked.add(repo.lower())
            target["repos"] = repos_list
            break

    if added:
        _save_yaml(INNOVATION_CONFIG, config)

    return {"config": "innovation_config.yaml", "added": added, "already_tracked": len(repo_names) - len(added)}


def update_creative_config(repo_names: List[str]) -> Dict:
    """Add repos to creative_config.yaml GitHub issues tracking list."""
    if not CREATIVE_CONFIG.exists():
        return {"error": "creative_config.yaml not found", "added": []}

    config = _load_yaml(CREATIVE_CONFIG)
    existing = set()
    repos_list = config.get("sources", {}).get("github_issues", {}).get("repos", [])
    existing = {r.lower() for r in repos_list}

    added = []
    for repo in repo_names:
        if repo.lower() not in existing:
            repos_list.append(repo)
            added.append(repo)
            existing.add(repo.lower())

    if added:
        config.setdefault("sources", {}).setdefault("github_issues", {})["repos"] = repos_list
        _save_yaml(CREATIVE_CONFIG, config)

    return {"config": "creative_config.yaml", "added": added, "already_tracked": len(repo_names) - len(added)}


def update_from_findings(findings: List[dict], config: dict = None) -> Dict:
    """Extract auto-add candidate repos from competitive findings and update configs."""
    config = config or {}
    update_innovation = config.get("integration", {}).get("update_innovation_config", True)
    update_creative = config.get("integration", {}).get("update_creative_config", True)
    max_add = config.get("competitive", {}).get("max_auto_add_per_day", 5)

    # Extract repo names from new_competitor findings
    repos_to_add = []
    for f in findings:
        if f.get("category") != "new_competitor":
            continue
        repo_name = f.get("metadata", {}).get("repo_name", "")
        if repo_name and f.get("metadata", {}).get("auto_add_candidate"):
            repos_to_add.append(repo_name)

    repos_to_add = repos_to_add[:max_add]
    result = {"repos_evaluated": len(repos_to_add), "updated_at": _now()}

    if repos_to_add and update_innovation:
        result["innovation"] = update_innovation_config(repos_to_add)
    if repos_to_add and update_creative:
        result["creative"] = update_creative_config(repos_to_add)

    total_added = 0
    if "innovation" in result:
        total_added += len(result["innovation"].get("added", []))
    if "creative" in result:
        total_added += len(result["creative"].get("added", []))
    result["total_repos_added"] = total_added

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout config auto-updater")
    parser.add_argument("--update", action="store_true", help="Add repos to configs")
    parser.add_argument("--repos", help="Comma-separated repo names (org/repo)")
    parser.add_argument("--status", action="store_true", help="Show tracked repos")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if args.update:
        if not args.repos:
            parser.error("--update requires --repos")
        repos = [r.strip() for r in args.repos.split(",")]
        result = {
            "innovation": update_innovation_config(repos),
            "creative": update_creative_config(repos),
        }
    elif args.status:
        inn_config = _load_yaml(INNOVATION_CONFIG)
        result = {"tracked_repos": sorted(_get_tracked_repos(inn_config))}
    else:
        parser.error("Specify --update or --status")
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
