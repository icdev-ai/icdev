#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Daily Digest Generator — produces Markdown reports of Scout findings.

Generates a structured daily digest with findings from all three pillars,
LLM synthesis, recommended actions, and Genesis build results.

Usage:
    python tools/scout/digest.py --generate --date 2026-03-20 --json
    python tools/scout/digest.py --view --date 2026-03-20
    python tools/scout/digest.py --list --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_dir(config: dict) -> Path:
    rel = config.get("digest", {}).get("output_dir", "data/scout")
    d = BASE_DIR / rel
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate(
    date_str: str,
    findings: List[dict],
    synthesis: Optional[str] = None,
    genesis_result: Optional[dict] = None,
    stats: dict = None,
    config: dict = None,
) -> Path:
    """Generate a Markdown digest file. Returns the file path."""
    config = config or {}
    digest_cfg = config.get("digest", {})
    max_per_pillar = digest_cfg.get("max_findings_per_pillar", 20)

    out_dir = _digest_dir(config)
    out_path = out_dir / f"{date_str}.md"

    stats = stats or {}
    duration_ms = stats.get("duration_ms", 0)
    total = len(findings)

    # Group findings by pillar
    by_pillar = {}
    for f in findings:
        p = f.get("pillar", "unknown")
        by_pillar.setdefault(p, []).append(f)

    lines = []

    # Header
    lines.append(f"# Scout Daily Digest — {date_str}\n")
    lines.append(f"> Generated at {_now()} | Duration: {duration_ms / 1000:.0f}s | Findings: {total}\n")

    # Synthesis
    if synthesis:
        lines.append("\n## Summary\n")
        lines.append(f"{synthesis}\n")

    # Pillars
    pillar_order = ["introspect", "trending", "competitive"]
    pillar_labels = {
        "introspect": "Pillar 1: Self-Introspection",
        "trending": "Pillar 2: Trending Open Source",
        "competitive": "Pillar 3: Competitive Intel",
    }

    for pillar in pillar_order:
        items = by_pillar.get(pillar, [])
        if not items:
            continue
        label = pillar_labels.get(pillar, pillar)
        lines.append(f"\n## {label} ({len(items)} findings)\n")

        # Group by category within pillar
        by_cat = {}
        for item in items:
            cat = item.get("category", "other")
            by_cat.setdefault(cat, []).append(item)

        for cat, cat_items in by_cat.items():
            lines.append(f"\n### {cat.replace('_', ' ').title()} ({len(cat_items)})\n")
            for item in sorted(cat_items, key=lambda x: x.get("relevance_score", 0), reverse=True)[:max_per_pillar]:
                sev = item.get("severity", "?")
                score = item.get("relevance_score", 0)
                title = item.get("title", "")
                desc = item.get("description", "")[:150]
                url = item.get("url", "")
                url_part = f" — [{url}]({url})" if url else ""
                lines.append(f"- **[{sev.upper()}]** ({score:.2f}) {title}{url_part}\n")
                if desc:
                    lines.append(f"  {desc}\n")

    # Recommended Actions
    actionable = [f for f in findings if f.get("actionable")]
    if actionable:
        lines.append("\n## Recommended Actions\n")
        actionable.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        for item in actionable[:15]:
            sev = item.get("severity", "medium").upper()
            action = item.get("suggested_action", "")
            title = item.get("title", "")
            lines.append(f"- [ ] **{sev}**: {action} — *{title}*\n")

    # Genesis Build Results
    if genesis_result:
        lines.append("\n## Genesis Build Results\n")
        status = genesis_result.get("status", "unknown")
        lines.append(f"- **Status**: {status}\n")
        if genesis_result.get("branch"):
            lines.append(f"- **Branch**: `{genesis_result['branch']}`\n")
        if genesis_result.get("finding_title"):
            lines.append(f"- **Triggered by**: {genesis_result['finding_title']}\n")
        if genesis_result.get("validation"):
            val = genesis_result["validation"]
            lines.append(
                f"- **Validation**: py_compile={val.get('py_compile', '?')}, "
                f"ruff={val.get('ruff', '?')}, pytest={val.get('pytest', '?')}, "
                f"bandit={val.get('bandit', '?')}\n"
            )
        if genesis_result.get("error"):
            lines.append(f"- **Error**: {genesis_result['error']}\n")
        if genesis_result.get("attempts"):
            lines.append(f"- **Attempts**: {len(genesis_result['attempts'])}\n")
            for att in genesis_result["attempts"]:
                lines.append(f"  - {att.get('finding_title', '?')}: {att.get('status', '?')}\n")

    # Footer
    lines.append("\n---\n")
    lines.append(
        f"*Scan duration: {duration_ms:,}ms | "
        f"Signals fed: {stats.get('signals_fed', 0)} | "
        f"Repos added: {stats.get('repos_added', 0)}*\n"
    )

    content = "\n".join(lines)
    out_path.write_text(content, encoding="utf-8", newline="")
    return out_path


def get_digest(date_str: str, config: dict = None) -> Optional[str]:
    """Read a past digest by date string (YYYY-MM-DD)."""
    config = config or {}
    out_dir = _digest_dir(config)
    path = out_dir / f"{date_str}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def list_digests(config: dict = None) -> List[dict]:
    """List all available digests with dates and sizes."""
    config = config or {}
    out_dir = _digest_dir(config)
    digests = []
    for f in sorted(out_dir.glob("*.md"), reverse=True):
        digests.append(
            {
                "date": f.stem,
                "path": str(f),
                "size_bytes": f.stat().st_size,
            }
        )
    return digests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout digest generator")
    parser.add_argument("--view", action="store_true", help="View a digest")
    parser.add_argument("--list", action="store_true", dest="list_digests", help="List digests")
    parser.add_argument("--date", help="Date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    if args.list_digests:
        result = list_digests(config)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            for d in result:
                print(f"{d['date']}  ({d['size_bytes']} bytes)")
    elif args.view:
        if not args.date:
            parser.error("--view requires --date")
        content = get_digest(args.date, config)
        if content:
            print(content)
        else:
            print(f"No digest found for {args.date}")
    else:
        parser.error("Specify --view or --list")


if __name__ == "__main__":
    main()
