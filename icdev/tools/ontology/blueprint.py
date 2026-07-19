# CUI // SP-CTI
"""Ontology Explorer Flask blueprint.

Routes:
    GET  /ontology          — Explorer page (class hierarchy + stats)
    GET  /api/ontology/classes  — JSON list of all ontology classes
    GET  /api/ontology/closure  — JSON transitive closure for a class
    POST /api/ontology/rebuild  — Re-run build_federation()
    POST /api/iqe-query     — IQE natural-language query (registered below)
"""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, request

from tools.dashboard.auth import require_role
from tools.ontology.constants import CANVAS_KEY, DOMAIN_LABELS

bp = Blueprint("ontology", __name__)

# Roles allowed to mutate the ontology (rebuild wipes and regenerates all
# ontology_* tables via build_federation()). nav-intel-01: previously
# unauthenticated — any caller could destroy/rebuild the federated ontology.
_ONTOLOGY_MUTATION_ROLES = ("admin", "pm")


def _get_conn():
    """Return the canonical ICDEV storage connection (respects ICDEV_STORAGE_BACKEND)."""
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection()


def _table_exists(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # nosec B608
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@bp.route("/ontology")
def ontology_page():
    conn = _get_conn()
    try:
        stats: dict = {}
        if _table_exists(conn, "ontology_classes"):
            stats["class_count"] = conn.execute(
                "SELECT COUNT(*) FROM ontology_classes"
            ).fetchone()[0]
            stats["domain_count"] = conn.execute(
                "SELECT COUNT(DISTINCT domain) FROM ontology_classes"
            ).fetchone()[0]
        else:
            stats["class_count"] = 0
            stats["domain_count"] = 0

        if _table_exists(conn, "ontology_subclass_closure"):
            stats["closure_pairs"] = conn.execute(
                "SELECT COUNT(*) FROM ontology_subclass_closure"
            ).fetchone()[0]
        else:
            stats["closure_pairs"] = 0

        if _table_exists(conn, "ontology_alignments"):
            stats["alignment_count"] = conn.execute(
                "SELECT COUNT(*) FROM ontology_alignments"
            ).fetchone()[0]
        else:
            stats["alignment_count"] = 0

        # Domain breakdown
        domains: list[dict] = []
        if _table_exists(conn, "ontology_classes"):
            for row in conn.execute(
                "SELECT domain, COUNT(*) as cnt FROM ontology_classes GROUP BY domain ORDER BY cnt DESC"
            ).fetchall():
                domains.append({
                    "domain": row["domain"],
                    "label": DOMAIN_LABELS.get(row["domain"], row["domain"].replace("_", " ").title()),
                    "count": row["cnt"],
                })

    finally:
        conn.close()

    return render_template(
        "ontology/page.html",
        stats=stats,
        domains=domains,
        canvas_key=CANVAS_KEY,
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@bp.route("/api/ontology/classes")
def api_ontology_classes():
    domain = request.args.get("domain", "")
    conn = _get_conn()
    try:
        if not _table_exists(conn, "ontology_classes"):
            return jsonify({"classes": [], "total": 0})
        if domain:
            rows = conn.execute(
                "SELECT id, domain, label, superclasses, canonical_id FROM ontology_classes WHERE domain=%s ORDER BY domain, label",
                (domain,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, domain, label, superclasses, canonical_id FROM ontology_classes ORDER BY domain, label"
            ).fetchall()
        classes = []
        for r in rows:
            try:
                supers = json.loads(r["superclasses"] or "[]")
            except Exception:
                supers = []
            classes.append({
                "id": r["id"],
                "domain": r["domain"],
                "label": r["label"],
                "superclasses": supers,
                "canonical_id": r["canonical_id"],
            })
    finally:
        conn.close()
    return jsonify({"classes": classes, "total": len(classes)})


@bp.route("/api/ontology/closure")
def api_ontology_closure():
    class_id = request.args.get("class_id", "")
    if not class_id:
        return jsonify({"error": "class_id required"}), 400
    conn = _get_conn()
    try:
        if not _table_exists(conn, "ontology_subclass_closure"):
            return jsonify({"superclasses": [], "subclasses": []})
        superclasses = [
            {"id": r["superclass"], "distance": r["distance"]}
            for r in conn.execute(
                "SELECT superclass, distance FROM ontology_subclass_closure WHERE subclass=%s ORDER BY distance",
                (class_id,),
            ).fetchall()
        ]
        subclasses = [
            {"id": r["subclass"], "distance": r["distance"]}
            for r in conn.execute(
                "SELECT subclass, distance FROM ontology_subclass_closure WHERE superclass=%s ORDER BY distance",
                (class_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    return jsonify({"class_id": class_id, "superclasses": superclasses, "subclasses": subclasses})


@bp.route("/api/ontology/rebuild", methods=["POST"])
@require_role(*_ONTOLOGY_MUTATION_ROLES)
def api_ontology_rebuild():
    try:
        from tools.ontology.federation import build_federation
        result = build_federation()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
