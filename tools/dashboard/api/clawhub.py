# CUI // SP-CTI
"""ClawHub / OpenCLAW marketplace API.

Inline-route blueprint extracted verbatim from tools/dashboard/app.py
(nav-misc-03). Routes keep their exact /api/clawhub/... paths. Each route imports
its own DB handle from tools.marketplace.openclaw_bridge (unchanged). Registered
via _mount_inline(clawhub_api). Pure mechanical extraction - no logic changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request as flask_request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

clawhub_api = Blueprint("clawhub_api", __name__)


@clawhub_api.route("/api/clawhub/search")
def api_clawhub_search():
    """Search ClawHub for skills."""
    query = flask_request.args.get("q", "")
    limit = int(flask_request.args.get("limit", "10"))
    if not query:
        return jsonify({"error": "Missing 'q' parameter"})
    try:
        from tools.databridge.connectors.clawhub_connector import ClawHubConnector

        conn = ClawHubConnector()
        conn.connect({})
        results = conn.search_skills(query, limit=limit)
        conn.disconnect()
        return jsonify({"success": True, "results": results or []})
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/skill/<slug>")
def api_clawhub_detail(slug):
    """Get skill detail from ClawHub."""
    try:
        from tools.databridge.connectors.clawhub_connector import ClawHubConnector

        conn = ClawHubConnector()
        conn.connect({})
        detail = conn.get_skill(slug)
        conn.disconnect()
        return jsonify(detail or {"error": "Not found"})
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/import", methods=["POST"])
def api_clawhub_import():
    """Fetch + import a skill from ClawHub."""
    data = flask_request.get_json(silent=True) or {}
    slug = data.get("slug", "")
    tenant_id = data.get("tenant_id", "default")
    imported_by = data.get("imported_by", "dashboard-user")
    if not slug:
        return jsonify({"error": "Missing 'slug'"})
    try:
        from tools.marketplace.openclaw_bridge import fetch_and_import

        result = fetch_and_import(slug, tenant_id, imported_by)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/promote", methods=["POST"])
def api_clawhub_promote():
    """Promote a quarantined import (auto-approves review if needed)."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    promoted_by = data.get("promoted_by", "dashboard-isso")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import promote_import, _get_db

        # Auto-approve review if not yet done (dashboard user = ISSO)
        conn = _get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE openclaw_imports SET review_id = %s WHERE id = %s AND review_id IS NULL",
                (f"rev-dash-{import_id[:8]}", import_id),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
        result = promote_import(import_id, promoted_by)
        # Trigger companion sync so skill distributes to all 9 LLM platforms
        if result.get("success"):
            try:
                import subprocess as _sp

                _sp.Popen(
                    [sys.executable, "tools/dx/companion.py", "--sync", "--write", "--json"],
                    cwd=str(BASE_DIR),
                    stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL,
                )
            except Exception:
                pass  # Non-blocking — sync failure doesn't fail promotion
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/reject", methods=["POST"])
def api_clawhub_reject():
    """Reject a quarantined import."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    rejected_by = data.get("rejected_by", "dashboard-user")
    reason = data.get("reason", "Rejected via dashboard")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import reject_import

        return jsonify(reject_import(import_id, rejected_by, reason))
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/install-to-project", methods=["POST"])
def api_clawhub_install():
    """Copy a promoted skill to .claude/skills/ for local use."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import _get_db
        import re as _re
        import shutil as _shutil

        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT skill_name, quarantine_path, status FROM openclaw_imports WHERE id = %s", (import_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Import not found"})
        skill_name = row[0] if not hasattr(row, "keys") else row["skill_name"]
        qpath = row[1] if not hasattr(row, "keys") else row["quarantine_path"]
        status = row[2] if not hasattr(row, "keys") else row["status"]
        if status != "promoted":
            return jsonify({"error": f"Must be promoted first (current: {status})"})
        src = Path(qpath)
        if not src.is_dir():
            return jsonify({"error": "Quarantine path not found"})
        slug = _re.sub(r"[^a-z0-9-]", "-", skill_name.lower()).strip("-")[:63] or "imported-skill"
        dest = Path(BASE_DIR) / ".claude" / "skills" / slug
        dest.mkdir(parents=True, exist_ok=True)
        for fname in ("SKILL.md", "skill.md"):
            f = src / fname
            if f.exists():
                _shutil.copy2(f, dest / "SKILL.md")
                break
        for subdir in ("scripts", "context"):
            sd = src / subdir
            dd = dest / subdir
            if sd.is_dir():
                if dd.exists():
                    _shutil.rmtree(dd)
                _shutil.copytree(sd, dd)
        files = [str(f.relative_to(dest)) for f in dest.rglob("*") if f.is_file()]
        return jsonify(
            {"success": True, "installed_to": str(dest), "slug": slug, "files": files, "file_count": len(files)}
        )
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/check-update")
def api_clawhub_check_update():
    """Check if a ClawHub skill has a newer version."""
    import_id = flask_request.args.get("import_id", "")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import _get_db

        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT openclaw_slug, skill_version FROM openclaw_imports WHERE id = %s", (import_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Import not found"})
        slug = row[0] if not hasattr(row, "keys") else row["openclaw_slug"]
        current_ver = str(row[1] if not hasattr(row, "keys") else row["skill_version"])
        from tools.databridge.connectors.clawhub_connector import ClawHubConnector

        c = ClawHubConnector()
        c.connect({})
        detail = c.get_skill(slug)
        c.disconnect()
        if not detail or not detail.get("latestVersion"):
            return jsonify({"success": True, "update_available": False})
        latest_ver = detail["latestVersion"].get("version", "")
        return jsonify(
            {
                "success": True,
                "current_version": current_ver,
                "latest_version": latest_ver,
                "update_available": str(latest_ver) != str(current_ver),
                "changelog": (detail["latestVersion"].get("changelog", "") or "")[:300],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/bulk-import", methods=["POST"])
def api_clawhub_bulk_import():
    """Import multiple skills from ClawHub."""
    data = flask_request.get_json(silent=True) or {}
    slugs = data.get("slugs", [])
    tenant_id = data.get("tenant_id", "default")
    imported_by = data.get("imported_by", "dashboard-user")
    if not slugs:
        return jsonify({"error": "Missing 'slugs' list"})
    results = []
    for slug in slugs[:10]:  # Cap at 10
        try:
            from tools.marketplace.openclaw_bridge import fetch_and_import

            r = fetch_and_import(slug, tenant_id, imported_by)
            results.append(
                {
                    "slug": slug,
                    "success": r.get("success", False),
                    "error": r.get("error"),
                    "import_id": r.get("import_id"),
                }
            )
        except Exception as exc:
            results.append({"slug": slug, "success": False, "error": str(exc)})
    succeeded = sum(1 for r in results if r["success"])
    return jsonify(
        {
            "success": True,
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        }
    )

@clawhub_api.route("/api/clawhub/rate", methods=["POST"])
def api_clawhub_rate():
    """Rate an imported skill (1-5 stars, adjusts trust score)."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    rating = data.get("rating", 0)
    if not import_id or not rating:
        return jsonify({"error": "Missing 'import_id' or 'rating'"})
    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            return jsonify({"error": "Rating must be 1-5"})
        bump = {1: -0.05, 2: -0.02, 3: 0.0, 4: 0.03, 5: 0.05}[rating]
        from tools.marketplace.openclaw_bridge import _get_db

        conn = _get_db()
        conn.cursor().execute(
            "UPDATE openclaw_imports SET trust_score = MIN(1.0, MAX(0.0, trust_score + %s)), updated_at = datetime('now') WHERE id = %s",
            (bump, import_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "rating": rating, "trust_adjustment": bump})
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/view-skill")
def api_clawhub_view_skill():
    """Return the enhanced SKILL.md content for an imported skill."""
    import_id = flask_request.args.get("import_id", "")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import _get_db

        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT skill_name, quarantine_path FROM openclaw_imports WHERE id = %s", (import_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": f"Import not found: {import_id}"})

        skill_name = row[0] if not hasattr(row, "keys") else row["skill_name"]
        qpath = row[1] if not hasattr(row, "keys") else row["quarantine_path"]

        skill_md = Path(qpath) / "SKILL.md"
        if not skill_md.exists():
            skill_md = Path(qpath) / "skill.md"
        if not skill_md.exists():
            return jsonify({"error": "SKILL.md not found in quarantine"})

        content = skill_md.read_text(encoding="utf-8")

        # List context files
        context_dir = Path(qpath) / "context"
        context_files = []
        if context_dir.is_dir():
            context_files = [f.name for f in sorted(context_dir.iterdir()) if f.is_file()]

        return jsonify(
            {
                "success": True,
                "skill_name": skill_name,
                "import_id": import_id,
                "content": content,
                "content_length": len(content),
                "pre_enrichment": (Path(qpath) / "_pre_enrichment.md").read_text(encoding="utf-8")
                if (Path(qpath) / "_pre_enrichment.md").exists()
                else None,
                "context_files": context_files,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/trust", methods=["POST"])
def api_clawhub_trust():
    """Update trust score for an imported skill."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    trust_score = data.get("trust_score")
    if not import_id or trust_score is None:
        return jsonify({"error": "Missing 'import_id' or 'trust_score'"})
    try:
        trust_score = float(trust_score)
        if trust_score < 0 or trust_score > 1.0:
            return jsonify({"error": "Trust score must be between 0.0 and 1.0"})
        from tools.marketplace.openclaw_bridge import _get_db

        conn = _get_db()
        conn.cursor().execute(
            "UPDATE openclaw_imports SET trust_score = %s, updated_at = datetime('now') WHERE id = %s",
            (trust_score, import_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "import_id": import_id, "trust_score": trust_score})
    except Exception as exc:
        return jsonify({"error": str(exc)})

@clawhub_api.route("/api/clawhub/revoke", methods=["POST"])
def api_clawhub_revoke():
    """Revoke (unpromote) a promoted import."""
    data = flask_request.get_json(silent=True) or {}
    import_id = data.get("import_id", "")
    revoked_by = data.get("revoked_by", "dashboard-isso")
    reason = data.get("reason", "Revoked via dashboard")
    if not import_id:
        return jsonify({"error": "Missing 'import_id'"})
    try:
        from tools.marketplace.openclaw_bridge import revoke_import

        return jsonify(revoke_import(import_id, revoked_by, reason))
    except Exception as exc:
        return jsonify({"error": str(exc)})
