# CUI // SP-CTI
"""ICDEV™ Pulse template-aware content drafter.

Updated 2026-03-12: Added LLM router integration for qwen3.5 drafting
and Claude Sonnet rewriting. Templates loaded from args/pulse_templates.yaml.

Pipeline:
  1. Ollama qwen3.5 drafts article (scanner tier, zero Claude tokens)
  2. WriteGuard checks quality (deterministic)
  3. Claude Sonnet rewrites based on findings (planner tier)
  4. Final WriteGuard verification

FORGE-compliant: LLM calls go through tools/llm/router.py, not direct API.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import random
import re
from pathlib import Path
from typing import Any

from slugify import slugify

# Configurable project ID — avoids hardcoding "sparkpilot"
PULSE_PROJECT_ID = os.environ.get("ICDEV_PULSE_PROJECT_ID", os.environ.get("ICDEV_PROJECT_ID", "pulse"))

from tools.pulse.config import (  # noqa: E402
    ARTICLE_TEMPLATES,
    GITHUB_REPO_URL,
    SITE_URL,
    TARGET_WORD_COUNT_MAX,
    TARGET_WORD_COUNT_MIN,
    TEMPLATE_DEFAULTS,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Template-aware prompt builders
# ---------------------------------------------------------------------------

_CHALLENGE_SOLUTION_PROMPT = """\
You are a senior technical writer producing a long-form thought-leadership \
blog post for the developer and GovTech audience. Your posts are \
educational, deeply technical, and actionable. You write in a confident \
but approachable tone — never salesy.

Rules:
- Write in Markdown.
- Target {word_min}-{word_max} words.
- Use real-world examples and concrete scenarios.
- When referencing ICDEV™, do so naturally in context of solving real \
  problems. Always link to {github_url} on first mention.
- Do NOT include [HERO_IMAGE] — the hero image is added automatically.
- Include [VIDEO_EMBED:topic] where a video walkthrough adds value. \
  The topic inside must be a short phrase (2-4 words) matching one of the pain points.
- Include [SOURCE_REF:url] to cite sources.
- Do NOT use filler phrases like "In today's fast-paced world". Be direct.
- Do NOT use + signs between words in titles. Write natural English titles.
- Do NOT include word counts like "(200 words)" or "(650 words)" in section headers or text.
- Every section must provide concrete, actionable value.
- Include a Call to Action section at the end linking to GitHub ({github_url}).
- Do NOT include links to documentation or community channels.
- Do NOT include CLI commands (python tools/...), internal tool paths, or backend \
  names like Pulse, SAM.gov, or sam_bridge. Describe capabilities generically.
- Start IMMEDIATELY with the Markdown content (# Title on the first line). \
  Do NOT include any preamble like "Here's a blog post" or "Okay, here is". \
  Do NOT use ## for the main title — use a single # for the article title.
"""

_CHALLENGE_SOLUTION_USER = """\
Write a comprehensive blog post using the **Challenge-Solution** template.

## Topic
{topic}

## Pain Points to Address
{pain_points}

## Research Context
{research_context}

## Source URLs (cite where relevant using [SOURCE_REF:url])
{source_urls}

## Required Structure
1. **TL;DR / Executive Summary** — dense summary of challenges and ICDEV™ solutions.
2. **Introduction** — hook with core pain point. No fluff.
3. **The Challenge** — one subsection per pain point. \
   Concrete examples, statistics, real-world impact.
4. **How ICDEV™ Addresses These Challenges** — for each challenge, explain \
   how ICDEV™'s toolchain solves it (compliance automation, agentic AI, \
   DevSecOps, zero trust, SBOM, cATO). Educate, don't pitch. \
   Include [VIDEO_EMBED:topic] where a walkthrough adds value.
5. **Practical Steps You Can Take This Week** — numbered, specific actions.
6. **Conclusion** — forward-looking perspective.
7. **Get Started** — call to action with link to GitHub ({github_url}).

Write the complete post now.
"""

_FEATURE_SPOTLIGHT_PROMPT = """\
You are a senior technical writer producing an educational deep-dive \
article about a specific ICDEV™ feature for the developer and GovTech \
audience. Your posts explain complex concepts clearly, use concrete \
examples, and provide actionable guidance. Professional yet approachable.

Rules:
- Write in Markdown.
- Target {word_min}-{word_max} words.
- Explain concepts before showing implementation.
- Do NOT include CLI commands (python tools/...), internal tool paths, or backend \
  names like Pulse, SAM.gov, or sam_bridge. Describe capabilities generically.
- When referencing ICDEV™, link to {github_url} on first mention.
- Include placeholder markers: [HERO_IMAGE] at top, [SOURCE_REF:url] for citations.
- Do NOT include [VIDEO_EMBED] placeholders — this template uses NO embedded videos.
- Do NOT use filler phrases. Be direct and educational.
- Do NOT include word counts like "(200 words)" or "(650 words)" in section headers or text.
- Include a Call to Action section at the end linking to GitHub ({github_url}).
- Do NOT include links to documentation or community channels.
- Start IMMEDIATELY with the Markdown content (# Title on the first line). \
  Do NOT include any preamble like "Here's a blog post" or "Okay, here is". \
  Do NOT use ## for the main title — use a single # for the article title.
"""

_FEATURE_SPOTLIGHT_USER = """\
Write a comprehensive feature spotlight article.

## Feature / Topic
{topic}

## Research Context
{research_context}

## Source URLs (cite where relevant using [SOURCE_REF:url])
{source_urls}

## Required Structure
1. **TL;DR / Executive Summary** — what the feature does, \
   why it matters, who benefits.
2. **Introduction** — set the stage with the problem this feature solves.
3. **Understanding the Problem** — educate on the domain. \
   Reference standards (NIST, FedRAMP, CMMC, OWASP) where relevant.
4. **How It Works** — detailed walkthrough with architecture, \
   capabilities, config options. Describe generically — no internal CLI commands.
5. **Real-World Use Cases** — 2-3 concrete scenarios with \
   different personas (developer, compliance officer, security engineer).
6. **Getting Started** — step-by-step quickstart. Copy-paste friendly.
7. **Best Practices and Tips** — numbered tips, common pitfalls.
8. **Conclusion** — recap and forward-looking.
9. **Try It Today** — call to action with link to GitHub ({github_url}).

Write the complete post now.
"""

# Legacy single-template prompts (backward compatibility)
DRAFT_SYSTEM_PROMPT = _CHALLENGE_SOLUTION_PROMPT
DRAFT_USER_PROMPT = _CHALLENGE_SOLUTION_USER

REWRITE_INSTRUCTIONS_TEMPLATE = """\
You are a professional editor. Rewrite the provided Markdown text to \
improve quality based on the fixes listed. Return ONLY the improved \
Markdown — no explanations, no preamble.

## Fixes to apply:
{instructions}

## Rules:
- Maintain original meaning and technical accuracy
- Keep all Markdown formatting (headings, lists, code blocks, links)
- Use active voice where possible
- Vary sentence length and structure to sound natural
- Do NOT add new sections or significantly expand content
- Do NOT remove any factual information
- Return the improved Markdown only

## Original text:
{text}
"""

# ---------------------------------------------------------------------------
# Content helpers (deterministic)
# ---------------------------------------------------------------------------


def _count_words(text: str) -> int:
    """Count words in text, ignoring Markdown syntax."""
    clean = re.sub(r"\[.*?\]\(.*?\)", "", text)  # strip links
    clean = re.sub(r"[#*_`>~\-\[\]]", "", clean)  # strip md chars
    return len(clean.split())


def _extract_title(markdown: str) -> str:
    """Extract the first heading from Markdown, stripping LLM preamble."""
    # Strip common LLM conversational preamble lines before the actual content
    _preamble_patterns = [
        re.compile(r"^(?:okay|sure|here(?:'s| is)|certainly|alright)[,.]?\s", re.IGNORECASE),
        re.compile(r"^(?:i'?ll|let me|below is)\s", re.IGNORECASE),
    ]

    cleaned = markdown
    # Remove leading lines that look like LLM preamble (before first heading)
    lines = cleaned.splitlines()
    first_heading_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            first_heading_idx = i
            break
    if first_heading_idx is not None and first_heading_idx > 0:
        # Check if lines before heading are preamble
        pre_lines = lines[:first_heading_idx]
        if all(not line.strip() or any(p.match(line.strip()) for p in _preamble_patterns) for line in pre_lines):
            cleaned = "\n".join(lines[first_heading_idx:])

    def _sanitize_title(title: str) -> str:
        """Clean LLM artifacts from titles."""
        title = re.sub(r"^#+\s*", "", title)  # leading ##
        title = re.sub(r"\*+", "", title)  # bold **
        # Replace " + " word separators with ", " (gemma3 quirk)
        title = re.sub(r"\s*\+\s*", ", ", title)
        # Collapse multiple commas
        title = re.sub(r",\s*,", ",", title)
        return title.strip()

    # Try H1 first
    match = re.search(r"^#\s+(.+)$", cleaned, re.MULTILINE)
    if match:
        return _sanitize_title(match.group(1))

    # Try H2 as fallback (some models use ## instead of #)
    match = re.search(r"^##\s+(.+)$", cleaned, re.MULTILINE)
    if match:
        return _sanitize_title(match.group(1))

    # Last resort: first non-empty, non-preamble line
    for line in cleaned.strip().splitlines():
        line = line.strip()
        if line and not any(p.match(line) for p in _preamble_patterns):
            return _sanitize_title(line[:100])
    return "Untitled Post"


def _strip_llm_preamble(markdown: str) -> str:
    """Remove conversational preamble lines before the first Markdown heading."""
    _preamble_re = [
        re.compile(r"^(?:okay|sure|here(?:'s| is)|certainly|alright)[,.]?\s", re.IGNORECASE),
        re.compile(r"^(?:i'?ll|let me|below is)\s", re.IGNORECASE),
    ]
    lines = markdown.splitlines()
    first_heading = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            first_heading = i
            break
    if first_heading is None or first_heading == 0:
        return markdown
    # Only strip if ALL preceding non-empty lines are preamble
    pre = lines[:first_heading]
    if all(not line.strip() or any(p.match(line.strip()) for p in _preamble_re) for line in pre):
        return "\n".join(lines[first_heading:])
    return markdown


def _extract_tldr(markdown: str) -> str:
    """Extract the TL;DR / Executive Summary section."""
    patterns = [
        r"##\s*TL;DR.*?\n(.*?)(?=\n##|\Z)",
        r"##\s*Executive\s+Summary.*?\n(.*?)(?=\n##|\Z)",
        r"##\s*TL;DR\s*/\s*Executive\s+Summary.*?\n(.*?)(?=\n##|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, markdown, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    # Fallback: first 200 words
    words = markdown.split()[:200]
    return " ".join(words)


def _format_pain_points(pain_points: list[str]) -> str:
    """Format pain points as a numbered list."""
    return "\n".join(f"{i}. {p}" for i, p in enumerate(pain_points, 1))


def _format_source_urls(urls: list[str]) -> str:
    """Format source URLs as a bulleted list."""
    if not urls:
        return "(No source URLs provided — use general knowledge)"
    return "\n".join(f"- {u}" for u in urls)


# ---------------------------------------------------------------------------
# SEO metadata generation (deterministic)
# ---------------------------------------------------------------------------


_SEO_STOP_WORDS = {
    "the",
    "a",
    "an",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "and",
    "or",
    "but",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "with",
    "from",
    "by",
    "how",
    "why",
    "what",
    "when",
    "where",
    "which",
    "who",
    "your",
    "you",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "not",
    "—",
    "-",
    "–",
    "",
    "vs",
    "via",
}

# High-value SEO phrases from ICDEV™ capabilities
_ICDEV_SEO_PHRASES = [
    "ICDEV™",
    "FedRAMP",
    "CMMC",
    "NIST 800-53",
    "ATO",
    "cATO",
    "zero trust",
    "DevSecOps",
    "SBOM",
    "compliance automation",
    "agentic AI",
    "secure by design",
    "supply chain security",
    "OSCAL",
    "digital thread",
    "MBSE",
    "threat modeling",
    "AI governance",
    "prompt injection",
    "MITRE ATLAS",
    "GovTech",
    "federal software",
    "policy-as-code",
]


def _generate_seo_metadata(title: str, tldr: str, topic: str = "") -> dict[str, str]:
    """Generate SEO metadata using deterministic keyword extraction.

    Combines keywords from title, topic, and ICDEV™ domain phrases.
    """
    # Extract words from title + topic
    combined_text = f"{title} {topic}"
    words = [w.lower().strip(".,;:!?\"'()") for w in combined_text.split()]
    base_keywords = [w for w in words if w and w not in _SEO_STOP_WORDS]

    # Check for ICDEV™-specific multi-word phrases in the combined text
    text_lower = combined_text.lower()
    phrase_hits = [p for p in _ICDEV_SEO_PHRASES if p.lower() in text_lower]

    # Build keyword list: phrase hits first, then single words, deduped
    seen = set()
    keywords = []
    for phrase in phrase_hits:
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            keywords.append(phrase)
    for word in base_keywords:
        if word not in seen and len(word) > 2:
            seen.add(word)
            keywords.append(word)
    keywords = keywords[:10]

    return {
        "seo_title": title[:60],
        "seo_description": tldr[:155].replace("\n", " "),
        "seo_keywords": ", ".join(keywords),
    }


# ---------------------------------------------------------------------------
# Template-aware draft context builder
# ---------------------------------------------------------------------------


def get_template(template_type: str) -> dict:
    """Get template definition from ARTICLE_TEMPLATES config.

    Args:
        template_type: 'challenge_solution' or 'feature_spotlight'.

    Returns:
        Template dict from args/pulse_templates.yaml, or empty dict.
    """
    return ARTICLE_TEMPLATES.get(template_type, {})


def build_draft_context(
    cluster: dict[str, Any],
    template_type: str = "challenge_solution",
    research_context: str = "",
    capability_context: str = "",
) -> dict[str, str]:
    """Build prompt context for LLM-based article drafting.

    Selects the appropriate template and builds system + user prompts
    for the LLM router to invoke via pulse_draft function.

    Args:
        cluster: Dict with keys: topic/name, pain_points, source_urls.
        template_type: 'challenge_solution' or 'feature_spotlight'.
        research_context: Ollama synthesis text for context injection.
        capability_context: ICDEV™ capability reference (from capability_scanner).

    Returns:
        Dict with system_prompt, user_prompt, topic, pain_points, template_type.
    """
    template = get_template(template_type)
    word_min = template.get("word_count_min", TARGET_WORD_COUNT_MIN)
    word_max = template.get("word_count_max", TARGET_WORD_COUNT_MAX)

    topic = cluster.get("topic", cluster.get("name", "Untitled Topic"))
    pain_points = cluster.get("pain_points", [])
    if isinstance(pain_points, str):
        pain_points = json.loads(pain_points)
    source_urls = cluster.get("source_urls", [])
    if isinstance(source_urls, str):
        source_urls = json.loads(source_urls)

    defaults = TEMPLATE_DEFAULTS or {}
    github_url = defaults.get("github_url", GITHUB_REPO_URL)
    site_url = defaults.get("site_url", SITE_URL)

    fmt_kwargs = {
        "word_min": word_min,
        "word_max": word_max,
        "github_url": github_url,
        "site_url": site_url,
        "topic": topic,
        "pain_points": _format_pain_points(pain_points),
        "source_urls": _format_source_urls(source_urls),
        "research_context": research_context or "(No research context provided)",
    }

    if template_type == "feature_spotlight":
        system = _FEATURE_SPOTLIGHT_PROMPT.format(**fmt_kwargs)
        user = _FEATURE_SPOTLIGHT_USER.format(**fmt_kwargs)
    else:
        # Default: challenge_solution
        system = _CHALLENGE_SOLUTION_PROMPT.format(**fmt_kwargs)
        user = _CHALLENGE_SOLUTION_USER.format(**fmt_kwargs)

    # Inject style profile (adapted from AWS Public Sector Blog)
    _style_path = Path(__file__).resolve().parent.parent.parent.parent / "context" / "pulse" / "style_profile.md"
    if _style_path.exists():
        try:
            _style = _style_path.read_text(encoding="utf-8")
            system += f"\n\n## WRITING STYLE GUIDE (mandatory)\n{_style}\n"
        except Exception:
            pass

    # Inject a randomized writing angle to ensure variety across runs
    angles = [
        "Write from the perspective of a frustrated developer who just lived through this.",
        "Frame this as lessons learned from a real federal agency modernization project.",
        "Lead with a surprising statistic or counterintuitive insight.",
        "Structure this as a before/after transformation story.",
        "Write as if briefing a non-technical executive who controls the budget.",
        "Frame this through the lens of a recent compliance audit that went wrong.",
        "Start with a war story from the trenches of DevSecOps adoption.",
        "Write this as a practical field guide — less theory, more step-by-step.",
        "Approach this from a security engineer's midnight incident response perspective.",
        "Frame this as a comparison of what worked vs. what failed across multiple teams.",
        "Write as a 2026 retrospective on how the industry changed in the past year.",
        "Lead with the hidden costs that nobody talks about in this domain.",
    ]
    chosen_angle = random.choice(angles)
    user += f"\n\n## Writing Angle\n{chosen_angle}\n"

    # Inject ICDEV™ capability context (D-PULSE-CAP-1)
    if capability_context:
        user += (
            "\n\n## ICDEV™ Capabilities (Reference these naturally)\n"
            "The following ICDEV™ capabilities are relevant to this topic. "
            "Reference them naturally in the article — explain how they solve "
            "real problems. Include CLI examples where they add value. "
            "Do NOT list all capabilities — weave the most relevant ones into "
            "the narrative.\n\n"
            f"{capability_context}\n"
        )

    return {
        "system_prompt": system,
        "user_prompt": user,
        "topic": topic,
        "pain_points": pain_points,
        "source_urls": source_urls,
        "template_type": template_type,
        "word_min": word_min,
        "word_max": word_max,
    }


def build_rewrite_context(
    text: str,
    findings: list[dict],
    capability_context: str = "",
) -> dict[str, str]:
    """Build prompt context for Claude Sonnet to rewrite an article.

    Args:
        text: Original markdown content.
        findings: List of WriteGuard findings with category, message, suggestion.
        capability_context: Optional ICDEV™ capability reference text to weave in.

    Returns:
        Dict with 'instructions' and 'prompt' for the LLM router.
    """
    instructions = "\n".join(
        f"- {f.get('category', 'general')}: {f.get('message', '')} → {f.get('suggestion', '')}"
        for f in findings
        if f.get("message")
    )

    if capability_context:
        instructions += (
            "\n\n## ICDEV™ Capabilities (Weave these into the article naturally)\n"
            "Reference these ICDEV™ capabilities where they fit the narrative. "
            "Explain how they solve the problems discussed. Include CLI examples "
            "where they add value. Do NOT list all capabilities — integrate the "
            "most relevant ones naturally into existing sections.\n\n"
            f"{capability_context}"
        )

    # Add style profile to rewrite instructions
    _style_path = Path(__file__).resolve().parent.parent.parent.parent / "context" / "pulse" / "style_profile.md"
    if _style_path.exists():
        try:
            _style = _style_path.read_text(encoding="utf-8")
            instructions += f"\n\n## WRITING STYLE GUIDE (adapt the article to match this voice)\n{_style}\n"
        except Exception:
            pass

    prompt = REWRITE_INSTRUCTIONS_TEMPLATE.format(
        instructions=instructions,
        text=text,
    )

    return {
        "instructions": instructions,
        "prompt": prompt,
        "finding_count": len(findings),
    }


# ---------------------------------------------------------------------------
# LLM Router integration — draft and rewrite via router
# ---------------------------------------------------------------------------


def draft_article_via_llm(
    cluster: dict[str, Any],
    template_type: str = "challenge_solution",
    research_context: str = "",
    capability_context: str = "",
) -> dict[str, Any]:
    """Draft an article using the LLM router (scanner tier: qwen3.5).

    This function calls the LLM router with function='pulse_draft',
    which routes to qwen3.5 (zero Claude tokens).

    Args:
        cluster: Topic cluster dict.
        template_type: Article template to use.
        research_context: Synthesis text from research phase.
        capability_context: Formatted ICDEV™ capability reference text.

    Returns:
        Dict with 'body_markdown', 'model_used', 'tokens', 'status'.
    """
    ctx = build_draft_context(cluster, template_type, research_context, capability_context)

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": ctx["user_prompt"]}],
            system_prompt=ctx["system_prompt"],
            max_tokens=8192,
            project_id=PULSE_PROJECT_ID,
            skip_injection_scan=True,  # Internal pipeline — topic seeds trigger false positives
        )
        response = router.invoke("pulse_draft", request)

        body = response.content or ""
        # Strip thinking tags if qwen3.5 includes them
        body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL).strip()

        # Strip LLM conversational preamble before first heading
        body = _strip_llm_preamble(body)

        logger.info(
            "Article drafted via LLM router: %d words, model=%s",
            _count_words(body),
            response.model_id,
        )

        return {
            "body_markdown": body,
            "model_used": response.model_id,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "status": "drafted",
            "template_type": template_type,
        }
    except Exception as e:
        logger.error("LLM draft failed: %s", e)
        return {
            "body_markdown": "",
            "model_used": "none",
            "status": "draft_failed",
            "error": str(e),
        }


def rewrite_article_via_llm(
    text: str,
    findings: list[dict],
    capability_context: str = "",
) -> dict[str, Any]:
    """Rewrite an article using the LLM router (planner tier: Claude Sonnet).

    This function calls the LLM router with function='pulse_rewrite',
    which routes to Claude Sonnet (planner tier).

    Args:
        text: Original markdown content.
        findings: WriteGuard findings to address.
        capability_context: Optional ICDEV™ capability reference text to weave in.

    Returns:
        Dict with 'body_markdown', 'model_used', 'tokens', 'status'.
    """
    ctx = build_rewrite_context(text, findings, capability_context)

    if not ctx["instructions"].strip():
        logger.info("No findings to address — skipping rewrite")
        return {
            "body_markdown": text,
            "model_used": "none",
            "status": "no_rewrite_needed",
        }

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": ctx["prompt"]}],
            system_prompt=(
                "You are a professional editor. Return only improved Markdown. "
                "CRITICAL: Never include internal tool names (Pulse, SAM.gov), CLI commands "
                "(python tools/...), or backend implementation details in published content."
            ),
            max_tokens=8192,
            project_id=PULSE_PROJECT_ID,
            skip_injection_scan=True,  # Internal pipeline — article content triggers false positives
        )
        response = router.invoke("pulse_rewrite", request)

        body = response.content or text
        # Strip thinking tags
        body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL).strip()

        logger.info(
            "Article rewritten via LLM router: %d words, model=%s",
            _count_words(body),
            response.model_id,
        )

        return {
            "body_markdown": body,
            "model_used": response.model_id,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "status": "rewritten",
        }
    except Exception as e:
        logger.error("LLM rewrite failed: %s", e)
        return {
            "body_markdown": text,
            "model_used": "none",
            "status": "rewrite_failed",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Main entry point — process pre-written content
# ---------------------------------------------------------------------------


def _ensure_unique_title(title: str, existing_titles: list[str]) -> str:
    """Ensure title is unique by appending a differentiator if needed."""
    lower_existing = {t.lower().strip() for t in existing_titles}
    if title.lower().strip() not in lower_existing:
        return title
    # Try numeric suffix
    for i in range(2, 20):
        candidate = f"{title} — Part {i}"
        if candidate.lower().strip() not in lower_existing:
            logger.info("Duplicate title detected, renamed to: %s", candidate)
            return candidate
    return title  # Give up — slug dedup will catch it downstream


def process_draft(
    body_markdown: str,
    cluster: dict[str, Any],
) -> dict[str, Any]:
    """Process a draft into a structured post dict.

    Extracts metadata and prepares the post for the pipeline (steps 4-9).
    Enforces unique titles and slugs to prevent duplicates.

    Args:
        body_markdown: Markdown content (from LLM or Claude Code).
        cluster: Topic cluster dict for context.

    Returns:
        Dict with keys: title, slug, body_markdown, tldr, seo_title,
        seo_description, seo_keywords, word_count, source_urls.
    """
    source_urls = cluster.get("source_urls", [])
    if isinstance(source_urls, str):
        source_urls = json.loads(source_urls)

    title = _extract_title(body_markdown)

    # Prevent duplicate titles
    try:
        from tools.pulse.db import get_existing_titles

        existing = get_existing_titles()
        title = _ensure_unique_title(title, existing)
    except Exception:
        pass  # Graceful fallback if DB unavailable

    tldr = _extract_tldr(body_markdown)
    slug = slugify(title, max_length=80)
    word_count = _count_words(body_markdown)

    topic = cluster.get("name", "") or cluster.get("description", "")
    seo = _generate_seo_metadata(title=title, tldr=tldr, topic=topic)

    logger.info(
        "Post processed: '%s' (%s) — %d words",
        title,
        slug,
        word_count,
    )

    return {
        "title": title,
        "slug": slug,
        "body_markdown": body_markdown,
        "tldr": tldr,
        "seo_title": seo["seo_title"],
        "seo_description": seo["seo_description"],
        "seo_keywords": seo["seo_keywords"],
        "word_count": word_count,
        "source_urls": source_urls,
    }


# Keep backward compatibility alias
draft_post = process_draft
