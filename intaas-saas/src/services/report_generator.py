#!/usr/bin/env python3
"""Report generator — produces formatted intelligence reports from topic analyses.

5 report templates adapted from INTaaS child app:
  1. sitrep       — Situation Report (quick tactical overview)
  2. intel_summary — Intelligence Summary (comprehensive source analysis)
  3. deep_dive    — Deep Dive (full bias forensics + contrarian + gaps)
  4. executive_brief — Executive Briefing (1-page decision-maker summary)
  5. threat_warning — Threat Warning (information integrity alert)
"""
import logging
from datetime import datetime, timezone

from src.db import dynamo
from src.services import contrarian_service, credibility_db

logger = logging.getLogger("intaas.services.report")

TEMPLATES = {
    "sitrep": "Situation Report",
    "intel_summary": "Intelligence Summary",
    "deep_dive": "Deep Dive Analysis",
    "executive_brief": "Executive Briefing",
    "threat_warning": "Information Integrity Warning",
}


def generate_report(topic_id: str, template: str = "executive_brief") -> dict:
    """Generate a formatted report for a topic."""
    if template not in TEMPLATES:
        return {"error": f"Unknown template: {template}. Options: {list(TEMPLATES.keys())}"}

    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    perspectives = dynamo.get_perspectives(topic_id)
    articles = dynamo.get_articles_for_topic(topic_id)

    title = topic.get("title", "Untitled")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    source_count = len(perspectives)
    lang_count = int(topic.get("language_count", 1))

    # Compute stats
    scores = [float(p.get("bias_composite", 3.0)) for p in perspectives]
    avg_bias = round(sum(scores) / max(len(scores), 1), 2)
    colors = {}
    for p in perspectives:
        c = p.get("bias_color", "green")
        colors[c] = colors.get(c, 0) + 1

    # Credibility breakdown
    cred_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0, "?": 0}
    for p in perspectives:
        cred = credibility_db.lookup_source(p.get("url", ""))
        grade = cred.get("credibility", "?")
        cred_counts[grade] = cred_counts.get(grade, 0) + 1

    generators = {
        "sitrep": _gen_sitrep,
        "intel_summary": _gen_intel_summary,
        "deep_dive": _gen_deep_dive,
        "executive_brief": _gen_executive_brief,
        "threat_warning": _gen_threat_warning,
    }

    body = generators[template](
        title=title,
        now=now,
        perspectives=perspectives,
        articles=articles,
        source_count=source_count,
        lang_count=lang_count,
        avg_bias=avg_bias,
        colors=colors,
        cred_counts=cred_counts,
        topic=topic,
        topic_id=topic_id,
    )

    return {
        "status": "generated",
        "template": template,
        "template_name": TEMPLATES[template],
        "topic_id": topic_id,
        "title": f"{TEMPLATES[template]}: {title}",
        "generated_at": now,
        "body_markdown": body,
        "metadata": {
            "sources": source_count,
            "languages": lang_count,
            "avg_bias": avg_bias,
            "color_distribution": colors,
            "credibility_distribution": cred_counts,
        },
    }


def _gen_sitrep(**kw) -> str:
    """Situation Report — quick tactical overview."""
    md = f"# SITREP: {kw['title']}\n"
    md += f"**Generated:** {kw['now']}\n\n"
    md += "## Situation Overview\n"
    md += (
        f"- **{kw['source_count']}** sources analyzed "
        f"across **{kw['lang_count']}** languages\n"
    )
    md += f"- Average bias score: **{kw['avg_bias']}/5.0**\n"
    md += f"- Color distribution: {_format_colors(kw['colors'])}\n\n"
    md += "## Key Perspectives\n\n"
    for p in kw["perspectives"][:5]:
        md += (
            f"- **{p.get('source_name', '?')}** "
            f"({p.get('bias_color', '?')} {float(p.get('bias_composite', 3.0)):.1f}): "
            f"{p.get('title', '')}\n"
        )
    md += "\n## Assessment\n"
    md += _assessment_text(kw["avg_bias"], kw["source_count"])
    return md


def _gen_intel_summary(**kw) -> str:
    """Intelligence Summary — comprehensive source analysis."""
    md = f"# Intelligence Summary: {kw['title']}\n"
    md += f"**Generated:** {kw['now']}\n\n"
    md += "## Source Analysis\n\n"
    md += "| Source | Bias | Credibility | Language |\n"
    md += "|--------|------|-------------|----------|\n"
    for p in kw["perspectives"]:
        cred = credibility_db.lookup_source(p.get("url", ""))
        md += (
            f"| {p.get('source_name', '?')} "
            f"| {p.get('bias_color', '?')} {float(p.get('bias_composite', 3.0)):.1f} "
            f"| {cred.get('credibility', '?')} {cred.get('political', '')} "
            f"| {p.get('language', 'en')} |\n"
        )
    md += "\n## Statistics\n"
    md += f"- Sources: {kw['source_count']}\n"
    md += f"- Languages: {kw['lang_count']}\n"
    md += f"- Average bias: {kw['avg_bias']}/5.0\n"
    md += f"- Credibility: {_format_cred(kw['cred_counts'])}\n"
    return md


def _gen_deep_dive(**kw) -> str:
    """Deep Dive — full bias forensics + contrarian + gaps."""
    md = f"# Deep Dive Analysis: {kw['title']}\n"
    md += f"**Generated:** {kw['now']}\n\n"
    md += _gen_intel_summary(**kw).split("## Statistics")[0]
    md += "\n## Bias Distribution\n\n"
    md += f"Color breakdown: {_format_colors(kw['colors'])}\n\n"

    # Contrarian
    contrarian = contrarian_service.generate_contrarian(
        kw["title"], kw["perspectives"],
    )
    if contrarian.get("contrarian_view"):
        md += "## Contrarian View\n\n"
        md += f"**Dominant narrative:** {contrarian.get('dominant_narrative', '')}\n\n"
        md += f"**Contrarian position:** {contrarian.get('contrarian_view', '')}\n\n"
        if contrarian.get("key_question"):
            md += f"**Key question:** *{contrarian['key_question']}*\n\n"
        if contrarian.get("blind_spot"):
            md += f"**Blind spot:** {contrarian['blind_spot']}\n\n"

    md += "## Assessment\n\n"
    md += _assessment_text(kw["avg_bias"], kw["source_count"])
    return md


def _gen_executive_brief(**kw) -> str:
    """Executive Briefing — 1-page decision-maker summary."""
    md = f"# Executive Briefing: {kw['title']}\n"
    md += f"**Generated:** {kw['now']}\n\n"
    md += "## Bottom Line\n\n"
    md += _assessment_text(kw["avg_bias"], kw["source_count"]) + "\n\n"
    md += "## Key Findings\n\n"
    md += (
        f"- **{kw['source_count']}** sources across "
        f"**{kw['lang_count']}** languages analyzed\n"
    )
    md += f"- Average bias score: **{kw['avg_bias']}/5.0**\n"
    md += f"- Credibility mix: {_format_cred(kw['cred_counts'])}\n"
    md += f"- Bias spread: {_format_colors(kw['colors'])}\n\n"
    md += "## Top Sources (by credibility)\n\n"
    rated = [
        (p, credibility_db.lookup_source(p.get("url", "")))
        for p in kw["perspectives"]
    ]
    rated.sort(
        key=lambda x: "ABCDF?".index(x[1].get("credibility", "?")),
    )
    for p, cred in rated[:5]:
        md += (
            f"- **{p.get('source_name', '?')}** "
            f"(Credibility: {cred.get('credibility', '?')}, "
            f"Bias: {p.get('bias_color', '?')} "
            f"{float(p.get('bias_composite', 3.0)):.1f}): "
            f"{p.get('title', '')[:80]}\n"
        )
    md += "\n## Recommendation\n\n"
    if kw["avg_bias"] >= 3.5:
        md += "Coverage is broadly balanced. Multiple credible sources confirm key facts.\n"
    elif kw["avg_bias"] >= 2.5:
        md += (
            "Coverage shows moderate bias. Cross-reference claims "
            "with high-credibility sources before acting.\n"
        )
    else:
        md += (
            "Coverage is significantly biased. Exercise caution. "
            "Seek primary sources before making decisions.\n"
        )
    return md


def _gen_threat_warning(**kw) -> str:
    """Information Integrity Warning."""
    md = f"# Information Integrity Warning: {kw['title']}\n"
    md += f"**Generated:** {kw['now']}\n\n"

    # Count red flags
    red_flags = []
    if kw["avg_bias"] < 2.5:
        red_flags.append("Average bias score below 2.5 (significant bias)")
    if kw["cred_counts"].get("D", 0) + kw["cred_counts"].get("F", 0) > 3:
        red_flags.append("Multiple low-credibility or unreliable sources")
    if kw["colors"].get("red", 0) > 0:
        red_flags.append(f"{kw['colors']['red']} source(s) rated RED (unacceptable)")
    yellow = kw["colors"].get("yellow", 0)
    if yellow > kw["source_count"] * 0.5:
        red_flags.append(f"{yellow}/{kw['source_count']} sources rated YELLOW or worse")
    if kw["source_count"] < 5:
        red_flags.append("Fewer than 5 sources — limited perspective diversity")

    md += f"## Threat Level: {'HIGH' if len(red_flags) >= 3 else 'MODERATE' if red_flags else 'LOW'}\n\n"

    if red_flags:
        md += "## Red Flags\n\n"
        for flag in red_flags:
            md += f"- {flag}\n"
        md += "\n"

    md += "## Recommended Actions\n\n"
    if red_flags:
        md += "1. Verify all claims against primary sources\n"
        md += "2. Cross-reference with A-rated credibility sources\n"
        md += "3. Check for coordinated narratives (same quotes, same framing)\n"
        md += "4. Consider what ALL sources are omitting\n"
    else:
        md += "No significant information integrity threats detected.\n"

    md += "\n## Coverage Summary\n"
    md += f"- Sources: {kw['source_count']}, Languages: {kw['lang_count']}\n"
    md += f"- Avg bias: {kw['avg_bias']}/5.0\n"
    md += f"- Credibility: {_format_cred(kw['cred_counts'])}\n"
    return md


def _format_colors(colors: dict) -> str:
    parts = []
    for c in ["blue", "purple", "green", "yellow", "red"]:
        if colors.get(c, 0) > 0:
            parts.append(f"{c.upper()}: {colors[c]}")
    return ", ".join(parts) if parts else "None"


def _format_cred(cred: dict) -> str:
    parts = []
    for g in ["A", "B", "C", "D", "F", "?"]:
        if cred.get(g, 0) > 0:
            parts.append(f"{g}: {cred[g]}")
    return ", ".join(parts) if parts else "None"


def _assessment_text(avg_bias: float, source_count: int) -> str:
    if avg_bias >= 4.0:
        return (
            f"Based on {source_count} sources, coverage is **exceptionally balanced**. "
            "Multiple viewpoints represented with strong sourcing."
        )
    if avg_bias >= 3.0:
        return (
            f"Based on {source_count} sources, coverage is **acceptable** "
            "but some perspectives are missing. Standard news reporting."
        )
    if avg_bias >= 2.0:
        return (
            f"Based on {source_count} sources, coverage shows **significant bias**. "
            "Loaded language and one-sided framing detected in multiple sources."
        )
    return (
        f"Based on {source_count} sources, coverage is **heavily biased**. "
        "Exercise extreme caution. Seek primary sources."
    )
