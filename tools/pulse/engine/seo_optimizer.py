
from tools.logging.icdev_logger import get_logger
"""ICDEV™ Pulse SEO optimization engine.

Optimizes blog posts for search engines: title/meta tuning, keyword
extraction, JSON-LD schema markup, YAML frontmatter generation,
sitemap entries, and scoring with actionable recommendations.
"""

import re
from datetime import datetime, timezone
from typing import Any

from tools.pulse import config

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITE_DOMAIN = config.SITE_DOMAIN  # "icdev.ai"
SITE_URL = config.SITE_URL  # "https://icdev.ai"
GITHUB_REPO_URL = config.GITHUB_REPO_URL  # "https://github.com/icdev-ai"

TITLE_MIN_LEN = 50
TITLE_MAX_LEN = 60
META_DESC_MIN_LEN = 150
META_DESC_MAX_LEN = 155
SLUG_MAX_LEN = 60
TARGET_KEYWORD_COUNT = (5, 8)  # min, max
MIN_WORD_COUNT = 300
IDEAL_WORD_COUNT = 1500
IDEAL_KEYWORD_DENSITY = (0.01, 0.025)  # 1%–2.5%

# Stop words excluded from keyword extraction
_STOP_WORDS: set[str] = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "is",
    "it",
    "as",
    "was",
    "are",
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
    "shall",
    "can",
    "this",
    "that",
    "these",
    "those",
    "not",
    "no",
    "nor",
    "so",
    "if",
    "then",
    "than",
    "too",
    "very",
    "just",
    "about",
    "above",
    "after",
    "again",
    "all",
    "also",
    "any",
    "because",
    "before",
    "between",
    "both",
    "each",
    "few",
    "how",
    "into",
    "its",
    "more",
    "most",
    "new",
    "now",
    "only",
    "other",
    "our",
    "out",
    "over",
    "own",
    "same",
    "she",
    "he",
    "they",
    "we",
    "you",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "your",
    "up",
    "us",
    "use",
    "used",
    "using",
    "here",
    "there",
    "their",
    "them",
    "his",
    "her",
    "him",
    "my",
    "me",
    "i",
    "we",
    "such",
    "some",
    "one",
    "two",
    "many",
    "much",
    "well",
    "get",
    "got",
    "make",
    "made",
    "like",
}

# Score weights (must sum to 100)
SCORE_WEIGHTS: dict[str, int] = {
    "title_length": 15,
    "meta_description": 15,
    "keyword_density": 15,
    "heading_structure": 15,
    "internal_links": 10,
    "image_alt": 10,
    "word_count": 10,
    "readability": 10,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    """Count words in plain text."""
    return len(text.split()) if text else 0


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:SLUG_MAX_LEN]


def _extract_keywords(text: str, n: int = 8) -> list[str]:
    """Extract top-n keywords by frequency, excluding stop words."""
    words = re.findall(r"[a-z]{3,}", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in _STOP_WORDS:
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in ranked[:n]]


def _extract_headings(content: str) -> list[tuple[int, str]]:
    """Extract markdown headings as (level, text) tuples."""
    headings: list[tuple[int, str]] = []
    for match in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE):
        level = len(match.group(1))
        headings.append((level, match.group(2).strip()))
    return headings


def _check_heading_hierarchy(headings: list[tuple[int, str]]) -> list[str]:
    """Verify H1 > H2 > H3 hierarchy; return issues found."""
    issues: list[str] = []
    if not headings:
        issues.append("No headings found in content.")
        return issues

    h1_count = sum(1 for lvl, _ in headings if lvl == 1)
    if h1_count == 0:
        issues.append("Missing H1 heading.")
    elif h1_count > 1:
        issues.append(f"Multiple H1 headings found ({h1_count}). Use exactly one.")

    prev_level = 0
    for lvl, text in headings:
        if prev_level > 0 and lvl > prev_level + 1:
            issues.append(f'Heading level jumps from H{prev_level} to H{lvl} at "{text[:40]}". Do not skip levels.')
        prev_level = lvl

    return issues


def _find_images(content: str) -> list[dict[str, str]]:
    """Find markdown images, returning src and alt text."""
    images: list[dict[str, str]] = []
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", content):
        images.append({"alt": match.group(1), "src": match.group(2)})
    return images


def _count_internal_links(content: str) -> int:
    """Count links pointing to SITE_DOMAIN or GITHUB_REPO_URL."""
    count = 0
    for match in re.finditer(r"\[([^\]]*)\]\(([^)]+)\)", content):
        url = match.group(2)
        if SITE_DOMAIN in url or GITHUB_REPO_URL in url:
            count += 1
    return count


def _flesch_reading_ease(text: str) -> float:
    """Approximate Flesch Reading Ease score.

    Uses simple heuristics for syllable counting — not perfectly
    accurate but sufficient for SEO scoring purposes.
    """
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    words = text.split()

    if not words or not sentences:
        return 0.0

    total_syllables = 0
    for word in words:
        word_clean = re.sub(r"[^a-z]", "", word.lower())
        if not word_clean:
            continue
        # Simple syllable estimation
        vowels = len(re.findall(r"[aeiouy]+", word_clean))
        total_syllables += max(vowels, 1)

    avg_sentence_len = len(words) / len(sentences)
    avg_syllables = total_syllables / len(words) if words else 0

    score = 206.835 - (1.015 * avg_sentence_len) - (84.6 * avg_syllables)
    return max(0.0, min(100.0, score))


def _truncate(text: str, max_len: int, ellipsis: bool = True) -> str:
    """Truncate text to max_len, optionally adding ellipsis."""
    if len(text) <= max_len:
        return text
    cut = max_len - 3 if ellipsis else max_len
    truncated = text[:cut].rsplit(" ", 1)[0]
    return truncated + "..." if ellipsis else truncated


def _keyword_density(content: str, keyword: str) -> float:
    """Calculate keyword density as a fraction of total words."""
    words = content.lower().split()
    if not words:
        return 0.0
    kw_lower = keyword.lower()
    count = sum(1 for w in words if kw_lower in w)
    return count / len(words)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _optimize_title(title: str, primary_keyword: str) -> str:
    """Optimize title for SEO length and keyword presence."""
    optimized = title
    if primary_keyword and primary_keyword.lower() not in title.lower():
        optimized = f"{title} - {primary_keyword.capitalize()}"
        logger.debug("Appended primary keyword to title.")

    if len(optimized) > TITLE_MAX_LEN:
        optimized = _truncate(optimized, TITLE_MAX_LEN)
    elif len(optimized) < TITLE_MIN_LEN and primary_keyword:
        optimized = f"{optimized} | ICDEV™"
        optimized = optimized[:TITLE_MAX_LEN]

    return optimized


def _build_meta_description(description: str, content: str, primary_keyword: str) -> str:
    """Build and optimize meta description from post data."""
    meta = description
    if not meta:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for p in paragraphs:
            if not p.startswith("#"):
                meta = p
                break
        if not meta and paragraphs:
            meta = paragraphs[0]

    # Strip markdown formatting and unresolved placeholders
    meta = re.sub(r"\[HERO_IMAGE\]", "", meta)
    meta = re.sub(r"\[VIDEO_EMBED:[^\]]*\]", "", meta)
    meta = re.sub(r"\[SOURCE_REF:[^\]]*\]", "", meta)
    meta = re.sub(r"[*_`#\[\]()]", "", meta)
    meta = re.sub(r"\s+", " ", meta).strip()

    if len(meta) > META_DESC_MAX_LEN:
        meta = _truncate(meta, META_DESC_MAX_LEN)
    elif len(meta) < META_DESC_MIN_LEN and primary_keyword:
        suffix = f" Learn more about {primary_keyword} at ICDEV™."
        if len(meta) + len(suffix) <= META_DESC_MAX_LEN:
            meta += suffix

    return meta


def optimize_post(post: dict[str, Any]) -> dict[str, Any]:
    """Take a post dict and add/improve SEO fields.

    Expected post keys: title, content, slug (optional), author (optional),
    date (optional), tags (optional), image (optional), description (optional).

    Returns the post dict with added/updated SEO fields under the ``seo`` key.
    """
    logger.info("Optimizing SEO for post: %s", post.get("title", "<untitled>"))

    content: str = post.get("content", "")
    title: str = post.get("title", "")
    description: str = post.get("description", "")
    slug: str = post.get("slug", "")
    tags: list[str] = post.get("tags", [])

    keywords = _extract_keywords(content, TARGET_KEYWORD_COUNT[1])
    primary_keyword = keywords[0] if keywords else ""

    optimized_title = _optimize_title(title, primary_keyword)
    meta_description = _build_meta_description(description, content, primary_keyword)
    slug = _slugify(slug) if slug else _slugify(title)

    if not tags:
        tags = keywords[: TARGET_KEYWORD_COUNT[1]]

    headings = _extract_headings(content)
    heading_issues = _check_heading_hierarchy(headings)
    images = _find_images(content)
    images_missing_alt = [img for img in images if not img["alt"].strip()]
    internal_link_count = _count_internal_links(content)
    canonical_url = f"{SITE_URL}/blog/{slug}"

    seo: dict[str, Any] = {
        "title": optimized_title,
        "meta_description": meta_description,
        "slug": slug,
        "canonical_url": canonical_url,
        "keywords": keywords,
        "primary_keyword": primary_keyword,
        "heading_issues": heading_issues,
        "images_missing_alt": images_missing_alt,
        "internal_link_count": internal_link_count,
        "word_count": _word_count(content),
    }

    post["seo"] = seo
    post["slug"] = slug
    post["tags"] = tags

    logger.info(
        "SEO optimization complete — slug=%s, keywords=%d, word_count=%d",
        slug,
        len(keywords),
        seo["word_count"],
    )
    return post


def generate_schema_json(post: dict[str, Any]) -> dict[str, Any]:
    """Generate JSON-LD Article schema markup for a post.

    Args:
        post: Post dict, ideally already run through ``optimize_post``.

    Returns:
        JSON-LD schema dict ready for embedding in ``<script type="application/ld+json">``.
    """
    seo: dict[str, Any] = post.get("seo", {})
    title = seo.get("title", post.get("title", ""))
    description = seo.get("meta_description", post.get("description", ""))
    slug = seo.get("slug", post.get("slug", ""))
    keywords = seo.get("keywords", post.get("tags", []))
    canonical_url = seo.get("canonical_url", f"{SITE_URL}/blog/{slug}")

    date_published = post.get("date", datetime.now(timezone.utc).isoformat())
    date_modified = post.get("date_modified", date_published)
    author_name = post.get("author", "ICDEV™ Team")
    image = post.get("image", f"{SITE_URL}/images/default-og.png")

    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description,
        "author": {
            "@type": "Person",
            "name": author_name,
        },
        "publisher": {
            "@type": "Organization",
            "name": "ICDEV™",
            "url": SITE_URL,
            "logo": {
                "@type": "ImageObject",
                "url": f"{SITE_URL}/images/icdev-logo.png",
            },
        },
        "datePublished": date_published,
        "dateModified": date_modified,
        "image": image,
        "url": canonical_url,
        "keywords": ", ".join(keywords) if keywords else "",
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": canonical_url,
        },
    }

    logger.debug("Generated JSON-LD schema for: %s", title)
    return schema


def generate_frontmatter(post: dict[str, Any]) -> str:
    """Generate YAML frontmatter string for MDX export.

    Args:
        post: Post dict, ideally already run through ``optimize_post``.

    Returns:
        YAML frontmatter block (including ``---`` delimiters).
    """
    seo: dict[str, Any] = post.get("seo", {})
    title = seo.get("title", post.get("title", ""))
    slug = seo.get("slug", post.get("slug", ""))
    description = seo.get("meta_description", post.get("description", ""))
    keywords = seo.get("keywords", post.get("tags", []))
    canonical_url = seo.get("canonical_url", f"{SITE_URL}/blog/{slug}")

    date = post.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    author = post.get("author", "ICDEV™ Team")
    tags = post.get("tags", keywords)
    image = post.get("image", "")
    draft = post.get("draft", False)

    # Build YAML manually to avoid PyYAML dependency
    lines: list[str] = ["---"]
    lines.append(f'title: "{_yaml_escape(title)}"')
    lines.append(f"slug: {slug}")
    lines.append(f"date: {date}")
    lines.append(f'author: "{_yaml_escape(author)}"')

    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append(f'  - "{_yaml_escape(tag)}"')

    lines.append(f'description: "{_yaml_escape(description)}"')

    if image:
        lines.append(f"image: {image}")

    lines.append(f"draft: {str(draft).lower()}")

    # SEO block
    lines.append("seo:")
    lines.append(f'  title: "{_yaml_escape(title)}"')
    lines.append(f'  description: "{_yaml_escape(description)}"')
    lines.append(f"  canonical: {canonical_url}")
    if keywords:
        lines.append("  keywords:")
        for kw in keywords:
            lines.append(f'    - "{_yaml_escape(kw)}"')

    lines.append("---")

    frontmatter = "\n".join(lines)
    logger.debug("Generated frontmatter for slug: %s", slug)
    return frontmatter


def _score_title(title: str, primary_keyword: str, recommendations: list[str]) -> float:
    """Score title length and keyword presence (15 pts weight)."""
    title_len = len(title)
    if TITLE_MIN_LEN <= title_len <= TITLE_MAX_LEN:
        score = 1.0
    elif title_len < TITLE_MIN_LEN:
        score = title_len / TITLE_MIN_LEN
        recommendations.append(f"Title is {title_len} chars — aim for {TITLE_MIN_LEN}-{TITLE_MAX_LEN} chars.")
    else:
        overshoot = title_len - TITLE_MAX_LEN
        score = max(0.0, 1.0 - overshoot / 20)
        recommendations.append(f"Title is {title_len} chars — trim to under {TITLE_MAX_LEN} chars.")

    if primary_keyword and primary_keyword.lower() not in title.lower():
        score *= 0.7
        recommendations.append(f'Include primary keyword "{primary_keyword}" in the title.')
    return score


def _score_meta_description(meta_desc: str, primary_keyword: str, recommendations: list[str]) -> float:
    """Score meta description length and keyword presence (15 pts weight)."""
    desc_len = len(meta_desc)
    if META_DESC_MIN_LEN <= desc_len <= META_DESC_MAX_LEN:
        score = 1.0
    elif desc_len == 0:
        score = 0.0
        recommendations.append("Add a meta description (150-155 chars).")
    elif desc_len < META_DESC_MIN_LEN:
        score = desc_len / META_DESC_MIN_LEN
        recommendations.append(
            f"Meta description is {desc_len} chars — aim for {META_DESC_MIN_LEN}-{META_DESC_MAX_LEN}."
        )
    else:
        score = max(0.0, 1.0 - (desc_len - META_DESC_MAX_LEN) / 50)
        recommendations.append(f"Meta description is {desc_len} chars — trim to {META_DESC_MAX_LEN}.")

    if primary_keyword and primary_keyword.lower() not in meta_desc.lower():
        score *= 0.8
        recommendations.append("Include primary keyword in the meta description.")
    return score


def _score_keyword_density(content: str, primary_keyword: str, recommendations: list[str]) -> float:
    """Score keyword density (15 pts weight)."""
    if not primary_keyword or not content:
        recommendations.append("No primary keyword detected. Add relevant keywords.")
        return 0.0

    density = _keyword_density(content, primary_keyword)
    min_d, max_d = IDEAL_KEYWORD_DENSITY
    if min_d <= density <= max_d:
        return 1.0
    elif density < min_d:
        recommendations.append(
            f'Keyword "{primary_keyword}" density is {density:.1%} — aim for {min_d:.0%}-{max_d:.0%}.'
        )
        return density / min_d if min_d > 0 else 0.0
    else:
        overshoot = density - max_d
        recommendations.append(
            f'Keyword "{primary_keyword}" density is {density:.1%} — '
            f"reduce to avoid keyword stuffing (target {max_d:.0%})."
        )
        return max(0.0, 1.0 - overshoot / 0.03)


def _score_headings(content: str, heading_issues: list[str], recommendations: list[str]) -> float:
    """Score heading structure (15 pts weight)."""
    headings = _extract_headings(content)
    if not headings:
        recommendations.append("Add headings (H1, H2, H3) to structure the content.")
        return 0.0
    elif heading_issues:
        for issue in heading_issues:
            recommendations.append(f"Heading: {issue}")
        return max(0.0, 1.0 - len(heading_issues) * 0.25)
    return 1.0


def _score_internal_links(internal_link_count: int, recommendations: list[str]) -> float:
    """Score internal link count (10 pts weight)."""
    if internal_link_count >= 3:
        return 1.0
    elif internal_link_count > 0:
        recommendations.append(
            f"Only {internal_link_count} internal link(s) found — add links to "
            f"{GITHUB_REPO_URL} or {SITE_URL} (aim for 3+)."
        )
        return internal_link_count / 3
    recommendations.append(f"No internal links found. Add links to {GITHUB_REPO_URL} or {SITE_URL}.")
    return 0.0


def _score_image_alt(content: str, images_missing_alt: list, recommendations: list[str]) -> float:
    """Score image alt text presence (10 pts weight)."""
    images = _find_images(content)
    if not images:
        recommendations.append("Consider adding images with descriptive alt text.")
        return 0.5
    elif images_missing_alt:
        recommendations.append(
            f"{len(images_missing_alt)} image(s) missing alt text. "
            "Add descriptive alt attributes for accessibility and SEO."
        )
        return max(0.0, 1.0 - len(images_missing_alt) / len(images))
    return 1.0


def _score_word_count(word_count: int, recommendations: list[str]) -> float:
    """Score word count (10 pts weight)."""
    if word_count >= IDEAL_WORD_COUNT:
        return 1.0
    elif word_count >= MIN_WORD_COUNT:
        recommendations.append(f"Word count is {word_count} — aim for {IDEAL_WORD_COUNT}+ for better search ranking.")
        return word_count / IDEAL_WORD_COUNT
    recommendations.append(
        f"Content is too short ({word_count} words). Minimum {MIN_WORD_COUNT}, ideal {IDEAL_WORD_COUNT}+."
    )
    return max(0.0, word_count / MIN_WORD_COUNT * 0.5)


def _score_readability(content: str, recommendations: list[str]) -> float:
    """Score readability via Flesch Reading Ease (10 pts weight)."""
    fre = _flesch_reading_ease(content)
    if 50 <= fre <= 80:
        return 1.0
    elif 30 <= fre < 50:
        return 0.7
    elif fre > 80:
        return 0.85
    recommendations.append(
        f"Readability score is {fre:.0f} (Flesch) — simplify sentences for a general audience (aim for 50-70)."
    )
    return max(0.0, fre / 50)


def analyze_seo_score(post: dict[str, Any]) -> dict[str, Any]:
    """Score SEO quality on a 0-100 scale with per-factor breakdown.

    Args:
        post: Post dict, ideally already run through ``optimize_post``.

    Returns:
        Dict with ``score`` (int 0-100), ``factors`` (per-factor scores),
        and ``recommendations`` (list of actionable strings).
    """
    seo: dict[str, Any] = post.get("seo", {})
    content: str = post.get("content", "")
    title = seo.get("title", post.get("title", ""))
    meta_desc = seo.get("meta_description", post.get("description", ""))
    keywords = seo.get("keywords", [])
    primary_keyword = seo.get("primary_keyword", keywords[0] if keywords else "")
    heading_issues = seo.get("heading_issues", [])
    images_missing_alt = seo.get("images_missing_alt", [])
    internal_link_count = seo.get("internal_link_count", 0)
    word_count = seo.get("word_count", _word_count(content))

    recommendations: list[str] = []

    factors: dict[str, dict[str, Any]] = {
        "title_length": {"score": round(_score_title(title, primary_keyword, recommendations), 2), "weight": 15},
        "meta_description": {
            "score": round(_score_meta_description(meta_desc, primary_keyword, recommendations), 2),
            "weight": 15,
        },
        "keyword_density": {
            "score": round(_score_keyword_density(content, primary_keyword, recommendations), 2),
            "weight": 15,
        },
        "heading_structure": {
            "score": round(_score_headings(content, heading_issues, recommendations), 2),
            "weight": 15,
        },
        "internal_links": {
            "score": round(_score_internal_links(internal_link_count, recommendations), 2),
            "weight": 10,
        },
        "image_alt": {"score": round(_score_image_alt(content, images_missing_alt, recommendations), 2), "weight": 10},
        "word_count": {"score": round(_score_word_count(word_count, recommendations), 2), "weight": 10},
        "readability": {"score": round(_score_readability(content, recommendations), 2), "weight": 10},
    }

    total = sum(f["score"] * f["weight"] for f in factors.values())
    score = round(total)

    result: dict[str, Any] = {
        "score": score,
        "grade": _score_grade(score),
        "factors": factors,
        "recommendations": recommendations,
    }

    logger.info("SEO score: %d/100 (%s) — %d recommendations", score, result["grade"], len(recommendations))
    return result


def generate_sitemap_entry(post: dict[str, Any]) -> str:
    """Generate an XML ``<url>`` sitemap entry for a single post.

    Args:
        post: Post dict, ideally already run through ``optimize_post``.

    Returns:
        XML string for inclusion in a sitemap.xml ``<urlset>``.
    """
    seo: dict[str, Any] = post.get("seo", {})
    slug = seo.get("slug", post.get("slug", ""))
    canonical_url = seo.get("canonical_url", f"{SITE_URL}/blog/{slug}")
    date = post.get("date_modified", post.get("date", ""))

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elif hasattr(date, "strftime"):
        date = date.strftime("%Y-%m-%d")
    else:
        # Ensure date string is just YYYY-MM-DD
        date = str(date)[:10]

    images = _find_images(post.get("content", ""))

    lines: list[str] = [
        "  <url>",
        f"    <loc>{_xml_escape(canonical_url)}</loc>",
        f"    <lastmod>{date}</lastmod>",
        "    <changefreq>monthly</changefreq>",
        "    <priority>0.7</priority>",
    ]

    # Image sitemap extension (Google)
    for img in images:
        src = img["src"]
        if not src.startswith("http"):
            src = f"{SITE_URL}/{src.lstrip('/')}"
        lines.append("    <image:image>")
        lines.append(f"      <image:loc>{_xml_escape(src)}</image:loc>")
        if img["alt"]:
            lines.append(f"      <image:caption>{_xml_escape(img['alt'])}</image:caption>")
        lines.append("    </image:image>")

    lines.append("  </url>")

    entry = "\n".join(lines)
    logger.debug("Generated sitemap entry for: %s", canonical_url)
    return entry


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _score_grade(score: int) -> str:
    """Map numeric score to letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    return "F"


def _yaml_escape(text: str) -> str:
    """Escape special characters for YAML string values."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
