# CUI // SP-CTI
"""ICDEV(TM) Pulse - Blog Engine API + pages.

Inline-route blueprint extracted verbatim from tools/dashboard/app.py
(nav-misc-03). Routes keep their exact /pulse and /api/pulse/... paths and all
decorators. Registered via _mount_inline(pulse_api) in
tools/dashboard/api/__init__.py. Pure mechanical extraction - no logic changes.
"""
from __future__ import annotations

import sys
import uuid  # noqa: F401  (used by some routes via local scope)
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path  # noqa: F401

from flask import Blueprint, jsonify, render_template, request as flask_request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.dashboard.config import DB_PATH  # noqa: E402
from tools.dashboard.route_utils import require_installed  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

pulse_api = Blueprint("pulse_api", __name__)

# Editorial roles allowed to approve/reject/publish Pulse posts (moved from app.py).
_PULSE_EDITORIAL_ROLES = ("admin", "pm", "reviewer")

# In-process pipeline/run state (moved from the create_app closure; used only by
# pulse routes, mutated in place - no rebinding, so no `global` needed).
_pulse_pipeline_runs: dict = {}
_sam_bridge_runs: dict[str, dict] = {}


def _get_db():
    """Verbatim copy of app.py create_app()._get_db (nav-misc-03)."""
    import os
    from flask import has_request_context
    if os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() == "postgresql":
        conn = get_connection()
    else:
        conn = get_connection(db_path=str(DB_PATH))
    try:
        if not has_request_context():
            conn.set_security_context(None)  # rls-bypass: CLI / background tasks run without a user session; no tenant context available.
    except Exception:
        pass
    return conn


@pulse_api.route("/pulse")
@require_installed("pulse")
def pulse():
    """ICDEV™ Pulse — AI-powered blog engine dashboard."""
    try:
        from tools.pulse.db import init_db, query_rows

        init_db()
        posts = query_rows("posts", limit=500)
        by_status = {}
        for p in posts:
            s = p.get("status", "unknown")
            by_status[s] = by_status.get(s, 0) + 1
        stats = {
            "total_posts": len(posts),
            "by_status": by_status,
        }
        # Get recent posts for the table (include quality stats)
        with _get_db() as conn:
            recent = conn.execute(
                "SELECT id, title, slug, status, word_count, readability_score, "
                "grammar_score, plagiarism_score, ai_detection_score, tone_score, "
                "writeguard_passed, capabilities_referenced, "
                "hero_image_path, generated_video_path, generated_video_method, "
                "judge_color, judge_composite, judge_combined, "
                "created_at, updated_at, published_at "
                "FROM pulse_posts ORDER BY updated_at DESC LIMIT 50"
            ).fetchall()
            recent_posts = [dict(r) for r in recent]
            research_count = conn.execute("SELECT COUNT(*) FROM pulse_research_cache").fetchone()[0]
            cluster_count = conn.execute("SELECT COUNT(*) FROM pulse_topic_clusters").fetchone()[0]
            run_count = conn.execute("SELECT COUNT(*) FROM pulse_schedule_log").fetchone()[0]
        stats["research_entries"] = research_count
        stats["clusters"] = cluster_count
        stats["pipeline_runs"] = run_count
        # Capability catalog stats
        try:
            from tools.pulse.engine.capability_scanner import load_all_capabilities

            stats["capabilities"] = len(load_all_capabilities())
        except Exception:
            stats["capabilities"] = 0
    except Exception:
        recent_posts = []
        stats = {"total_posts": 0, "by_status": {}, "research_entries": 0, "clusters": 0, "pipeline_runs": 0}
    return render_template("pulse.html", posts=recent_posts, stats=stats)

@pulse_api.route("/pulse/post/<post_id>")
@require_installed("pulse")
def pulse_post_detail(post_id):
    """ICDEV™ Pulse — Single post detail view."""
    try:
        from tools.pulse.db import get_row

        post = get_row("posts", post_id)
    except Exception:
        post = None
    if not post:
        return render_template("pulse.html", posts=[], stats={}, error=f"Post not found: {post_id}"), 404
    # Render markdown to HTML if body_html is missing
    if post.get("body_markdown") and not post.get("body_html"):
        import re

        md = post["body_markdown"]
        # Convert markdown to basic HTML
        lines = md.split("\n")
        html_parts = []
        in_list = False
        in_code = False
        for line in lines:
            stripped = line.strip()
            # Code blocks
            if stripped.startswith("```"):
                if in_code:
                    html_parts.append("</code></pre>")
                    in_code = False
                else:
                    lang = stripped[3:].strip()
                    html_parts.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                    in_code = True
                continue
            if in_code:
                html_parts.append(line.replace("<", "&lt;").replace(">", "&gt;") + "\n")
                continue
            # Headers
            if stripped.startswith("######"):
                html_parts.append(f"<h6>{stripped[6:].strip()}</h6>")
            elif stripped.startswith("#####"):
                html_parts.append(f"<h5>{stripped[5:].strip()}</h5>")
            elif stripped.startswith("####"):
                html_parts.append(f"<h4>{stripped[4:].strip()}</h4>")
            elif stripped.startswith("###"):
                html_parts.append(f"<h3>{stripped[3:].strip()}</h3>")
            elif stripped.startswith("##"):
                html_parts.append(f"<h2>{stripped[2:].strip()}</h2>")
            elif stripped.startswith("# "):
                html_parts.append(f"<h1>{stripped[1:].strip()}</h1>")
            # List items
            elif stripped.startswith("- ") or stripped.startswith("* "):
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                content = stripped[2:]
                content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                html_parts.append(f"<li>{content}</li>")
            elif re.match(r"^\d+\.\s", stripped):
                if not in_list:
                    html_parts.append("<ol>")
                    in_list = True
                content = re.sub(r"^\d+\.\s", "", stripped)
                content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                html_parts.append(f"<li>{content}</li>")
            # Empty line
            elif not stripped:
                if in_list:
                    html_parts.append(
                        "</ul>" if html_parts[-5:] and "<ul>" in "".join(html_parts[-5:]) else "</ol>"
                    )
                    in_list = False
                html_parts.append("")
            # Paragraph
            else:
                if in_list:
                    html_parts.append("</ul>" if "<ul>" in "".join(html_parts[-10:]) else "</ol>")
                    in_list = False
                content = stripped
                content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
                content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
                content = re.sub(r"`(.+?)`", r"<code>\1</code>", content)
                content = re.sub(
                    r"\[(.+?)\]\((.+?)\)", r'<a href="\2" style="color:var(--primary);">\1</a>', content
                )
                html_parts.append(f"<p>{content}</p>")
        if in_list:
            html_parts.append("</ul>")
        if in_code:
            html_parts.append("</code></pre>")
        post["body_html"] = "\n".join(html_parts)
    # nav-sec-07: body_html may be LLM-generated/persisted or built above without
    # escaping user text → stored XSS. Sanitize at the render chokepoint (covers
    # both the DB-persisted and inline-generated paths) with the canonical
    # air-gap-safe sanitizer.
    if post.get("body_html"):
        try:
            from tools.docgen.workflow import _sanitize_html as _sanitize_post_html
            post["body_html"] = _sanitize_post_html(post["body_html"])
        except Exception:
            import html as _html_lib
            post["body_html"] = "<pre>" + _html_lib.escape(post["body_html"]) + "</pre>"
    return render_template("pulse_post.html", post=post)

@pulse_api.route("/api/pulse/posts")
@require_installed("pulse")
def api_pulse_list_posts():
    """List all Pulse posts."""
    try:
        from tools.pulse.db import init_db, query_rows

        init_db()
        status = flask_request.args.get("status")
        if status:
            rows = query_rows("posts", where="status = ?", params=(status,), limit=500)
        else:
            rows = query_rows("posts", limit=500)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>")
@require_installed("pulse")
def api_pulse_get_post(post_id):
    """Get a single Pulse post."""
    try:
        from tools.pulse.db import get_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        return jsonify(post)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>", methods=["PUT"])
@require_installed("pulse")
def api_pulse_update_post(post_id):
    """Update a Pulse post."""
    try:
        from tools.pulse.db import get_row, update_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        body = flask_request.get_json(silent=True) or {}
        updates = {}
        for field in ("title", "body_markdown", "tldr", "seo_title", "seo_description", "seo_keywords", "status"):
            if field in body:
                updates[field] = body[field]
        if "title" in updates:
            from slugify import slugify as _slugify

            updates["slug"] = _slugify(updates["title"], max_length=80)
        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400
        update_row("posts", post_id, updates)
        return jsonify({"post_id": post_id, "updated": list(updates.keys())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/approve", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_approve(post_id):
    """Approve a Pulse post."""
    try:
        from tools.pulse.db import get_row, update_row, insert_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        now = datetime.now(timezone.utc).isoformat()
        update_row("posts", post_id, {"status": "approved"})
        insert_row(
            "post_reviews",
            {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "approved",
                "notes": "",
                "created_at": now,
            },
        )
        return jsonify({"status": "approved", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/reject", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_reject(post_id):
    """Reject a Pulse post."""
    try:
        from tools.pulse.db import get_row, update_row, insert_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        body = flask_request.get_json(silent=True) or {}
        notes = body.get("notes", "")
        now = datetime.now(timezone.utc).isoformat()
        update_row("posts", post_id, {"status": "rejected", "review_notes": notes})
        insert_row(
            "post_reviews",
            {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "rejected",
                "notes": notes,
                "created_at": now,
            },
        )
        return jsonify({"status": "rejected", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/judge", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_judge_post(post_id):
    """Run LLM Judge (Prometheus-2) on a Pulse post."""
    import threading

    try:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT id, body_markdown, readability_score FROM pulse_posts WHERE id = %s",
                (post_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({"error": "Post not found"}), 404

        def _judge(pid, body, wg_score):
            try:
                from tools.writing.llm_judge import evaluate_and_store, init_judge_db

                init_judge_db()
                result = evaluate_and_store(
                    text=body,
                    content_type="blog",
                    writeguard_score=wg_score or 0,
                    post_id=pid,
                )
                if result.get("status") == "evaluated":
                    conn2 = _get_db()
                    # try/finally: this runs on a daemon thread under a bare
                    # `except Exception: pass`, so a raise between open and close
                    # leaks the connection silently -- an idle-in-transaction
                    # backend on PostgreSQL that never gets reclaimed.
                    try:
                        conn2.execute(
                            "UPDATE pulse_posts SET judge_color = %s, judge_composite = %s, "
                            "judge_combined = %s WHERE id = %s",
                            (
                                result["color_rating"]["color"],
                                result["composite_score"],
                                result.get("combined_score", 0),
                                pid,
                            ),
                        )
                        conn2.commit()
                    finally:
                        conn2.close()
            except Exception:
                pass

        threading.Thread(
            target=_judge,
            args=(post_id, row["body_markdown"], row["readability_score"]),
            daemon=True,
        ).start()
        return jsonify({"status": "judging", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/undo-reject", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_undo_reject(post_id):
    """Undo rejection — revert post to draft status."""
    try:
        from tools.pulse.db import get_row, update_row, insert_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        if post.get("status") != "rejected":
            return jsonify({"error": f"Post is {post.get('status')}, not rejected"}), 400
        now = datetime.now(timezone.utc).isoformat()
        update_row("posts", post_id, {"status": "draft", "review_notes": ""})
        insert_row(
            "post_reviews",
            {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "undo_reject",
                "notes": "Reverted to draft",
                "created_at": now,
            },
        )
        return jsonify({"status": "draft", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/publish", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_publish(post_id):
    """Publish a Pulse post, export, and optionally push to Hostinger.

    Hard-gated on the LLM-judge verdict (nav-intel-09, "block red"): a RED
    verdict — or no verdict at all (judge never ran / errored) — refuses the
    publish with 409 ``{blocked: true, reason, verdict}``. An admin may override
    with ``force_publish=true`` + a non-empty ``force_reason``; the override is
    recorded in the append-only ``pulse_publish_audit`` table (NIST AU).
    """
    try:
        from flask import g

        from tools.pulse.db import get_row, update_row
        from tools.pulse.engine.exporter import export_both
        from tools.pulse.publish_gate import evaluate_publish_gate, record_publish_override

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404

        body = flask_request.get_json(silent=True) or {}
        force_publish = bool(body.get("force_publish", False))
        force_reason = (body.get("force_reason") or "").strip()

        gate = evaluate_publish_gate(post)
        forced = False
        if gate["blocked"]:
            if not force_publish:
                return jsonify(
                    {
                        "blocked": True,
                        "reason": gate["reason"],
                        "verdict": gate["verdict"],
                        "post_id": post_id,
                    }
                ), 409
            # Audited HITL override — admin role only + documented reason.
            user = getattr(g, "current_user", None) or {}
            role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
            if role != "admin":
                return jsonify(
                    {
                        "blocked": True,
                        "reason": "Force-publishing over a blocked judge verdict requires the admin role.",
                        "verdict": gate["verdict"],
                        "post_id": post_id,
                    }
                ), 403
            if not force_reason:
                return jsonify(
                    {
                        "blocked": True,
                        "reason": "force_publish requires a non-empty force_reason.",
                        "verdict": gate["verdict"],
                        "post_id": post_id,
                    }
                ), 409
            record_publish_override(
                post_id,
                reviewer=(user.get("id") if isinstance(user, dict) else "admin"),
                verdict=gate["verdict"],
                reason=force_reason,
                tenant_id=(user.get("tenant_id") if isinstance(user, dict) else None),
            )
            forced = True

        now = datetime.now(timezone.utc).isoformat()
        update_row("posts", post_id, {"status": "published", "published_at": now})
        exports = export_both(post_id)

        # Auto-push to WordPress (icdev.ai). A forced local publish threads the
        # override through so the WP-layer gate does not re-block it.
        wp_result = None
        auto_push = body.get("auto_push", True)
        if auto_push:
            try:
                from tools.pulse.engine.wordpress_publisher import publish_post as wp_publish

                wp_result = wp_publish(post_id, force=forced)
            except Exception as we:
                wp_result = {"status": "error", "message": str(we)}

        return jsonify(
            {
                "status": "published",
                "post_id": post_id,
                "forced": forced,
                "verdict": gate["verdict"],
                "exports": exports,
                "hostinger": wp_result,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/unpublish", methods=["POST"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_unpublish(post_id):
    """Unpublish a post: revert to draft locally and set WP post to draft."""
    try:
        from tools.pulse.db import get_row, update_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        update_row(
            "posts",
            post_id,
            {
                "status": "draft",
                "published_at": None,
            },
        )

        # Set WordPress post to draft if it was published there
        wp_result = None
        wp_post_id = post.get("wp_post_id")
        if wp_post_id:
            try:
                from tools.pulse.engine.wordpress_publisher import (
                    _get_client,
                    WP_BLOG_ID,
                    WP_USERNAME,
                    WP_PASSWORD,
                )

                if WP_PASSWORD:
                    wp = _get_client()
                    wp.wp.editPost(
                        WP_BLOG_ID,
                        WP_USERNAME,
                        WP_PASSWORD,
                        wp_post_id,
                        {"post_status": "draft"},
                    )
                    wp_result = {"status": "ok", "wp_post_id": wp_post_id, "wp_status": "draft"}
            except Exception as we:
                wp_result = {"status": "error", "message": str(we)}

        return jsonify(
            {
                "status": "unpublished",
                "post_id": post_id,
                "wordpress": wp_result,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/push-hostinger", methods=["POST"])
@require_installed("pulse")
def api_pulse_push_hostinger(post_id):
    """Push a published post to WordPress (icdev.ai)."""
    try:
        from tools.pulse.db import get_row
        from tools.pulse.engine.wordpress_publisher import publish_post as wp_publish

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        if post.get("status") != "published":
            return jsonify({"error": f"Post must be published first (current: {post.get('status')})"}), 400
        result = wp_publish(post_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/hostinger/session")
@require_installed("pulse")
def api_pulse_hostinger_session():
    """Check WordPress connection status."""
    try:
        from tools.pulse.engine.wordpress_publisher import test_connection

        result = test_connection()
        return jsonify(
            {
                "session": result,
                "key_rotation": {"status": "ok", "message": "N/A — WordPress uses password auth"},
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/export", methods=["POST"])
@require_installed("pulse")
def api_pulse_export(post_id):
    """Export a Pulse post as MDX + HTML."""
    try:
        from tools.pulse.db import get_row
        from tools.pulse.engine.exporter import export_both

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        exports = export_both(post_id)
        return jsonify({"post_id": post_id, "exports": exports})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>", methods=["DELETE"])
@require_installed("pulse")
@require_role(*_PULSE_EDITORIAL_ROLES)
def api_pulse_archive(post_id):
    """Archive or permanently delete a Pulse post."""
    try:
        from tools.pulse.db import get_row, update_row

        post = get_row("posts", post_id)
        if not post:
            return jsonify({"error": "Post not found"}), 404
        permanent = flask_request.args.get("permanent", "false").lower() == "true"
        if permanent:
            with _get_db() as conn:
                conn.execute("DELETE FROM pulse_posts WHERE id = %s", (post_id,))
                conn.commit()
            return jsonify({"status": "deleted", "post_id": post_id, "permanent": True})
        now = datetime.now(timezone.utc).isoformat()
        update_row("posts", post_id, {"status": "archived", "archived_at": now})
        return jsonify({"status": "archived", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/research")
@require_installed("pulse")
def api_pulse_research():
    """List Pulse research cache entries."""
    try:
        from tools.pulse.db import query_rows

        limit = flask_request.args.get("limit", 50, type=int)
        rows = query_rows("research_cache", limit=limit)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/clusters")
@require_installed("pulse")
def api_pulse_clusters():
    """List Pulse topic clusters."""
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM pulse_topic_clusters ORDER BY priority_score DESC LIMIT 100"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/pipeline/run", methods=["POST"])
@require_installed("pulse")
def api_pulse_pipeline_run():
    """Trigger a Pulse content pipeline run.

    Two modes:
    - With body_markdown + topic: Process pre-written draft (Claude Code orchestrated)
    - With topic only: Run research + cluster phase (returns context for Claude Code)
    - No params: Run research + cluster for all configured topics
    """
    import threading

    try:
        from tools.pulse.db import init_db

        init_db()
        body = flask_request.get_json(silent=True) or {}
        topic = body.get("topic")
        body_markdown = body.get("body_markdown")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        _pulse_pipeline_runs[run_id] = {"run_id": run_id, "status": "running"}

        def _run_bg(rid, t, bm):
            try:
                if bm and t:
                    # Claude Code wrote the article — run post-processing
                    from tools.pulse.engine.scheduler import run_pipeline_from_draft

                    result = run_pipeline_from_draft(t, bm, [])
                else:
                    # Research + cluster only — returns context for Claude Code
                    from tools.pulse.engine.scheduler import research_phase

                    result = research_phase(topic_override=t)
                _pulse_pipeline_runs[rid] = result
            except Exception as exc:
                _pulse_pipeline_runs[rid] = {"run_id": rid, "status": "failed", "error": str(exc)}

        threading.Thread(target=_run_bg, args=(run_id, topic, body_markdown), daemon=True).start()
        return jsonify({"run_id": run_id, "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/rewrite", methods=["POST"])
@require_installed("pulse")
def api_pulse_rewrite_post(post_id):
    """Update a post with rewritten content from Claude Code."""
    try:
        from tools.pulse.engine.scheduler import update_post_content

        body = flask_request.get_json(silent=True) or {}
        body_markdown = body.get("body_markdown")
        if not body_markdown:
            return jsonify({"error": "body_markdown is required"}), 400
        result = update_post_content(post_id, body_markdown)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/rewrite-llm", methods=["POST"])
@require_installed("pulse")
def api_pulse_rewrite_llm(post_id):
    """Trigger Claude Sonnet rewrite for a post via LLM router.

    Reads the post, runs WriteGuard to get findings, then rewrites
    via the LLM router (pulse_rewrite → Claude Sonnet planner tier).
    """
    try:
        from tools.pulse.engine.scheduler import rewrite_post_via_llm

        result = rewrite_post_via_llm(post_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/<post_id>/enrich-capabilities", methods=["POST"])
@require_installed("pulse")
def api_pulse_enrich_capabilities(post_id):
    """Rewrite a post with ICDEV™ capability context injected.

    Matches capabilities based on title/topic, injects into rewrite prompt,
    triggers Claude Sonnet rewrite with capability references.
    """
    try:
        from tools.pulse.engine.scheduler import enrich_post_with_capabilities

        result = enrich_post_with_capabilities(post_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/posts/enrich-all", methods=["POST"])
@require_installed("pulse")
def api_pulse_enrich_all():
    """Enrich all published posts with ICDEV™ capabilities (batch)."""
    import threading

    try:
        from tools.pulse.db import init_db

        init_db()
        with _get_db() as conn:
            posts = conn.execute("SELECT id, title FROM pulse_posts WHERE status = 'published'").fetchall()

        run_id = f"enrich-{__import__('uuid').uuid4().hex[:8]}"
        post_ids = [p["id"] for p in posts]

        def _run_batch():
            from tools.pulse.engine.scheduler import enrich_post_with_capabilities

            results = []
            for pid in post_ids:
                try:
                    r = enrich_post_with_capabilities(pid)
                    results.append({"post_id": pid, "status": r.get("status", "unknown")})
                except Exception as e:
                    results.append({"post_id": pid, "status": "error", "error": str(e)})
            # Store results in pipeline runs table
            try:
                from tools.pulse.db import insert_row

                insert_row(
                    "pipeline_runs",
                    {
                        "id": run_id,
                        "status": "completed",
                        "stage": "enrich_capabilities",
                        "config_json": __import__("json").dumps({"post_ids": post_ids}),
                        "result_json": __import__("json").dumps(results),
                    },
                )
            except Exception:
                pass

        t = threading.Thread(target=_run_batch, daemon=True)
        t.start()
        return jsonify(
            {
                "status": "started",
                "run_id": run_id,
                "posts_queued": len(post_ids),
                "post_ids": post_ids,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/pipeline/run-full", methods=["POST"])
@require_installed("pulse")
def api_pulse_pipeline_run_full():
    """Run the full automated pipeline: research → draft → quality → rewrite.

    Uses LLM router: qwen3.5 for research/draft, Claude Sonnet for rewrite.

    Body params:
        topic (str, optional): Topic override.
        template_type (str): 'challenge_solution' or 'feature_spotlight'.
        auto_rewrite (bool): Whether to auto-rewrite via Sonnet (default true).
    """
    import threading

    try:
        from tools.pulse.db import init_db

        init_db()
        body = flask_request.get_json(silent=True) or {}
        topic = body.get("topic")
        template_type = body.get("template_type", "challenge_solution")
        auto_rewrite = body.get("auto_rewrite", True)
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        _pulse_pipeline_runs[run_id] = {"run_id": run_id, "status": "running", "stage": "research"}

        _PULSE_STAGES = ["research", "quality_check", "rewrite", "publish"]

        def _run_bg(rid, t, tmpl, ar):
            def _on_stage(stage):
                _pulse_pipeline_runs[rid] = {
                    "run_id": rid,
                    "status": "running",
                    "stage": stage,
                }
                # SSE progress broadcast
                try:
                    from tools.dashboard.sse_manager import emit_progress

                    idx = _PULSE_STAGES.index(stage) if stage in _PULSE_STAGES else 0
                    emit_progress(
                        rid,
                        "pulse_pipeline",
                        stage,
                        idx + 1,
                        len(_PULSE_STAGES),
                        detail=f"Pulse pipeline: {stage}",
                    )
                except Exception:
                    pass

            try:
                from tools.pulse.engine.scheduler import run_full_pipeline

                result = run_full_pipeline(
                    topic_override=t,
                    template_type=tmpl,
                    auto_rewrite=ar,
                    progress_callback=_on_stage,
                )
                _pulse_pipeline_runs[rid] = result
            except Exception as exc:
                _pulse_pipeline_runs[rid] = {
                    "run_id": rid,
                    "status": "failed",
                    "stage": "error",
                    "error": str(exc),
                }

        threading.Thread(
            target=_run_bg,
            args=(run_id, topic, template_type, auto_rewrite),
            daemon=True,
        ).start()
        return jsonify({"run_id": run_id, "status": "started", "stage": "research"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/pipeline/status/<run_id>")
@require_installed("pulse")
def api_pulse_pipeline_status(run_id):
    """Get Pulse pipeline run status."""
    if run_id in _pulse_pipeline_runs:
        return jsonify(_pulse_pipeline_runs[run_id])
    try:
        from tools.pulse.db import get_row

        entry = get_row("schedule_log", run_id)
        if not entry:
            return jsonify({"error": "Run not found"}), 404
        return jsonify(entry)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/pipeline/history")
@require_installed("pulse")
def api_pulse_pipeline_history():
    """Get Pulse pipeline run history."""
    try:
        with _get_db() as conn:
            rows = conn.execute("SELECT * FROM pulse_schedule_log ORDER BY started_at DESC LIMIT 50").fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/authors")
@require_installed("pulse")
def api_pulse_authors():
    """List Pulse authors."""
    try:
        from tools.pulse.db import query_rows

        rows = query_rows("authors", limit=100)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/authors", methods=["POST"])
@require_installed("pulse")
def api_pulse_create_author():
    """Create a Pulse author."""
    try:
        from tools.pulse.db import insert_row

        body = flask_request.get_json(silent=True) or {}
        name = body.get("name")
        if not name:
            return jsonify({"error": "name is required"}), 400
        author_id = f"author-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "id": author_id,
            "name": name,
            "email": body.get("email", ""),
            "bio": body.get("bio", ""),
            "role": body.get("role", "contributor"),
            "created_at": now,
        }
        insert_row("authors", data)
        return jsonify(data), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/stats")
@require_installed("pulse")
def api_pulse_stats():
    """Get Pulse pipeline statistics."""
    try:
        from tools.pulse.db import init_db

        init_db()
        with _get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM pulse_posts").fetchone()[0]
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM pulse_posts GROUP BY status"
            ).fetchall()
            by_status = {row["status"]: row["count"] for row in status_rows}
            research_count = conn.execute("SELECT COUNT(*) FROM pulse_research_cache").fetchone()[0]
            cluster_count = conn.execute("SELECT COUNT(*) FROM pulse_topic_clusters").fetchone()[0]
            run_count = conn.execute("SELECT COUNT(*) FROM pulse_schedule_log").fetchone()[0]
        return jsonify(
            {
                "total_posts": total,
                "by_status": by_status,
                "research_entries": research_count,
                "clusters": cluster_count,
                "pipeline_runs": run_count,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/analytics/<post_id>")
@require_installed("pulse")
def api_pulse_analytics(post_id):
    """Get analytics for a Pulse post."""
    try:
        from tools.pulse.db import query_rows

        rows = query_rows("post_analytics", where="post_id = ?", params=(post_id,), limit=100)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/sam-bridge/run", methods=["POST"])
@require_installed("pulse")
def api_pulse_sam_bridge_run():
    """Run SAM-to-Pulse bridge (extracts pain points from SAM.gov, generates articles).

    Body params:
        dry_run (bool): If true, extract topics without generating articles.
        max_articles (int): Max articles to generate (default 5).
    """
    import threading

    try:
        from tools.pulse.db import init_db

        init_db()
        body = flask_request.get_json(silent=True) or {}
        dry_run = body.get("dry_run", False)
        max_articles = body.get("max_articles", 5)
        run_id = f"sam-{uuid.uuid4().hex[:12]}"
        _sam_bridge_runs[run_id] = {
            "run_id": run_id,
            "status": "running",
            "stage": "scanning",
            "dry_run": dry_run,
        }

        def _run_bg(rid, dr, ma):
            try:
                _sam_bridge_runs[rid]["stage"] = "extracting"
                from tools.pulse.engine.sam_bridge import run_sam_to_pulse

                result = run_sam_to_pulse(dry_run=dr, max_articles=ma)
                result["run_id"] = rid
                result["status"] = "completed"
                _sam_bridge_runs[rid] = result
            except Exception as exc:
                _sam_bridge_runs[rid] = {
                    "run_id": rid,
                    "status": "failed",
                    "stage": "error",
                    "error": str(exc),
                }

        threading.Thread(
            target=_run_bg,
            args=(run_id, dry_run, max_articles),
            daemon=True,
        ).start()
        return jsonify({"run_id": run_id, "status": "started", "dry_run": dry_run})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/sam-bridge/status/<run_id>")
@require_installed("pulse")
def api_pulse_sam_bridge_status(run_id):
    """Get SAM bridge run status."""
    if run_id in _sam_bridge_runs:
        return jsonify(_sam_bridge_runs[run_id])
    return jsonify({"error": "Run not found"}), 404

@pulse_api.route("/api/pulse/sam-bridge/stats")
@require_installed("pulse")
def api_pulse_sam_bridge_stats():
    """Get SAM bridge pipeline statistics."""
    try:
        from tools.pulse.db import init_db

        init_db()
        with _get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM pulse_sam_article_log").fetchone()[0]
            by_status = {}
            status_rows = conn.execute(
                "SELECT pipeline_status, COUNT(*) as count FROM pulse_sam_article_log GROUP BY pipeline_status"
            ).fetchall()
            for row in status_rows:
                by_status[row["pipeline_status"]] = row["count"]
            by_domain = {}
            domain_rows = conn.execute(
                "SELECT domain_category, COUNT(*) as count FROM pulse_sam_article_log GROUP BY domain_category"
            ).fetchall()
            for row in domain_rows:
                by_domain[row["domain_category"] or "unknown"] = row["count"]
            recent = conn.execute(
                "SELECT id, opportunity_title, domain_category, article_topic, "
                "pipeline_status, created_at FROM pulse_sam_article_log "
                "ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
        return jsonify(
            {
                "total": total,
                "by_status": by_status,
                "by_domain": by_domain,
                "recent": [dict(r) for r in recent],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/demand-signals")
@require_installed("pulse")
def api_pulse_demand_signals():
    """List demand signals, optionally filtered to high-demand only."""
    try:
        from tools.pulse.db import init_db

        init_db()
        high_only = flask_request.args.get("high_demand", "0") == "1"
        with _get_db() as conn:
            if high_only:
                rows = conn.execute(
                    "SELECT * FROM pulse_demand_signals WHERE is_high_demand = 1 ORDER BY frequency DESC"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM pulse_demand_signals ORDER BY frequency DESC").fetchall()
        return jsonify({"signals": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/demand-signals/aggregate")
@require_installed("pulse")
def api_pulse_demand_signals_aggregate():
    """Aggregate demand signal stats by domain."""
    try:
        from tools.pulse.db import init_db

        init_db()
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT domain_category, COUNT(*) as count, "
                "SUM(CASE WHEN is_high_demand = 1 THEN 1 ELSE 0 END) as high_demand_count, "
                "AVG(frequency) as avg_frequency "
                "FROM pulse_demand_signals GROUP BY domain_category "
                "ORDER BY count DESC"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM pulse_demand_signals").fetchone()[0]
            high_total = conn.execute(
                "SELECT COUNT(*) FROM pulse_demand_signals WHERE is_high_demand = 1"
            ).fetchone()[0]
        return jsonify(
            {
                "by_domain": [dict(r) for r in rows],
                "total": total,
                "high_demand_total": high_total,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/capability-graph")
@require_installed("pulse")
def api_pulse_capability_graph():
    """Query capability graph edges, optionally filtered by capability slug."""
    try:
        from tools.pulse.db import init_db

        init_db()
        cap_slug = flask_request.args.get("capability")
        with _get_db() as conn:
            if cap_slug:
                rows = conn.execute(
                    "SELECT * FROM pulse_capability_graph WHERE capability_slug = %s ORDER BY confidence DESC",
                    (cap_slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pulse_capability_graph ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        return jsonify({"edges": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/capabilities")
@require_installed("pulse")
def api_pulse_capabilities():
    """List all ICDEV™ capabilities from the capability catalog."""
    try:
        from tools.pulse.engine.capability_scanner import load_domains

        domains = load_domains(include_capabilities=True)
        total = sum(d["capability_count"] for d in domains)
        return jsonify({"domains": domains, "total_capabilities": total, "total_domains": len(domains)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/capabilities/match")
@require_installed("pulse")
def api_pulse_capabilities_match():
    """Match capabilities by keywords."""
    try:
        from tools.pulse.engine.capability_scanner import match_capabilities

        q = flask_request.args.get("q", "")
        top_n = int(flask_request.args.get("top_n", "5"))
        keywords = [kw for kw in q.split() if len(kw) > 2]
        if not keywords:
            return jsonify({"error": "Provide ?q= with keywords"}), 400
        matched = match_capabilities(keywords, top_n=top_n)
        return jsonify({"query": q, "matched": len(matched), "capabilities": matched})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/hero-image/<post_id>")
def api_pulse_hero_image(post_id):
    """Serve a Pulse post hero image from disk."""
    from flask import send_file

    try:
        conn = _get_db()
        row = conn.execute("SELECT hero_image_path FROM pulse_posts WHERE id = %s", (post_id,)).fetchone()
        conn.close()
        if not row or not row["hero_image_path"]:
            return "No image", 404
        img_path = Path(row["hero_image_path"])
        if not img_path.exists():
            return "Image file not found", 404
        mime = "image/png" if str(img_path).endswith(".png") else "image/svg+xml"
        return send_file(str(img_path), mimetype=mime)
    except Exception as e:
        return str(e), 500

@pulse_api.route("/api/pulse/posts/<post_id>/generate-image", methods=["POST"])
@require_installed("pulse")
def api_pulse_generate_image(post_id):
    """Generate a hero image for a Pulse post using SDXL Turbo (local GPU)."""
    import threading

    try:
        conn = _get_db()
        row = conn.execute("SELECT id, title, topic FROM pulse_posts WHERE id = %s", (post_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Post not found"}), 404
        title = row["title"]
        category = row["topic"] or ""

        def _gen(pid, t, c):
            try:
                from tools.pulse.engine.image_generator import generate_hero_image

                result = generate_hero_image(title=t, category=c)
                if result.get("success"):
                    conn2 = _get_db()
                    conn2.execute(
                        "UPDATE pulse_posts SET hero_image_path = %s, hero_image_method = %s, hero_image_prompt = %s WHERE id = %s",
                        (result["path"], result["method"], result.get("prompt", ""), pid),
                    )
                    conn2.commit()
                    conn2.close()
            except Exception:
                pass

        threading.Thread(target=_gen, args=(post_id, title, category), daemon=True).start()
        return jsonify({"success": True, "status": "generating", "method": "sdxl_turbo", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@pulse_api.route("/api/pulse/generated-video/<post_id>")
@require_installed("pulse")
def api_pulse_generated_video(post_id):
    """Serve a Pulse post generated video from disk."""
    from flask import send_file

    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT generated_video_path, generated_video_method FROM pulse_posts WHERE id = %s",
            (post_id,),
        ).fetchone()
        conn.close()
        if not row or not row["generated_video_path"]:
            return "No video", 404
        vid_path = Path(row["generated_video_path"])
        if not vid_path.exists():
            return "Video file not found", 404
        method = row["generated_video_method"] or ""
        if method == "animated_svg" or str(vid_path).endswith(".svg"):
            mime = "image/svg+xml"
        else:
            mime = "video/mp4"
        return send_file(str(vid_path), mimetype=mime)
    except Exception as e:
        return str(e), 500

@pulse_api.route("/api/pulse/posts/<post_id>/generate-video", methods=["POST"])
@require_installed("pulse")
def api_pulse_generate_video(post_id):
    """Generate a hero video for a Pulse post using LTX-Video 2B (local GPU)."""
    import threading

    try:
        conn = _get_db()
        row = conn.execute("SELECT id, title, topic FROM pulse_posts WHERE id = %s", (post_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Post not found"}), 404
        title = row["title"]
        category = row["topic"] or ""

        def _gen(pid, t, c):
            try:
                from tools.pulse.engine.video_generator import generate_post_video

                result = generate_post_video(title=t, category=c)
                if result.get("success"):
                    conn2 = _get_db()
                    conn2.execute(
                        "UPDATE pulse_posts SET generated_video_path = %s, "
                        "generated_video_method = %s, generated_video_duration = %s WHERE id = %s",
                        (result["path"], result["method"], result.get("duration_sec", 0), pid),
                    )
                    conn2.commit()
                    conn2.close()
            except Exception:
                pass

        threading.Thread(target=_gen, args=(post_id, title, category), daemon=True).start()
        return jsonify({"success": True, "status": "generating", "method": "ltx_video_2b", "post_id": post_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
