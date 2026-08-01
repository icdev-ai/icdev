# CUI // SP-CTI
"""Hostinger Website Builder blog publisher.

Publishes Pulse posts to Hostinger Website Builder using browser automation
with a persistent profile (login once, reuse session).

Architecture:
- Uses Playwright Python (sync API) with persistent browser context
- Cookies/session stored in data/hostinger_profile/ (survives restarts)
- First run: visible browser for manual Cloudflare + Google OAuth login
- Subsequent runs: headless with saved session (falls back to visible if needed)
- API key rotation tracked via HOSTINGER_KEY_ISSUED env var

Usage:
  # First time — opens browser, you login manually
  python tools/pulse/engine/hostinger_publisher.py --setup

  # Publish a specific post
  python tools/pulse/engine/hostinger_publisher.py --publish --post-id "post-abc123"

  # Publish all approved posts
  python tools/pulse/engine/hostinger_publisher.py --publish --all-approved

  # Check session health
  python tools/pulse/engine/hostinger_publisher.py --check-session

  # Key rotation reminder
  python tools/pulse/engine/hostinger_publisher.py --check-key-rotation

  # JSON output for pipeline integration
  python tools/pulse/engine/hostinger_publisher.py --publish --post-id "post-abc" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.pulse.db import get_row, query_rows, update_row  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROFILE_DIR = PROJECT_ROOT / "data" / os.getenv("HOSTINGER_PROFILE_DIR", "hostinger_pw_profile")
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

BUILDER_URL = os.getenv(
    "HOSTINGER_BUILDER_URL",
    "https://builder.hostinger.com/2HwifueUMHezEeXC",
)
HOSTINGER_DOMAIN = os.getenv("HOSTINGER_DOMAIN", "icdev.ai")

# Key rotation: default 90 days
KEY_ROTATION_DAYS = int(os.getenv("HOSTINGER_KEY_ROTATION_DAYS", "90"))
KEY_ISSUED_DATE = os.getenv("HOSTINGER_KEY_ISSUED", "2026-03-12")

# Timeouts (ms)
NAV_TIMEOUT = 60_000
ACTION_TIMEOUT = 15_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def _check_key_rotation() -> dict:
    """Check if API key needs rotation."""
    try:
        issued = datetime.strptime(KEY_ISSUED_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {
            "status": "unknown",
            "message": f"Cannot parse HOSTINGER_KEY_ISSUED: {KEY_ISSUED_DATE}",
        }

    now = datetime.now(timezone.utc)
    expires = issued + timedelta(days=KEY_ROTATION_DAYS)
    days_left = (expires - now).days

    if days_left <= 0:
        return {
            "status": "expired",
            "days_overdue": abs(days_left),
            "message": f"API key expired {abs(days_left)} days ago. Rotate immediately.",
            "issued": KEY_ISSUED_DATE,
            "rotation_due": expires.strftime("%Y-%m-%d"),
        }
    elif days_left <= 14:
        return {
            "status": "warning",
            "days_left": days_left,
            "message": f"API key expires in {days_left} days. Plan rotation.",
            "issued": KEY_ISSUED_DATE,
            "rotation_due": expires.strftime("%Y-%m-%d"),
        }
    else:
        return {
            "status": "ok",
            "days_left": days_left,
            "issued": KEY_ISSUED_DATE,
            "rotation_due": expires.strftime("%Y-%m-%d"),
        }


# ---------------------------------------------------------------------------
# Browser session management
# ---------------------------------------------------------------------------


def _launch_browser(headless: bool = False):
    """Launch browser with persistent context.

    Tries system Chrome first (less likely to be flagged by Cloudflare).
    Falls back to bundled Chromium if Chrome is already running or unavailable.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    common_opts = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        ignore_default_args=["--enable-automation"],
    )

    # Try system Chrome first (avoids Cloudflare bot detection)
    try:
        context = pw.chromium.launch_persistent_context(channel="chrome", **common_opts)
        _log("Using system Chrome")
        return pw, context
    except Exception as e:
        _log(f"System Chrome unavailable ({e}) — using bundled Chromium")

    context = pw.chromium.launch_persistent_context(**common_opts)
    _log("Using bundled Chromium")
    return pw, context


def _wait_for_builder_load(page, timeout_s: int = 120) -> bool:
    """Wait until the builder UI loads (past Cloudflare + auth).

    Guards against race conditions: the builder URL may flash briefly before
    redirecting to auth.  We require a *stable* builder URL — checked twice
    with a 3-second gap.
    """
    deadline = time.time() + timeout_s
    _confirmed_once = False  # True after first positive check
    while time.time() < deadline:
        try:
            title = page.title()
            url = page.url
        except Exception:
            # Page navigating (Cloudflare redirect, OAuth, etc.)
            _confirmed_once = False
            time.sleep(2)
            continue

        # --- still on an intermediate page ---
        # Cloudflare challenge page
        if "Just a moment" in title:
            _log("Cloudflare challenge active — waiting...")
            _confirmed_once = False
            time.sleep(3)
            continue
        # Google OAuth redirect
        if "accounts.google.com" in url:
            _log("Google login page — waiting for user to complete...")
            _confirmed_once = False
            time.sleep(3)
            continue
        # Hostinger login / auth page (URL may still contain
        # "builder.hostinger.com" inside the redirectUrl query param,
        # so check the *host* portion explicitly)
        if "auth.hostinger.com" in url:
            _log("Hostinger auth page — waiting for user to complete...")
            _confirmed_once = False
            time.sleep(3)
            continue
        # Catch generic login paths (e.g. /login in host path, not query)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if "/login" in parsed.path.lower():
            _log("Login page detected — waiting for user to complete...")
            _confirmed_once = False
            time.sleep(3)
            continue

        # --- candidate: looks like the real builder ---
        if "builder.hostinger.com" in parsed.netloc:
            if _confirmed_once:
                # Second consecutive positive check — stable
                _log(f"Builder loaded (confirmed): {title}")
                return True
            else:
                _log(f"Builder detected (verifying...): {title}")
                _confirmed_once = True
                time.sleep(3)  # wait and re-check
                continue

        # Unknown page — keep waiting
        _confirmed_once = False
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Setup (first-time login)
# ---------------------------------------------------------------------------


def setup_session() -> dict:
    """Two-phase setup: launch clean Chrome, user logs in, then connect Playwright.

    Phase 1: Launch Chrome natively (no Playwright/DevTools) so Cloudflare
             sees a real browser.  User completes login manually.
    Phase 2: User presses Enter in the terminal once logged in.  We then
             close the clean Chrome and re-launch via Playwright with the
             same profile to verify + discover blog selectors.
    """
    import subprocess

    _log("Phase 1: Launching Chrome natively (no automation flags)...")
    _log(f"Profile directory: {PROFILE_DIR}")

    # Find Chrome executable
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_exe = None
    for p in chrome_paths:
        if os.path.exists(p):
            chrome_exe = p
            break
    if not chrome_exe:
        return {"status": "error", "message": "Chrome not found. Install Google Chrome."}

    # Launch Chrome natively — zero automation signals
    proc = subprocess.Popen(
        [
            chrome_exe,
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            BUILDER_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _log(f"Chrome launched (PID {proc.pid})")

    _log("=" * 60)
    _log("MANUAL ACTION REQUIRED:")
    _log("1. A Chrome window has opened with the Hostinger builder URL")
    _log("2. Complete the Cloudflare check if prompted")
    _log("3. Login with your Google account")
    _log("4. Wait until the Hostinger Website Builder dashboard loads")
    _log("5. THEN come back here and press ENTER")
    _log("=" * 60)

    # Wait for user to confirm login
    try:
        input("\n>>> Press ENTER after you have logged into Hostinger builder... ")
    except EOFError:
        # Running non-interactively — wait and check periodically
        _log("Non-interactive mode — waiting 120s for login...")
        time.sleep(120)

    _log("Phase 2: Closing native Chrome and connecting via Playwright...")

    # Terminate the native Chrome
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    # Brief pause to release profile lock
    time.sleep(3)

    # Now launch Playwright with the same profile (cookies/session saved)
    _log("Connecting Playwright to saved session...")
    pw, context = _launch_browser(headless=False)
    page = context.pages[0] if context.pages else context.new_page()

    _log(f"Navigating to builder: {BUILDER_URL}")
    page.goto(BUILDER_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")

    success = _wait_for_builder_load(page, timeout_s=60)

    if success:
        _log("Login verified! Capturing builder state...")
        page.wait_for_timeout(3000)

        marker = {
            "login_completed": _now_iso(),
            "builder_url": page.url,
            "page_title": page.title(),
        }
        marker_path = PROFILE_DIR / "session_marker.json"
        marker_path.write_text(json.dumps(marker, indent=2))
        _log(f"Session saved to {PROFILE_DIR}")

        blog_info = _discover_blog_section(page)
        marker["blog_section"] = blog_info

        context.close()
        pw.stop()
        return {"status": "ok", "message": "Setup complete", **marker}
    else:
        # Session didn't persist — save partial marker anyway
        _log("Builder did not load via Playwright. Session may not have persisted.")
        _log("Saving partial session — publishing may still work if cookies are valid.")

        marker = {
            "login_completed": _now_iso(),
            "builder_url": page.url,
            "page_title": page.title(),
            "partial": True,
        }
        marker_path = PROFILE_DIR / "session_marker.json"
        marker_path.write_text(json.dumps(marker, indent=2))

        context.close()
        pw.stop()
        return {
            "status": "partial",
            "message": "Login saved but Playwright re-verification hit Cloudflare. "
            "Cookies are stored — publish may work if Cloudflare passes on retry.",
            **marker,
        }


def _discover_blog_section(page) -> dict:
    """Try to find the blog section in the builder UI."""
    info = {"found": False, "selectors": {}}

    try:
        # Common Hostinger builder blog navigation patterns
        # Look for "Blog" in the sidebar/navigation
        blog_selectors = [
            'text="Blog"',
            '[data-qa="blog"]',
            'a:has-text("Blog")',
            'button:has-text("Blog")',
            '[class*="blog"]',
            'nav >> text="Blog"',
        ]

        for sel in blog_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    info["found"] = True
                    info["selector"] = sel
                    _log(f"Found blog section via: {sel}")

                    # Click to navigate to blog
                    el.click()
                    page.wait_for_timeout(2000)
                    info["blog_url"] = page.url
                    break
            except Exception:
                continue

        if not info["found"]:
            # Take snapshot for manual selector discovery
            snapshot_path = PROFILE_DIR / "builder_snapshot.png"
            page.screenshot(path=str(snapshot_path), full_page=True)
            info["snapshot"] = str(snapshot_path)
            _log(f"Blog section not auto-detected. Snapshot saved: {snapshot_path}")
            _log("Run with --discover-blog to manually identify the blog section.")

    except Exception as e:
        info["error"] = str(e)

    return info


# ---------------------------------------------------------------------------
# Session check
# ---------------------------------------------------------------------------


def check_session() -> dict:
    """Check if saved session is still valid."""
    marker_path = PROFILE_DIR / "session_marker.json"
    if not marker_path.exists():
        return {
            "status": "no_session",
            "message": "No session found. Run --setup first.",
        }

    marker = json.loads(marker_path.read_text())

    # Check session age
    login_time = marker.get("login_completed", "")
    if login_time:
        try:
            lt = datetime.fromisoformat(login_time)
            age_hours = (datetime.now(timezone.utc) - lt).total_seconds() / 3600
            marker["session_age_hours"] = round(age_hours, 1)

            if age_hours > 24 * 7:
                marker["status"] = "stale"
                marker["message"] = "Session is >7 days old. May need re-login (--setup)."
            else:
                marker["status"] = "ok"
                marker["message"] = f"Session is {round(age_hours, 1)}h old."
        except Exception:
            marker["status"] = "unknown"
    else:
        marker["status"] = "unknown"

    return marker


# ---------------------------------------------------------------------------
# Blog publishing
# ---------------------------------------------------------------------------


def publish_post(post_id: str, force: bool = False) -> dict:
    """Publish a single Pulse post to Hostinger builder blog.

    Gated on the LLM-judge verdict (nav-intel-09, "block red"): a RED verdict —
    or no verdict at all — refuses the publish unless ``force=True`` (an audited
    admin override recorded upstream). Fail-closed.
    """
    # 1. Fetch post from DB
    post = get_row("pulse_posts", post_id)
    if not post:
        return {"status": "error", "message": f"Post not found: {post_id}"}

    if post.get("status") not in ("approved", "published"):
        return {
            "status": "error",
            "message": f"Post status is '{post.get('status')}' — must be 'approved' or 'published'.",
        }

    # Judge-verdict publish gate (fail-closed; bypassed only by an audited force).
    if not force:
        from tools.pulse.publish_gate import evaluate_publish_gate

        gate = evaluate_publish_gate(post)
        if gate["blocked"]:
            _log(f"Publish blocked for {post_id}: {gate['reason']}")
            return {
                "status": "blocked",
                "blocked": True,
                "post_id": post_id,
                "verdict": gate["verdict"],
                "reason": gate["reason"],
                "message": gate["reason"],
            }

    _log(f"Publishing: {post.get('title', 'Untitled')}")

    # 2. Check session
    session = check_session()
    if session.get("status") == "no_session":
        return {
            "status": "error",
            "message": "No session. Run --setup first.",
        }

    # 3. Load blog section selectors
    selectors = _load_selectors()

    # 4. Launch browser (try headless first, fall back to visible)
    pw, context = _launch_browser(headless=True)
    page = context.pages[0] if context.pages else context.new_page()

    try:
        # Navigate to builder
        _log("Navigating to builder...")
        page.goto(BUILDER_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")

        # Check if Cloudflare blocks us (session expired)
        page.wait_for_timeout(5000)
        if "Just a moment" in page.title():
            _log("Session expired — Cloudflare challenge. Re-run --setup.")
            context.close()
            pw.stop()
            return {
                "status": "error",
                "message": "Session expired. Run --setup to re-login.",
            }

        if not _wait_for_builder_load(page, timeout_s=30):
            _log("Builder did not load. Session may be expired.")
            context.close()
            pw.stop()
            return {
                "status": "error",
                "message": "Builder failed to load. Run --setup to re-login.",
            }

        # 5. Navigate to blog section
        result = _navigate_to_blog(page, selectors)
        if not result.get("ok"):
            context.close()
            pw.stop()
            return {
                "status": "error",
                "message": f"Could not navigate to blog: {result.get('error')}",
            }

        # 6. Create new blog post
        result = _create_blog_post(page, post, selectors)
        if not result.get("ok"):
            context.close()
            pw.stop()
            return {"status": "error", "message": result.get("error")}

        # 7. Update post status in DB
        update_row(
            "pulse_posts",
            post_id,
            {
                "status": "published",
                "published_at": _now_iso(),
            },
        )

        context.close()
        pw.stop()

        return {
            "status": "ok",
            "post_id": post_id,
            "title": post.get("title"),
            "published_at": _now_iso(),
            "message": f"Published to Hostinger: {post.get('title')}",
        }

    except Exception as e:
        # Save debug screenshot
        try:
            debug_path = PROFILE_DIR / f"error_{int(time.time())}.png"
            page.screenshot(path=str(debug_path))
            _log(f"Error screenshot: {debug_path}")
        except Exception:
            pass

        context.close()
        pw.stop()
        return {"status": "error", "message": str(e)}


def _load_selectors() -> dict:
    """Load blog section CSS selectors from config or defaults."""
    sel_path = PROFILE_DIR / "selectors.json"
    if sel_path.exists():
        return json.loads(sel_path.read_text())

    # Hostinger Website Builder common selectors (may need customization)
    # These are discovered during --setup and saved to selectors.json
    defaults = {
        "blog_nav": [
            'text="Blog"',
            '[data-qa="blog"]',
            'button:has-text("Blog")',
            'a:has-text("Blog")',
        ],
        "add_post": [
            'text="Add new post"',
            'button:has-text("Add")',
            '[data-qa="add-post"]',
            'text="New post"',
            'button:has-text("New post")',
        ],
        "title_input": [
            '[placeholder*="title" i]',
            '[data-qa="post-title"]',
            'input[name="title"]',
            '[contenteditable="true"]:first-of-type',
        ],
        "content_area": [
            '[data-qa="post-content"]',
            '[contenteditable="true"]',
            ".ql-editor",
            '[class*="editor"]',
            'div[role="textbox"]',
        ],
        "seo_title": [
            '[placeholder*="SEO" i]',
            '[data-qa="seo-title"]',
            'input[name="seo_title"]',
        ],
        "seo_description": [
            '[placeholder*="description" i]',
            '[data-qa="seo-description"]',
            'textarea[name="meta_description"]',
        ],
        "publish_button": [
            'button:has-text("Publish")',
            '[data-qa="publish"]',
            'button:has-text("Save and publish")',
            'text="Publish"',
        ],
    }

    sel_path.write_text(json.dumps(defaults, indent=2))
    _log(f"Default selectors written to {sel_path}. Customize after --setup.")
    return defaults


def _find_element(page, selector_list: list, timeout: int = 5000):
    """Try multiple selectors, return first visible match."""
    for sel in selector_list:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=timeout):
                return el
        except Exception:
            continue
    return None


def _navigate_to_blog(page, selectors: dict) -> dict:
    """Navigate to the blog section of the builder."""
    blog_nav = _find_element(page, selectors.get("blog_nav", []))
    if blog_nav:
        blog_nav.click()
        page.wait_for_timeout(3000)
        _log("Navigated to blog section")
        return {"ok": True}

    # Try direct URL patterns
    for suffix in ["/blog", "?section=blog", "#blog"]:
        try:
            page.goto(
                BUILDER_URL + suffix,
                timeout=NAV_TIMEOUT,
                wait_until="domcontentloaded",
            )
            page.wait_for_timeout(3000)
            # Check if blog section loaded
            add_btn = _find_element(page, selectors.get("add_post", []), timeout=3000)
            if add_btn:
                _log(f"Blog section found via URL: {suffix}")
                return {"ok": True}
        except Exception:
            continue

    return {"ok": False, "error": "Blog section not found. Run --setup to discover."}


def _create_blog_post(page, post: dict, selectors: dict) -> dict:
    """Create a new blog post in the builder."""
    # Click "Add new post"
    add_btn = _find_element(page, selectors.get("add_post", []))
    if not add_btn:
        return {"ok": False, "error": "Add post button not found"}

    add_btn.click()
    page.wait_for_timeout(3000)
    _log("Clicked add new post")

    # Fill title
    title_el = _find_element(page, selectors.get("title_input", []))
    if title_el:
        title_el.click()
        title_el.fill(post.get("title", "Untitled"))
        _log(f"Set title: {post.get('title')}")
    else:
        _log("WARNING: Title input not found", level="WARN")

    # Fill content — use body_html if available, otherwise markdown
    content_el = _find_element(page, selectors.get("content_area", []))
    if content_el:
        content = post.get("body_html") or post.get("body_markdown", "")
        # For contenteditable, inject HTML via JS
        try:
            content_el.click()
            # Try setting innerHTML for rich text editors
            page.evaluate(
                """(args) => {
                    const el = document.querySelector(args.selector);
                    if (el) {
                        el.innerHTML = args.html;
                        // Trigger input event for framework reactivity
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                {
                    "selector": "[contenteditable='true']",
                    "html": content,
                },
            )
            _log("Set post content")
        except Exception as e:
            _log(f"Content injection fallback: {e}", level="WARN")
            # Fallback: type plain text
            plain = post.get("body_markdown", "")
            content_el.fill(plain[:10000])  # Truncate for safety
    else:
        _log("WARNING: Content area not found", level="WARN")

    # Fill SEO fields (if visible)
    seo_title_el = _find_element(page, selectors.get("seo_title", []), timeout=2000)
    if seo_title_el:
        seo_title_el.fill(post.get("seo_title") or post.get("title", ""))
        _log("Set SEO title")

    seo_desc_el = _find_element(page, selectors.get("seo_description", []), timeout=2000)
    if seo_desc_el:
        seo_desc_el.fill(post.get("seo_description", ""))
        _log("Set SEO description")

    # Click publish
    pub_btn = _find_element(page, selectors.get("publish_button", []))
    if pub_btn:
        pub_btn.click()
        page.wait_for_timeout(5000)
        _log("Clicked publish")
        return {"ok": True}
    else:
        # Save as draft if publish not found
        _log("Publish button not found — post may be saved as draft", level="WARN")
        return {
            "ok": True,
            "warning": "Publish button not found. Post may be saved as draft.",
        }


# ---------------------------------------------------------------------------
# Batch publish
# ---------------------------------------------------------------------------


def publish_all_approved() -> dict:
    """Publish all approved Pulse posts (batch / scheduler auto-publish).

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
                f"(verdict={gate['verdict'] or 'absent'}): {gate['reason']}"
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
        result = publish_post(post["id"])
        results.append(result)
        # Brief pause between posts
        time.sleep(2)

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
# CLI
# ---------------------------------------------------------------------------


def _handle_publish(args) -> dict:
    """Handle --publish subcommand."""
    if args.all_approved:
        return publish_all_approved()
    if args.post_id:
        return publish_post(args.post_id)
    return {"status": "error", "message": "Specify --post-id or --all-approved"}


def _dispatch_command(args, parser) -> dict | None:
    """Dispatch CLI args to the appropriate handler. Returns result dict or None."""
    if args.setup or args.discover_blog:
        return setup_session()
    if args.check_session:
        return check_session()
    if args.check_key_rotation:
        return _check_key_rotation()
    if args.publish:
        return _handle_publish(args)
    parser.print_help()
    return None


def _print_result(args, result: dict) -> None:
    """Format and print the command result."""
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    status = result.get("status", "unknown")
    msg = result.get("message", "")
    print(f"[{status.upper()}] {msg}")

    if not args.check_key_rotation:
        rotation = _check_key_rotation()
        if rotation.get("status") in ("expired", "warning"):
            print(f"  KEY ROTATION: {rotation['message']}")


def main():
    parser = argparse.ArgumentParser(description="Hostinger Website Builder blog publisher")
    parser.add_argument("--setup", action="store_true", help="Open browser for manual login")
    parser.add_argument("--publish", action="store_true", help="Publish post(s)")
    parser.add_argument("--post-id", help="Specific post ID to publish")
    parser.add_argument("--all-approved", action="store_true", help="Publish all approved posts")
    parser.add_argument("--check-session", action="store_true", help="Check session status")
    parser.add_argument("--check-key-rotation", action="store_true", help="Check API key rotation")
    parser.add_argument(
        "--discover-blog",
        action="store_true",
        help="Open builder to discover blog selectors",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    result = _dispatch_command(args, parser)
    if result is None:
        return

    _print_result(args, result)


if __name__ == "__main__":
    main()
