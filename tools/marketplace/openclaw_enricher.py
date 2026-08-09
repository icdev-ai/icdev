#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""OpenClaw Skill Enrichment Engine — enhance imported skills with 3-engine intelligence.

Runs Innovation (web trends), Creative (alternative approaches), and Research
(proven patterns) engines on imported skills to produce enhanced instructions
and supplementary context files.

Also discovers similar skills on SkillHub for merge candidates.

Scanner-tier only (Ollama qwen3.5, zero Claude cost). Gracefully skips if
engines or Ollama are unavailable.

Usage:
    python tools/marketplace/openclaw_enricher.py --enrich /path/to/skill --json
    python tools/marketplace/openclaw_enricher.py --discover-similar "code review" --json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Graceful imports
# ---------------------------------------------------------------------------
def _try_import(module_path):
    try:
        return __import__(module_path, fromlist=[""])
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Similar skill discovery (SkillHub vector search)
# ---------------------------------------------------------------------------
def discover_similar_skills(query, limit=5):
    """Search SkillHub for similar skills by different authors.

    Args:
        query: Skill name or description to search for.
        limit: Max results.

    Returns:
        dict with similar skills found.
    """
    try:
        from tools.databridge.connectors.skillhub_connector import SkillHubConnector

        conn = SkillHubConnector()
        conn.connect({})
        results = conn.search_skills(query, limit=limit)
        conn.disconnect()
        return {
            "success": True,
            "query": query,
            "similar_count": len(results) if results else 0,
            "similar_skills": results or [],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "similar_skills": []}


# ---------------------------------------------------------------------------
# Engine runners (scanner-tier, graceful fallback)
# ---------------------------------------------------------------------------
def run_innovation_for_skill(skill_name, description):
    """Run Innovation engine web research for a skill's domain.

    Scrapes web for trends, blog posts, and best practices related to the skill.

    Args:
        skill_name: Name of the skill.
        description: Skill description for topic extraction.

    Returns:
        dict with innovation findings.
    """
    try:
        from tools.pulse.engine.scheduler import research_phase

        topic = f"{skill_name}: {description[:200]}"
        result = research_phase(topic_override=topic)
        if result.get("status") == "error":
            return {"success": False, "error": result.get("error", "Research failed"), "findings": []}

        findings = []
        for r in result.get("research_results", []):
            if isinstance(r, dict):
                findings.append(
                    {
                        "title": r.get("title", ""),
                        "summary": r.get("snippet", r.get("summary", ""))[:300],
                        "url": r.get("url", r.get("link", "")),
                        "source": "web_research",
                    }
                )

        synthesis = result.get("synthesis", {})
        return {
            "success": True,
            "findings_count": len(findings),
            "findings": findings[:10],
            "synthesis": synthesis.get("text", synthesis.get("summary", ""))
            if isinstance(synthesis, dict)
            else str(synthesis)[:500],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "findings": []}


def run_creative_for_skill(skill_name, description):
    """Run Creative engine for alternative approaches and edge cases.

    Args:
        skill_name: Name of the skill.
        description: Skill description.

    Returns:
        dict with creative alternatives.
    """
    try:
        from tools.creative.creative_engine import stage_discover

        result = stage_discover(domain=f"{skill_name} {description[:100]}")
        if not result:
            return {"success": True, "alternatives": [], "edge_cases": []}

        alternatives = []
        if isinstance(result, dict):
            for comp in result.get("competitors", result.get("discoveries", [])):
                if isinstance(comp, dict):
                    alternatives.append(
                        {
                            "name": comp.get("name", comp.get("title", "")),
                            "approach": comp.get("description", comp.get("summary", ""))[:200],
                            "source": "creative_engine",
                        }
                    )

        return {
            "success": True,
            "alternatives_count": len(alternatives),
            "alternatives": alternatives[:5],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "alternatives": []}


def run_research_for_skill(skill_name, description, focus_areas=None):
    """Run Research engine for proven industry patterns.

    Args:
        skill_name: Name of the skill.
        description: Skill description.
        focus_areas: Optional list of focus areas.

    Returns:
        dict with research patterns.
    """
    try:
        from tools.research.research_engine import run_pipeline

        result = run_pipeline(
            vertical=re.sub(r"[^a-z0-9-]", "-", skill_name.lower())[:30],
            name=f"Skill enrichment: {skill_name}",
            description=description[:300],
            focus_areas=focus_areas or [skill_name],
        )
        if not result or result.get("error"):
            return {
                "success": False,
                "error": result.get("error", "Pipeline failed") if result else "No result",
                "patterns": [],
            }

        patterns = []
        stages = result.get("stages", {})
        for stage_name, stage_data in stages.items():
            if isinstance(stage_data, dict):
                for key in ("findings", "signals", "challenges", "trends"):
                    for item in stage_data.get(key, []):
                        if isinstance(item, dict):
                            patterns.append(
                                {
                                    "stage": stage_name,
                                    "title": item.get("title", item.get("name", ""))[:100],
                                    "detail": item.get("description", item.get("summary", ""))[:200],
                                    "source": "research_engine",
                                }
                            )

        return {
            "success": True,
            "session_id": result.get("session_id", ""),
            "patterns_count": len(patterns),
            "patterns": patterns[:10],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "patterns": []}


# ---------------------------------------------------------------------------
# Enrichment orchestrator
# ---------------------------------------------------------------------------
def enrich_skill(skill_path, skip_engines=None):
    """Run full enrichment pipeline on an imported skill.

    Stages:
        1. Discover similar skills on SkillHub (merge candidates)
        2. Innovation engine (web trends + best practices)
        3. Creative engine (alternative approaches)
        4. Research engine (proven patterns)
        5. Generate enhanced instructions + context files

    Args:
        skill_path: Path to skill directory (with SKILL.md).
        skip_engines: Optional set of engines to skip ('innovation', 'creative', 'research', 'discover').

    Returns:
        dict with enrichment results and generated files.
    """
    skill_path = Path(skill_path)
    skip = set(skip_engines or [])

    # Parse skill metadata
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_path / "skill.md"
    if not skill_md.exists():
        return {"success": False, "error": "No SKILL.md found"}

    import yaml

    content = skill_md.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        body = fm_match.group(2)
    else:
        frontmatter = {}
        body = content

    skill_name = frontmatter.get("name", skill_path.name)
    description = frontmatter.get("description", body[:200])

    results = {
        "skill_name": skill_name,
        "engines_run": [],
        "engines_skipped": [],
        "engines_failed": [],
    }

    # Stage 1: Discover similar skills
    if "discover" not in skip:
        similar = discover_similar_skills(f"{skill_name} {description[:100]}")
        results["similar_skills"] = similar
        if similar.get("success"):
            results["engines_run"].append("discover")
            results["merge_candidates"] = similar.get("similar_count", 0)
        else:
            results["engines_failed"].append("discover")
    else:
        results["engines_skipped"].append("discover")

    # Stage 2: Innovation engine
    if "innovation" not in skip:
        innovation = run_innovation_for_skill(skill_name, description)
        results["innovation"] = innovation
        if innovation.get("success"):
            results["engines_run"].append("innovation")
        else:
            results["engines_failed"].append("innovation")
    else:
        results["engines_skipped"].append("innovation")

    # Stage 3: Creative engine
    if "creative" not in skip:
        creative = run_creative_for_skill(skill_name, description)
        results["creative"] = creative
        if creative.get("success"):
            results["engines_run"].append("creative")
        else:
            results["engines_failed"].append("creative")
    else:
        results["engines_skipped"].append("creative")

    # Stage 4: Research engine
    if "research" not in skip:
        research = run_research_for_skill(skill_name, description)
        results["research"] = research
        if research.get("success"):
            results["engines_run"].append("research")
        else:
            results["engines_failed"].append("research")
    else:
        results["engines_skipped"].append("research")

    # Stage 5: Generate context files
    context_dir = skill_path / "context"
    context_dir.mkdir(exist_ok=True)
    files_generated = []

    # Use cases from similar skills
    similar_data = results.get("similar_skills", {})
    if similar_data.get("similar_skills"):
        use_cases_content = "# Similar Skills & Merge Candidates\n\n"
        use_cases_content += f"Found {similar_data['similar_count']} similar skills on SkillHub.\n\n"
        for s in similar_data["similar_skills"][:5]:
            slug = s.get("slug", "")
            name = s.get("displayName", slug)
            summary = s.get("summary", "")[:200]
            score = s.get("score", 0)
            use_cases_content += f"## {name}\n"
            use_cases_content += f"- **Slug:** {slug}\n"
            use_cases_content += f"- **Relevance:** {score:.2f}\n"
            use_cases_content += f"- **Summary:** {summary}\n"
            use_cases_content += f"- *Adapted from @{slug} on SkillHub*\n\n"
        (context_dir / "merge_candidates.md").write_text(use_cases_content, encoding="utf-8", newline="")
        files_generated.append("context/merge_candidates.md")

    # Best practices from Innovation
    innovation_data = results.get("innovation", {})
    if innovation_data.get("findings"):
        practices_content = "# Best Practices & Trends\n\n"
        practices_content += "*Generated by ICDEV™ Innovation Engine (web research)*\n\n"
        synthesis = innovation_data.get("synthesis", "")
        if synthesis:
            practices_content += f"## Synthesis\n\n{synthesis}\n\n"
        practices_content += "## Sources\n\n"
        for f in innovation_data["findings"][:10]:
            practices_content += f"- **{f.get('title', 'Untitled')}**\n"
            practices_content += f"  {f.get('summary', '')}\n"
            if f.get("url"):
                practices_content += f"  Source: {f['url']}\n"
            practices_content += "\n"
        (context_dir / "best_practices.md").write_text(practices_content, encoding="utf-8", newline="")
        files_generated.append("context/best_practices.md")

    # Alternative approaches from Creative
    creative_data = results.get("creative", {})
    if creative_data.get("alternatives"):
        alt_content = "# Alternative Approaches\n\n"
        alt_content += "*Generated by ICDEV™ Creative Engine*\n\n"
        for a in creative_data["alternatives"][:5]:
            alt_content += f"## {a.get('name', 'Alternative')}\n"
            alt_content += f"{a.get('approach', '')}\n\n"
        (context_dir / "alternatives.md").write_text(alt_content, encoding="utf-8", newline="")
        files_generated.append("context/alternatives.md")

    # Industry patterns from Research
    research_data = results.get("research", {})
    if research_data.get("patterns"):
        patterns_content = "# Proven Industry Patterns\n\n"
        patterns_content += "*Generated by ICDEV™ Research Engine*\n\n"
        for p in research_data["patterns"][:10]:
            patterns_content += f"## {p.get('title', 'Pattern')}\n"
            patterns_content += f"*Stage: {p.get('stage', 'N/A')}*\n\n"
            patterns_content += f"{p.get('detail', '')}\n\n"
        (context_dir / "patterns.md").write_text(patterns_content, encoding="utf-8", newline="")
        files_generated.append("context/patterns.md")

    # Enhance the SKILL.md with enrichment section
    enrichment_section = "\n\n## Enrichment (ICDEV™ Intelligence)\n\n"
    enrichment_section += f"*Auto-generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d')} by Innovation + Creative + Research engines*\n\n"

    if innovation_data.get("synthesis"):
        enrichment_section += f"### Industry Context\n\n{innovation_data['synthesis'][:500]}\n\n"

    if creative_data.get("alternatives"):
        enrichment_section += "### Alternative Approaches\n\n"
        for a in creative_data["alternatives"][:3]:
            enrichment_section += f"- **{a.get('name', '')}**: {a.get('approach', '')}\n"
        enrichment_section += "\n"

    if research_data.get("patterns"):
        enrichment_section += "### Proven Patterns\n\n"
        for p in research_data["patterns"][:3]:
            enrichment_section += f"- **{p.get('title', '')}**: {p.get('detail', '')}\n"
        enrichment_section += "\n"

    if similar_data.get("similar_skills"):
        enrichment_section += "### Related Skills on SkillHub\n\n"
        for s in similar_data["similar_skills"][:3]:
            enrichment_section += f"- **{s.get('displayName', s.get('slug', ''))}** (relevance: {s.get('score', 0):.2f}) — *Adapted from @{s.get('slug', '')} on SkillHub*\n"
        enrichment_section += "\n"

    # Append enrichment to SKILL.md (before Compatibility Notes if present)
    if "## Compatibility Notes" in content:
        content = content.replace("## Compatibility Notes", enrichment_section + "## Compatibility Notes")
    else:
        content += enrichment_section

    skill_md.write_text(content, encoding="utf-8", newline="")
    files_generated.append("SKILL.md (enhanced)")

    results["success"] = True
    results["files_generated"] = files_generated
    results["files_count"] = len(files_generated)
    results["enrichment_applied"] = True

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="OpenClaw Skill Enrichment Engine")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enrich", metavar="PATH", help="Enrich an imported skill")
    group.add_argument("--discover-similar", metavar="QUERY", help="Find similar skills on SkillHub")

    parser.add_argument(
        "--skip", nargs="*", default=[], help="Engines to skip (innovation, creative, research, discover)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    try:
        if args.enrich:
            result = enrich_skill(args.enrich, skip_engines=set(args.skip))
        elif args.discover_similar:
            result = discover_similar_skills(args.discover_similar)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if result.get("error"):
                print(f"ERROR: {result['error']}")
                sys.exit(1)
            for k, v in result.items():
                if k not in ("innovation", "creative", "research", "similar_skills"):
                    print(f"  {k}: {v}")
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
