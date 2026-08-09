# CUI // SP-CTI
"""ICDEV™ Pulse Capability Scanner — loads capability YAML files and matches
capabilities to article topics via deterministic keyword scoring.

Design decisions:
    D-PULSE-CAP-1: Capability YAML files in context/capabilities/ (FORGE context layer)
    D-PULSE-CAP-4: Deterministic keyword matching for capability lookup (zero LLM)

Usage:
    python tools/pulse/engine/capability_scanner.py --list --json
    python tools/pulse/engine/capability_scanner.py --match "zero trust compliance" --json
    python tools/pulse/engine/capability_scanner.py --refresh --json
    python tools/pulse/engine/capability_scanner.py --domains --json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import yaml

# ── Project imports ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("pulse.capability_scanner")

CAPABILITIES_DIR = PROJECT_ROOT / "context" / "capabilities"


# ── Core functions ────────────────────────────────────────────────────


def load_all_capabilities() -> list[dict]:
    """Read all context/capabilities/*.yaml and return a flat list of capabilities.

    Each capability dict includes its parent domain info.

    Returns:
        List of capability dicts with keys: slug, name, description,
        cli_examples, related_controls, article_hooks, frameworks,
        keywords, source_catalog_id, domain, domain_description.
    """
    capabilities: list[dict] = []

    if not CAPABILITIES_DIR.exists():
        logger.warning("Capabilities directory not found: %s", CAPABILITIES_DIR)
        return capabilities

    for yaml_file in sorted(CAPABILITIES_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            domain = data.get("domain", yaml_file.stem)
            domain_desc = data.get("description", "")

            for cap in data.get("capabilities", []):
                cap["domain"] = domain
                cap["domain_description"] = domain_desc
                cap["_source_file"] = yaml_file.name
                capabilities.append(cap)

        except Exception as e:
            logger.warning("Failed to load %s: %s", yaml_file.name, e)

    logger.info(
        "Loaded %d capabilities from %d domain files", len(capabilities), len(list(CAPABILITIES_DIR.glob("*.yaml")))
    )
    return capabilities


def load_domains(include_capabilities: bool = False) -> list[dict]:
    """Return domain-level summaries, optionally with individual capabilities.

    Args:
        include_capabilities: If True, include capability details per domain.

    Returns:
        List of dicts with: domain, description, capability_count, file,
        and optionally capabilities[].
    """
    domains: list[dict] = []

    if not CAPABILITIES_DIR.exists():
        return domains

    for yaml_file in sorted(CAPABILITIES_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            entry = {
                "domain": data.get("domain", yaml_file.stem),
                "description": data.get("description", ""),
                "capability_count": len(data.get("capabilities", [])),
                "file": yaml_file.name,
            }
            if include_capabilities:
                entry["capabilities"] = [
                    {
                        "slug": c.get("slug", ""),
                        "name": c.get("name", ""),
                        "description": c.get("description", ""),
                        "keywords": c.get("keywords", []),
                        "frameworks": c.get("frameworks", []),
                    }
                    for c in data.get("capabilities", [])
                ]
            domains.append(entry)
        except Exception as e:
            logger.warning("Failed to load %s: %s", yaml_file.name, e)

    return domains


def match_capabilities(
    keywords: list[str],
    top_n: int = 5,
    config: dict | None = None,
) -> list[dict]:
    """Score capabilities against keywords and return top matches.

    Scoring (D-PULSE-CAP-4 — deterministic, zero LLM):
      - Exact keyword match in capability keywords: +3
      - Partial keyword match (substring): +1
      - Framework name match: +2
      - Domain name match: +1

    Args:
        keywords: List of search terms (from article topic, pain points).
        top_n: Maximum capabilities to return.
        config: Optional config dict with scoring weights.

    Returns:
        List of capability dicts sorted by score (highest first),
        with added 'match_score' and 'matched_keywords' fields.
    """
    config = config or {}
    scoring = config.get("scoring", {})
    exact_weight = scoring.get("exact_keyword", 3)
    partial_weight = scoring.get("partial_keyword", 1)
    framework_weight = scoring.get("framework_match", 2)

    capabilities = load_all_capabilities()
    if not capabilities or not keywords:
        return []

    # Normalize search keywords
    search_terms = [kw.lower().strip() for kw in keywords if kw.strip()]

    scored: list[tuple[float, list[str], dict]] = []

    for cap in capabilities:
        score = 0.0
        matched: list[str] = []
        cap_keywords = [k.lower() for k in cap.get("keywords", [])]
        cap_frameworks = [f.lower() for f in cap.get("frameworks", [])]
        cap_domain = cap.get("domain", "").lower()
        cap_name = cap.get("name", "").lower()
        cap_desc = cap.get("description", "").lower()

        for term in search_terms:
            # Exact keyword match
            if term in cap_keywords:
                score += exact_weight
                matched.append(term)
            # Framework match
            elif any(term in fw for fw in cap_frameworks):
                score += framework_weight
                matched.append(term)
            # Domain name match
            elif term in cap_domain or term == cap_domain:
                score += 1
                matched.append(term)
            # Partial match in keywords, name, or description
            elif any(term in kw for kw in cap_keywords):
                score += partial_weight
                matched.append(term)
            elif re.search(r"\b" + re.escape(term) + r"\b", cap_name):
                score += partial_weight
                matched.append(term)
            elif re.search(r"\b" + re.escape(term) + r"\b", cap_desc):
                score += 0.5
                matched.append(term)

        if score > 0:
            scored.append((score, list(set(matched)), cap))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, matched, cap in scored[:top_n]:
        result = dict(cap)
        result["match_score"] = score
        result["matched_keywords"] = matched
        results.append(result)

    return results


def format_capability_context(capabilities: list[dict]) -> str:
    """Format matched capabilities for injection into drafter prompt.

    Produces a concise reference block that the LLM can naturally
    weave into the article without reading the full codebase.

    Args:
        capabilities: List of matched capability dicts.

    Returns:
        Formatted string for prompt injection.
    """
    if not capabilities:
        return ""

    lines = []
    for cap in capabilities:
        lines.append(f"### {cap['name']} ({cap['domain']})")
        lines.append(f"{cap['description']}")

        # Article hooks (talking points)
        hooks = cap.get("article_hooks", [])
        if hooks:
            lines.append("**Key talking points:**")
            for hook in hooks[:2]:
                lines.append(f"- {hook}")

        # CLI examples
        cli = cap.get("cli_examples", [])
        if cli:
            lines.append("**CLI examples:**")
            for ex in cli[:2]:
                lines.append(f"```bash\n{ex}\n```")

        # Frameworks
        frameworks = cap.get("frameworks", [])
        if frameworks:
            lines.append(f"**Frameworks:** {', '.join(frameworks[:4])}")

        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="ICDEV™ Pulse Capability Scanner (CUI // SP-CTI)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all capabilities")
    group.add_argument("--domains", action="store_true", help="List domains only")
    group.add_argument("--match", type=str, help="Match capabilities to keywords")
    group.add_argument("--format-context", type=str, help="Match and format for drafter injection")

    parser.add_argument("--top-n", type=int, default=5, help="Max capabilities to return (default: 5)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list:
        caps = load_all_capabilities()
        result = {
            "status": "ok",
            "total": len(caps),
            "domains": len(set(c["domain"] for c in caps)),
            "capabilities": [
                {
                    "slug": c["slug"],
                    "name": c["name"],
                    "domain": c["domain"],
                    "keywords": c.get("keywords", [])[:5],
                }
                for c in caps
            ],
        }
    elif args.domains:
        domains = load_domains()
        result = {
            "status": "ok",
            "total_domains": len(domains),
            "total_capabilities": sum(d["capability_count"] for d in domains),
            "domains": domains,
        }
    elif args.match:
        keywords = [kw.strip() for kw in args.match.split() if kw.strip()]
        matched = match_capabilities(keywords, top_n=args.top_n)
        result = {
            "status": "ok",
            "query": args.match,
            "matched": len(matched),
            "capabilities": [
                {
                    "slug": c["slug"],
                    "name": c["name"],
                    "domain": c["domain"],
                    "match_score": c["match_score"],
                    "matched_keywords": c["matched_keywords"],
                    "article_hooks": c.get("article_hooks", [])[:2],
                }
                for c in matched
            ],
        }
    elif args.format_context:
        keywords = [kw.strip() for kw in args.format_context.split() if kw.strip()]
        matched = match_capabilities(keywords, top_n=args.top_n)
        context = format_capability_context(matched)
        result = {
            "status": "ok",
            "query": args.format_context,
            "matched": len(matched),
            "context": context,
        }
    else:
        result = {"status": "error", "error": "No action specified"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
