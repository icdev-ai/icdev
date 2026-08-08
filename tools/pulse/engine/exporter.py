"""ICDEV™ Pulse content exporter.

Exports blog posts as:
- MDX with frontmatter (Hugo/Jekyll/Next.js compatible)
- Standalone HTML with embedded styles and JSON-LD schema
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import markdown

from tools.pulse.config import DRAFTS_DIR, EXPORTS_DIR, PUBLISHED_DIR, SITE_URL, GA_MEASUREMENT_ID
from tools.pulse.db import get_row, update_row

logger = get_logger(__name__)

# Ensure image export directory exists
IMAGES_DIR = EXPORTS_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def export_mdx(post_id: str) -> Path:
    """Export a post as MDX with YAML frontmatter.

    Returns the path to the exported file.
    """
    post = get_row("posts", post_id)
    if not post:
        raise ValueError(f"Post not found: {post_id}")

    frontmatter = post.get("frontmatter", "")
    if not frontmatter:
        frontmatter = _generate_default_frontmatter(post)

    body = post.get("body_markdown", "")

    # Resolve placeholders in markdown body
    # Hero image
    hero_path = post.get("hero_image_path", "")
    og_image = post.get("og_image_url", "")
    if "[HERO_IMAGE]" in body:
        if hero_path or og_image:
            img_src = og_image or hero_path
            alt = post.get("title", "Blog post hero image")
            body = body.replace("[HERO_IMAGE]", f"![{alt}]({img_src})")
        else:
            body = body.replace("[HERO_IMAGE]", "")

    # Source references
    body = re.sub(
        r"\[SOURCE_REF:(.*?)\]",
        lambda m: f"[source]({m.group(1)})",
        body,
    )

    # Video embeds — remove in MDX (videos handled in separate section)
    body = re.sub(r"\[VIDEO_EMBED:[^\]]*\]", "", body)

    # Strip any remaining placeholder patterns
    body = re.sub(r"\[HERO_IMAGE\]", "", body)
    body = re.sub(r"\[SOURCE_REF:[^\]]*\]", "", body)

    # Build MDX content
    content = f"---\n{frontmatter}\n---\n\n{body}\n"

    # Determine output directory based on status
    if post["status"] == "published":
        out_dir = PUBLISHED_DIR
    else:
        out_dir = DRAFTS_DIR

    slug = post["slug"]
    out_path = out_dir / f"{slug}.mdx"
    out_path.write_text(content, encoding="utf-8", newline="")

    logger.info("Exported MDX: %s", out_path)
    return out_path


def _parse_json_field(value: str | list | dict | None, default=None):
    """Safely parse a JSON string field, returning default on failure."""
    if default is None:
        default = {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def _resolve_video_placeholders(body_html: str, videos: list) -> str:
    """Replace [VIDEO_EMBED:topic] placeholders with actual embed HTML."""
    for video in videos:
        # Try multiple key names — video_finder returns source_pain_point
        topic = video.get("source_pain_point", "") or video.get("topic", "") or video.get("title", "")
        placeholder = f"[VIDEO_EMBED:{topic}]"
        embed_html = video.get("embed_html", "")
        if placeholder in body_html and embed_html:
            body_html = body_html.replace(placeholder, embed_html)
    return body_html


def _resolve_hero_image(body_html: str, hero_path: str, og_image: str, alt: str) -> str:
    """Replace [HERO_IMAGE] or [HERO_IMAGE:desc] placeholder with an img tag or remove it."""
    hero_re = re.compile(r"\[HERO_IMAGE(?::[^\]]*)?\]")
    if not hero_re.search(body_html):
        return body_html
    if hero_path or og_image:
        img_src = og_image or hero_path
        img_tag = f'<img src="{img_src}" alt="{alt}" class="hero-image" loading="eager" />'
        return hero_re.sub(img_tag, body_html)
    return hero_re.sub("", body_html)


def _resolve_source_refs(body_html: str, sources: list) -> str:
    """Replace [SOURCE_REF:url] placeholders with anchor tags."""
    for source in sources:
        url = source if isinstance(source, str) else source.get("url", "")
        if url:
            placeholder_pattern = re.compile(re.escape(f"[SOURCE_REF:{url}]"))
            link = f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
            body_html = placeholder_pattern.sub(link, body_html)
    return body_html


def _clean_remaining_placeholders(body_html: str) -> str:
    """Remove any unresolved placeholders from HTML body."""
    body_html = re.sub(r"\[VIDEO_EMBED:[^\]]*\]", "", body_html)
    body_html = re.sub(r"\[SOURCE_REF:[^\]]*\]", "", body_html)
    body_html = re.sub(r"\[HERO_IMAGE(?::[^\]]*)?\]", "", body_html)
    return body_html


def _build_ga_snippet(measurement_id: str) -> str:
    """Build Google Analytics script snippet."""
    if not measurement_id:
        return ""
    return f"""
    <!-- Google Analytics -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={measurement_id}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{measurement_id}');
    </script>"""


def _build_html_page(
    title: str,
    seo_desc: str,
    seo_keywords: str,
    canonical: str,
    og_image: str,
    schema: dict,
    ga_snippet: str,
    body_html: str,
) -> str:
    """Assemble the full HTML page from components."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | ICDEV™ Blog</title>
    <meta name="description" content="{seo_desc}">
    <meta name="keywords" content="{seo_keywords}">
    <link rel="canonical" href="{canonical}">

    <!-- Open Graph -->
    <meta property="og:type" content="article">
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{seo_desc}">
    <meta property="og:url" content="{canonical}">
    <meta property="og:site_name" content="ICDEV™ Blog">
    {f'<meta property="og:image" content="{og_image}">' if og_image else ""}

    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{seo_desc}">
    {f'<meta name="twitter:image" content="{og_image}">' if og_image else ""}

    <!-- JSON-LD Schema -->
    <script type="application/ld+json">
    {json.dumps(schema, indent=2)}
    </script>
    {ga_snippet}

    <style>
        :root {{
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --text: #1f2937;
            --text-light: #6b7280;
            --bg: #ffffff;
            --bg-alt: #f9fafb;
            --border: #e5e7eb;
            --code-bg: #1e293b;
            --max-width: 780px;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: var(--text);
            background: var(--bg);
            line-height: 1.8;
            font-size: 18px;
        }}
        .container {{
            max-width: var(--max-width);
            margin: 0 auto;
            padding: 2rem 1.5rem;
        }}
        .hero-image {{
            width: 100%;
            height: auto;
            border-radius: 12px;
            margin-bottom: 2rem;
        }}
        h1 {{
            font-size: 2.5rem;
            line-height: 1.2;
            margin-bottom: 1rem;
            color: var(--text);
        }}
        h2 {{
            font-size: 1.75rem;
            margin-top: 2.5rem;
            margin-bottom: 1rem;
            color: var(--text);
            border-bottom: 2px solid var(--border);
            padding-bottom: 0.5rem;
        }}
        h3 {{
            font-size: 1.35rem;
            margin-top: 2rem;
            margin-bottom: 0.75rem;
        }}
        p {{ margin-bottom: 1.25rem; }}
        a {{
            color: var(--primary);
            text-decoration: none;
        }}
        a:hover {{ text-decoration: underline; }}
        blockquote {{
            border-left: 4px solid var(--primary);
            padding: 1rem 1.5rem;
            margin: 1.5rem 0;
            background: var(--bg-alt);
            border-radius: 0 8px 8px 0;
            font-style: italic;
        }}
        code {{
            background: var(--bg-alt);
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-size: 0.9em;
        }}
        pre {{
            background: var(--code-bg);
            color: #e2e8f0;
            padding: 1.25rem;
            border-radius: 8px;
            overflow-x: auto;
            margin: 1.5rem 0;
        }}
        pre code {{
            background: none;
            padding: 0;
            color: inherit;
        }}
        .tldr {{
            background: linear-gradient(135deg, #eff6ff, #dbeafe);
            border: 1px solid #93c5fd;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }}
        .tldr h2 {{
            border: none;
            margin-top: 0;
            font-size: 1.25rem;
            color: var(--primary-dark);
        }}
        .meta {{
            color: var(--text-light);
            font-size: 0.9rem;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
        }}
        .cta {{
            background: var(--primary);
            color: white;
            padding: 1.5rem;
            border-radius: 12px;
            text-align: center;
            margin: 2rem 0;
        }}
        .cta a {{ color: white; font-weight: 600; }}
        .video-embed {{
            position: relative;
            padding-bottom: 56.25%;
            height: 0;
            overflow: hidden;
            margin: 1.5rem 0;
            border-radius: 8px;
        }}
        .video-embed iframe {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            border: none;
        }}
        ul, ol {{
            margin-bottom: 1.25rem;
            padding-left: 1.5rem;
        }}
        li {{ margin-bottom: 0.5rem; }}
        @media (max-width: 768px) {{
            body {{ font-size: 16px; }}
            h1 {{ font-size: 1.75rem; }}
            .container {{ padding: 1rem; }}
        }}
    </style>
</head>
<body>
    <article class="container">
        {body_html}
    </article>
</body>
</html>"""


def export_html(post_id: str) -> Path:
    """Export a post as standalone HTML with styles and JSON-LD.

    Returns the path to the exported file.
    """
    post = get_row("posts", post_id)
    if not post:
        raise ValueError(f"Post not found: {post_id}")

    body_md = post.get("body_markdown", "")
    body_html = markdown.markdown(
        body_md,
        extensions=["extra", "codehilite", "toc", "meta", "smarty"],
    )

    schema = _parse_json_field(post.get("schema_json", "{}"), default={})
    videos = _parse_json_field(post.get("video_embeds", "[]"), default=[])
    sources = _parse_json_field(post.get("source_urls", "[]"), default=[])

    hero_path = post.get("hero_image_path", "")
    og_image = post.get("og_image_url", "")
    alt = post.get("title", "Blog post hero image")

    body_html = _resolve_video_placeholders(body_html, videos)
    body_html = _resolve_hero_image(body_html, hero_path, og_image, alt)
    body_html = _resolve_source_refs(body_html, sources)
    body_html = _clean_remaining_placeholders(body_html)

    title = post.get("title", "Untitled")
    seo_desc = post.get("seo_description", "")
    seo_keywords = post.get("seo_keywords", "")
    slug = post["slug"]
    canonical = f"{SITE_URL}/blog/{slug}"

    ga_snippet = _build_ga_snippet(GA_MEASUREMENT_ID)
    html = _build_html_page(title, seo_desc, seo_keywords, canonical, og_image, schema, ga_snippet, body_html)

    out_path = EXPORTS_DIR / f"{slug}.html"
    out_path.write_text(html, encoding="utf-8", newline="")

    update_row("posts", post_id, {"body_html": body_html})

    logger.info("Exported HTML: %s", out_path)
    return out_path


def export_both(post_id: str) -> dict:
    """Export as both MDX and HTML.

    Returns dict with paths.
    """
    mdx_path = export_mdx(post_id)
    html_path = export_html(post_id)
    return {
        "mdx": str(mdx_path),
        "html": str(html_path),
        "post_id": post_id,
    }


def _generate_default_frontmatter(post: dict) -> str:
    """Generate YAML frontmatter from post data."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    published = post.get("published_at", now)
    keywords = post.get("seo_keywords", "")

    # Parse tags from keywords (may be JSON array or comma-separated string)
    tags = []
    if keywords:
        try:
            parsed = json.loads(keywords)
            if isinstance(parsed, list):
                tags = [str(k).strip() for k in parsed if str(k).strip()]
        except (json.JSONDecodeError, TypeError):
            tags = [k.strip() for k in keywords.split(",") if k.strip()]
    tags_yaml = "\n".join([f"  - {t}" for t in tags[:8]])

    return f"""title: "{post.get("title", "Untitled")}"
slug: "{post.get("slug", "")}"
date: "{published}"
lastmod: "{now}"
draft: {str(post.get("status", "draft") != "published").lower()}
author: "ICDEV™ Team"
description: "{post.get("seo_description", "")}"
seo_title: "{post.get("seo_title", post.get("title", ""))}"
keywords: "{", ".join(tags[:8]) if tags else keywords}"
tags:
{tags_yaml}
image: "{post.get("og_image_url", "")}"
canonical: "{SITE_URL}/blog/{post.get("slug", "")}"
"""
