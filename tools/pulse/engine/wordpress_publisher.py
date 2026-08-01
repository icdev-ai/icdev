# CUI // SP-CTI
"""WordPress publisher for Pulse blog posts.

Publishes Pulse posts to WordPress (icdev.ai) via XML-RPC API.
No browser automation needed — pure API calls.

Architecture:
- Uses Python stdlib xmlrpc.client (zero external deps, air-gap safe)
- Credentials from .env (WP_XMLRPC_URL, WP_USERNAME, WP_PASSWORD)
- Maps Pulse post fields to WordPress post fields
- Supports publish, draft, update, and delete operations

Usage:
  # Publish a specific post
  python tools/pulse/engine/wordpress_publisher.py --publish --post-id "post-abc123"

  # Publish all approved posts
  python tools/pulse/engine/wordpress_publisher.py --publish --all-approved

  # Test connection
  python tools/pulse/engine/wordpress_publisher.py --test

  # List recent WP posts
  python tools/pulse/engine/wordpress_publisher.py --list-wp

  # JSON output
  python tools/pulse/engine/wordpress_publisher.py --publish --post-id "post-abc" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xmlrpc.client  # nosec B411 — internal publisher talks to our own WordPress site
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if not already loaded
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists() and not os.getenv("WP_PASSWORD"):
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from tools.pulse.db import get_row, query_rows, update_row  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WP_XMLRPC_URL = os.getenv("WP_XMLRPC_URL", "https://icdev.ai/xmlrpc.php")
WP_USERNAME = os.getenv("WP_USERNAME", "pulse-bot")
WP_PASSWORD = os.getenv("WP_PASSWORD", "")
WP_BLOG_ID = os.getenv("WP_BLOG_ID", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def _get_client() -> xmlrpc.client.ServerProxy:
    """Create XML-RPC client."""
    return xmlrpc.client.ServerProxy(WP_XMLRPC_URL)


def _upload_image(wp, image_path: str, post_title: str = "") -> dict | None:
    """Upload a local image to WordPress media library.

    Args:
        wp: XML-RPC client.
        image_path: Local path to the image file.
        post_title: Used for alt text / title.

    Returns:
        dict with 'id' and 'url' on success, None on failure.
    """
    img_path = Path(image_path)
    if not img_path.exists():
        _log(f"Image not found: {image_path}", "WARN")
        return None

    # Determine MIME type from extension
    ext = img_path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }
    mime_type = mime_map.get(ext, "image/png")

    filename = img_path.name
    img_data = img_path.read_bytes()

    media = {
        "name": filename,
        "type": mime_type,
        "bits": xmlrpc.client.Binary(img_data),
    }

    try:
        result = wp.wp.uploadFile(WP_BLOG_ID, WP_USERNAME, WP_PASSWORD, media)
        attachment_id = result.get("id") or result.get("attachment_id")
        url = result.get("url", "")
        _log(f"Uploaded image: {filename} -> WP attachment {attachment_id}")
        return {"id": str(attachment_id), "url": url}
    except xmlrpc.client.Fault as e:
        _log(f"Image upload failed: {e.faultString}", "ERROR")
        return None
    except Exception as e:
        _log(f"Image upload failed: {e}", "ERROR")
        return None


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------


def test_connection() -> dict:
    """Test WordPress XML-RPC connection and credentials."""
    if not WP_PASSWORD:
        return {"status": "error", "message": "WP_PASSWORD not set in .env"}

    wp = _get_client()
    try:
        blogs = wp.wp.getUsersBlogs(WP_USERNAME, WP_PASSWORD)
        blog = blogs[0] if blogs else {}
        return {
            "status": "ok",
            "message": "Connected to WordPress",
            "blog_name": blog.get("blogName", ""),
            "blog_url": blog.get("url", ""),
            "is_admin": blog.get("isAdmin", False),
            "xmlrpc_url": WP_XMLRPC_URL,
        }
    except xmlrpc.client.Fault as e:
        return {"status": "error", "message": f"Auth failed: {e.faultString}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Pulse post -> WordPress post mapping
# ---------------------------------------------------------------------------


def _strip_hero_image_from_content(content: str) -> str:
    """Remove inline hero image from post content.

    WordPress themes display the featured image (post_thumbnail) automatically,
    so including it inline in the body causes it to appear twice.
    """
    import re

    # Remove markdown hero images: ![Hero](...) — greedy match to handle URLs with parens/query strings
    content = re.sub(r"!\[Hero\]\(.*?\)\s*", "", content)
    # Remove <p> wrapped hero images: <p><img ... alt="Hero" ... /></p>
    content = re.sub(r'<p>\s*<img[^>]*alt="Hero"[^>]*/?\s*>\s*</p>\s*', "", content)
    # Remove standalone HTML hero images: <img ... alt="Hero" ... />
    content = re.sub(r'<img[^>]*alt="Hero"[^>]*/?\s*>\s*', "", content)
    # Remove HTML hero images with class: <img ... class="hero-image" ... />
    content = re.sub(r'<img[^>]*class="hero-image"[^>]*/?\s*>\s*', "", content)
    # Remove leftover [HERO_IMAGE] placeholders
    content = re.sub(r"\[HERO_IMAGE(?::[^\]]*)?\]\s*", "", content)
    return content


def _resolve_local_images(content: str, wp) -> str:
    """Find local image paths in HTML/markdown content and upload to WordPress.

    Catches patterns like:
      src="/static/screenshots/foo.png"
      src="tools/dashboard/static/screenshots/foo.png"
      ![alt](/static/screenshots/foo.png)

    Uploads each local file to WP media library and replaces the path with the
    returned public URL.  If the file doesn't exist or upload fails, the
    reference is left unchanged (better than silently dropping the image).
    """
    import re

    # Directories to search for local images (most-specific first)
    search_dirs = [
        PROJECT_ROOT / "tools" / "dashboard" / "static" / "screenshots",
        PROJECT_ROOT / "tools" / "dashboard" / "static",
        PROJECT_ROOT / "playwright" / "screenshots",
        PROJECT_ROOT,
    ]

    # Match src="..." or markdown ![...](...) with local-looking paths
    # Local = starts with / (but not //) or doesn't start with http
    local_img_re = re.compile(
        r"(?:"
        r'(?:src=["\'])(/static/screenshots/[^"\']+)["\']'  # HTML src="/static/..."
        r"|"
        r"!\[[^\]]*\]\((/static/screenshots/[^)]+)\)"  # Markdown ![](/static/...)
        r"|"
        r'(?:src=["\'])((?:tools/|playwright/)[^"\']+\.(?:png|jpg|jpeg|webp|gif))["\']'  # HTML src="playwright/..."
        r"|"
        r"!\[[^\]]*\]\(((?:tools/|playwright/)[^)]+\.(?:png|jpg|jpeg|webp|gif))\)"  # Markdown ![](playwright/...)
        r"|"
        r"!\[[^\]]*\]\(((?:[A-Z]:\\|/)[^)]+\.(?:png|jpg|jpeg|webp|gif))\)"  # Markdown ![](C:\abs\path.png)
        r")"
    )

    replacements = {}
    for m in local_img_re.finditer(content):
        local_path = m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5)
        if local_path in replacements:
            continue  # Already processed

        # Resolve to absolute filesystem path
        resolved = None
        # Check if it's already an absolute path
        abs_candidate = Path(local_path)
        if abs_candidate.is_absolute() and abs_candidate.exists():
            resolved = abs_candidate
        else:
            # Strip leading slash for filesystem lookup
            clean = local_path.lstrip("/")
            for search_dir in search_dirs:
                candidate = search_dir / clean
                if candidate.exists():
                    resolved = candidate
                    break
                # Also try just the filename
                candidate = search_dir / Path(clean).name
                if candidate.exists():
                    resolved = candidate
                    break

        if not resolved:
            _log(f"Local image not found on disk: {local_path}", "WARN")
            continue

        result = _upload_image(wp, str(resolved), resolved.stem)
        if result and result.get("url"):
            replacements[local_path] = result["url"]
            _log(f"Uploaded inline image: {local_path} -> {result['url']}")
        else:
            _log(f"Failed to upload inline image: {local_path}", "WARN")

    for old_path, new_url in replacements.items():
        content = content.replace(old_path, new_url)

    return content


def _pulse_to_wp(post: dict, status: str = "publish") -> dict:
    """Map Pulse post fields to WordPress XML-RPC post struct."""
    content = post.get("body_html") or post.get("body_markdown", "")
    content = _strip_hero_image_from_content(content)
    wp_post = {
        "post_type": "post",
        "post_status": status,
        "post_title": post.get("title", "Untitled"),
        "post_content": content,
    }

    # SEO fields via Yoast/AIOSEO custom fields (if plugin installed)
    custom_fields = []
    if post.get("seo_title"):
        custom_fields.append({"key": "_yoast_wpseo_title", "value": post["seo_title"]})
        custom_fields.append({"key": "_aioseo_title", "value": post["seo_title"]})
    if post.get("seo_description"):
        custom_fields.append({"key": "_yoast_wpseo_metadesc", "value": post["seo_description"]})
        custom_fields.append({"key": "_aioseo_description", "value": post["seo_description"]})
    if post.get("seo_keywords"):
        custom_fields.append({"key": "_yoast_wpseo_focuskw", "value": post["seo_keywords"]})

    # Pulse metadata
    custom_fields.append({"key": "_pulse_post_id", "value": post.get("id", "")})
    custom_fields.append({"key": "_pulse_published_at", "value": _now_iso()})

    if post.get("tldr"):
        custom_fields.append({"key": "_pulse_tldr", "value": post["tldr"]})

    if custom_fields:
        wp_post["custom_fields"] = custom_fields

    # Categories/tags from topic
    if post.get("topic"):
        wp_post["terms_names"] = {
            "category": [post["topic"]],
        }

    return wp_post


# ---------------------------------------------------------------------------
# Publish operations
# ---------------------------------------------------------------------------


def publish_post(post_id: str, as_draft: bool = False, force: bool = False) -> dict:
    """Publish a single Pulse post to WordPress.

    Gated on the LLM-judge verdict (nav-intel-09, "block red"): a RED verdict —
    or no verdict at all — refuses the publish unless ``force=True`` (the caller
    has already recorded an audited admin override). Fail-closed.
    """
    # 1. Fetch post from Pulse DB
    post = get_row("pulse_posts", post_id)
    if not post:
        return {"status": "error", "message": f"Post not found: {post_id}"}

    if post.get("status") not in ("approved", "published"):
        return {
            "status": "error",
            "message": f"Post status is '{post.get('status')}' — must be 'approved' or 'published'.",
        }

    # Judge-verdict publish gate (fail-closed; bypassed only by an audited
    # force). Evaluated before the credential check so a RED verdict is refused
    # regardless of whether WP creds happen to be configured.
    if not force:
        from tools.pulse.publish_gate import evaluate_publish_gate

        gate = evaluate_publish_gate(post)
        if gate["blocked"]:
            _log(f"Publish blocked for {post_id}: {gate['reason']}", level="WARN")
            return {
                "status": "blocked",
                "blocked": True,
                "post_id": post_id,
                "verdict": gate["verdict"],
                "reason": gate["reason"],
                "message": gate["reason"],
            }

    if not WP_PASSWORD:
        return {"status": "error", "message": "WP_PASSWORD not set in .env"}

    _log(f"Publishing: {post.get('title', 'Untitled')}")

    # 2. Check if already published to WP (has wp_post_id)
    wp_post_id = post.get("wp_post_id")

    wp = _get_client()
    wp_status = "draft" if as_draft else "publish"
    wp_post = _pulse_to_wp(post, status=wp_status)

    # 2a. Resolve any local image paths in post_content → upload to WP
    wp_post["post_content"] = _resolve_local_images(wp_post["post_content"], wp)

    # 2b. Upload hero image if available
    image_attachment = None
    hero_path = post.get("hero_image_path") or post.get("og_image_url", "")
    if hero_path and Path(hero_path).exists() and not hero_path.endswith(".svg"):
        _log(f"Uploading hero image: {hero_path}")
        image_attachment = _upload_image(wp, hero_path, post.get("title", ""))
        if image_attachment and image_attachment.get("id"):
            wp_post["post_thumbnail"] = image_attachment["id"]

    try:
        if wp_post_id:
            # Update existing WP post
            success = wp.wp.editPost(WP_BLOG_ID, WP_USERNAME, WP_PASSWORD, wp_post_id, wp_post)
            if success:
                _log(f"Updated WP post {wp_post_id}")
            else:
                return {"status": "error", "message": f"Failed to update WP post {wp_post_id}"}
        else:
            # Create new WP post
            wp_post_id = wp.wp.newPost(WP_BLOG_ID, WP_USERNAME, WP_PASSWORD, wp_post)
            _log(f"Created WP post {wp_post_id}")

        # 3. Update Pulse DB with WP post ID and status
        update_fields = {
            "wp_post_id": str(wp_post_id),
            "status": "published",
            "published_at": _now_iso(),
        }
        if image_attachment and image_attachment.get("url"):
            update_fields["og_image_url"] = image_attachment["url"]
        update_row("pulse_posts", post_id, update_fields)

        # 4. Get the published URL
        wp_post_data = wp.wp.getPost(WP_BLOG_ID, WP_USERNAME, WP_PASSWORD, wp_post_id, ["link"])
        post_url = wp_post_data.get("link", f"https://icdev.ai/?p={wp_post_id}")

        return {
            "status": "ok",
            "post_id": post_id,
            "wp_post_id": str(wp_post_id),
            "title": post.get("title"),
            "url": post_url,
            "wp_status": wp_status,
            "published_at": _now_iso(),
            "image_uploaded": bool(image_attachment),
            "image_url": image_attachment.get("url") if image_attachment else None,
            "message": f"Published to WordPress: {post.get('title')}",
        }

    except xmlrpc.client.Fault as e:
        return {"status": "error", "message": f"WordPress error: {e.faultString}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def publish_all_approved(as_draft: bool = False) -> dict:
    """Publish all approved Pulse posts to WordPress (batch / scheduler auto-publish).

    Judge-verdict gated (nav-intel-09, "block red"): posts whose latest verdict
    is RED — or that have no verdict on record — are skipped (never force-pushed)
    and logged; only cleared posts are published.
    """
    from tools.pulse.publish_gate import evaluate_publish_gate

    posts = query_rows("pulse_posts", where="status = ?", params=("approved",))
    if not posts:
        return {"status": "ok", "message": "No approved posts to publish.", "count": 0}

    results = []
    skipped = []
    for post in posts:
        gate = evaluate_publish_gate(post)
        if gate["blocked"]:
            _log(
                f"Auto-publish skipped for {post['id']} "
                f"(verdict={gate['verdict'] or 'absent'}): {gate['reason']}",
                level="WARN",
            )
            skipped.append(
                {
                    "post_id": post["id"],
                    "status": "skipped",
                    "verdict": gate["verdict"],
                    "reason": gate["reason"],
                }
            )
            continue
        result = publish_post(post["id"], as_draft=as_draft)
        results.append(result)

    succeeded = sum(1 for r in results if r.get("status") == "ok")
    failed = len(results) - succeeded

    return {
        "status": "ok" if failed == 0 else "partial",
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": len(skipped),
        "skipped_details": skipped,
        "results": results,
    }


# ---------------------------------------------------------------------------
# List WP posts
# ---------------------------------------------------------------------------


def list_wp_posts(count: int = 10) -> dict:
    """List recent WordPress posts."""
    if not WP_PASSWORD:
        return {"status": "error", "message": "WP_PASSWORD not set in .env"}

    wp = _get_client()
    try:
        posts = wp.wp.getPosts(
            WP_BLOG_ID,
            WP_USERNAME,
            WP_PASSWORD,
            {"number": count, "post_type": "post"},
            ["post_id", "post_title", "post_status", "post_date", "link"],
        )
        return {
            "status": "ok",
            "count": len(posts),
            "posts": [
                {
                    "wp_id": p.get("post_id"),
                    "title": p.get("post_title"),
                    "status": p.get("post_status"),
                    "date": str(p.get("post_date", "")),
                    "url": p.get("link"),
                }
                for p in posts
            ],
        }
    except xmlrpc.client.Fault as e:
        return {"status": "error", "message": f"WordPress error: {e.faultString}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="WordPress publisher for Pulse posts")
    parser.add_argument("--test", action="store_true", help="Test WP connection")
    parser.add_argument("--publish", action="store_true", help="Publish post(s)")
    parser.add_argument("--post-id", help="Specific post ID to publish")
    parser.add_argument("--all-approved", action="store_true", help="Publish all approved posts")
    parser.add_argument("--as-draft", action="store_true", help="Publish as draft instead of live")
    parser.add_argument("--list-wp", action="store_true", help="List recent WP posts")
    parser.add_argument("--count", type=int, default=10, help="Number of posts to list")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    result = {"status": "error", "message": "No action specified"}

    if args.test:
        result = test_connection()
    elif args.list_wp:
        result = list_wp_posts(count=args.count)
    elif args.publish:
        if args.all_approved:
            result = publish_all_approved(as_draft=args.as_draft)
        elif args.post_id:
            result = publish_post(args.post_id, as_draft=args.as_draft)
        else:
            result = {"status": "error", "message": "Specify --post-id or --all-approved"}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = result.get("status", "unknown")
        msg = result.get("message", "")
        print(f"[{status.upper()}] {msg}")
        if result.get("url"):
            print(f"  URL: {result['url']}")


if __name__ == "__main__":
    main()
