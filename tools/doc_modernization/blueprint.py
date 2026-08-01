# CUI // SP-CTI
"""Central Standards Catalog — canvas-independent curated-standards surface.

Turnkey requirement (approved docmod plan, Part C): every domain gets catalog
capability without enabling any particular canvas. A DevSecOps team that never
enables /network still curates approved/deprecated standards here; the network
team's /network/hardware-profiles and /network/device-profiles stay untouched
and appear read-only via the NetworkCatalogAdapter, labelled with their source.

Every mutation appends a docmod_catalog_audit row (append-only, NIST AU).
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Core-extension convention: app.py registers these blueprints WITHOUT a
# url_prefix (unlike canvases), so the prefix must live on the Blueprint itself.
docmod_bp = Blueprint(
    "standards_catalog", __name__, template_folder="../dashboard/templates",
    url_prefix="/standards-catalog",
)

# Launch domains; future packs appear automatically via pack_loader.
_FALLBACK_DOMAINS = [
    ("network_hardware", "Hardware"),
    ("software", "Software"),
    ("crypto_protocols", "Crypto & Protocols"),
    ("policy_refs", "Policy Standards"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _catalog():
    from tools.doc_modernization.catalog import MergedCatalog
    return MergedCatalog()


def _domains() -> list[tuple[str, str]]:
    try:
        from tools.doc_modernization.pack_loader import load_packs
        packs = load_packs()
        if packs:
            return [(p.pack_id, p.label) for p in packs.values()]
    except Exception as exc:
        logger.warning("standards-catalog: pack registry unavailable: %s", exc)
    return _FALLBACK_DOMAINS


def _audit(conn, entry_id: str, event_type: str, actor: str, details: dict) -> None:
    conn.execute(
        """INSERT INTO docmod_catalog_audit
           (id, entry_id, event_type, actor, details, recorded_at)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (f"aud-{uuid.uuid4().hex[:10]}", entry_id, event_type, actor,
         json.dumps(details, default=str), _now()),
    )


def _actor() -> str:
    try:
        from flask import g
        ctx = getattr(g, "security_context", None) or {}
        return ctx.get("user_id") or ctx.get("username") or "dashboard"
    except Exception:
        return "dashboard"


# ── Page ──────────────────────────────────────────────────────────────────────

@docmod_bp.route("/")
def standards_catalog_page():
    return render_template(
        "standards_catalog/page.html",
        domains=_domains(),
        iqe_canvas="standards_catalog",
    )


# ── APIs ──────────────────────────────────────────────────────────────────────

@docmod_bp.route("/api/entries", methods=["GET"])
def api_list_entries():
    """Merged catalog for a domain — generic store + adapters, source-labelled."""
    domain = (request.args.get("domain") or "").strip()
    if not domain:
        return jsonify({"error": "domain required"}), 400
    status = (request.args.get("status") or "").strip()
    cat = _catalog()
    entries = cat.get_approved(domain) + cat.get_deprecated(domain)
    if status:
        entries = [e for e in entries if e.status == status]
    return jsonify([
        {
            "entry_id": e.entry_id, "domain": e.domain, "category": e.category,
            "vendor": e.vendor, "product": e.product, "model_family": e.model_family,
            "version": e.version, "status": e.status, "eol_date": e.eol_date,
            "eos_date": e.eos_date, "replacement_entry_id": e.replacement_entry_id,
            "tags": e.tags, "source": e.provider_source,
            "editable": e.provider_source == "generic",
            "manage_href": "/network/hardware-profiles"
            if e.provider_source == "network_canvas" else None,
        }
        for e in entries
    ])


@docmod_bp.route("/api/entries", methods=["POST"])
def api_create_entry():
    body = request.get_json(silent=True) or {}
    domain = (body.get("domain") or "").strip()
    product = (body.get("product") or "").strip()
    if not domain or not product:
        return jsonify({"error": "domain and product required"}), 400
    from tools.doc_modernization.constants import CATALOG_STATUSES
    status = (body.get("status") or "approved").strip()
    if status not in CATALOG_STATUSES:
        return jsonify({"error": f"invalid status: {status}"}), 400

    entry_id = f"cat-{uuid.uuid4().hex[:10]}"
    actor = _actor()
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO docmod_catalog_entries
               (entry_id, domain, category, vendor, product, model_family, version,
                status, eol_date, eos_date, replacement_entry_id, metadata_json,
                tags_json, source, created_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'manual',%s,%s,%s)""",
            (
                entry_id, domain, (body.get("category") or "general").strip(),
                body.get("vendor"), product, body.get("model_family"),
                body.get("version"), status, body.get("eol_date"),
                body.get("eos_date"), body.get("replacement_entry_id"),
                json.dumps(body.get("metadata") or {}),
                json.dumps(body.get("tags") or []), actor, _now(), _now(),
            ),
        )
        _audit(conn, entry_id, "create", actor, {"domain": domain, "product": product})
        conn.commit()
        return jsonify({"entry_id": entry_id}), 201
    except Exception as exc:
        logger.warning("standards-catalog create failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@docmod_bp.route("/api/entries/<entry_id>/status", methods=["POST"])
def api_change_status(entry_id: str):
    """Lifecycle: approved -> deprecated -> retired (+ optional replacement link)."""
    body = request.get_json(silent=True) or {}
    from tools.doc_modernization.constants import CATALOG_STATUSES
    new_status = (body.get("status") or "").strip()
    if new_status not in CATALOG_STATUSES:
        return jsonify({"error": f"invalid status: {new_status}"}), 400
    actor = _actor()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT status, is_builtin FROM docmod_catalog_entries WHERE entry_id = %s",
            (entry_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "not found (adapter entries are managed in "
                                     "their source canvas)"}), 404
        old = dict(row)
        conn.execute(
            "UPDATE docmod_catalog_entries SET status = %s, replacement_entry_id = "
            "COALESCE(%s, replacement_entry_id), updated_at = %s WHERE entry_id = %s",
            (new_status, body.get("replacement_entry_id"), _now(), entry_id),
        )
        _audit(conn, entry_id, "status_change", actor,
               {"from": old.get("status"), "to": new_status,
                "replacement_entry_id": body.get("replacement_entry_id")})
        conn.commit()
        return jsonify({"entry_id": entry_id, "status": new_status})
    finally:
        conn.close()


@docmod_bp.route("/api/import", methods=["POST"])
def api_import():
    """Bulk CSV/YAML import into the generic store (source='imported')."""
    actor = _actor()
    rows: list[dict] = []
    fmt = "csv"
    if request.files.get("file"):
        f = request.files["file"]
        text = f.read().decode("utf-8", errors="replace")
        if (f.filename or "").endswith((".yaml", ".yml")):
            fmt = "yaml"
            import yaml
            raw = yaml.safe_load(text) or {}
            rows = raw.get("entries") or []
        else:
            rows = list(csv.DictReader(io.StringIO(text)))
    else:
        body = request.get_json(silent=True) or {}
        rows = body.get("entries") or []
        fmt = "json"

    report = {"imported": 0, "skipped": [], "format": fmt}
    conn = _conn()
    try:
        for i, r in enumerate(rows):
            domain = (r.get("domain") or "").strip()
            product = (r.get("product") or "").strip()
            if not domain or not product:
                report["skipped"].append({"row": i, "reason": "domain/product required"})
                continue
            entry_id = f"cat-{uuid.uuid4().hex[:10]}"
            try:
                conn.execute(
                    """INSERT INTO docmod_catalog_entries
                       (entry_id, domain, category, vendor, product, model_family,
                        version, status, eol_date, eos_date, source, created_by,
                        created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'imported',%s,%s,%s)
                       ON CONFLICT (domain, category, vendor, product, version) DO NOTHING""",
                    (
                        entry_id, domain, (r.get("category") or "general").strip(),
                        r.get("vendor"), product, r.get("model_family"), r.get("version"),
                        (r.get("status") or "approved").strip(), r.get("eol_date"),
                        r.get("eos_date"), actor, _now(), _now(),
                    ),
                )
                _audit(conn, entry_id, "import", actor, {"row": i, "product": product})
                report["imported"] += 1
            except Exception as exc:
                report["skipped"].append({"row": i, "reason": str(exc)})
        conn.commit()
        return jsonify(report)
    finally:
        conn.close()


@docmod_bp.route("/api/propose-from-defacto", methods=["POST"])
def api_propose_from_defacto():
    """One-click promotion of a learned de-facto standard to a draft entry
    (catalog_gap card flow)."""
    body = request.get_json(silent=True) or {}
    if not body.get("domain") or not body.get("product"):
        return jsonify({"error": "domain and product required"}), 400
    from tools.doc_modernization.catalog import propose_from_defacto
    try:
        entry_id = propose_from_defacto(body, actor=_actor())
        return jsonify({"entry_id": entry_id}), 201
    except Exception as exc:
        logger.warning("standards-catalog propose failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@docmod_bp.route("/api/seed", methods=["POST"])
def api_seed():
    """Idempotent launch seeds: crypto/policy entries from the rulebooks.
    Software bootstraps via propose-from-defacto; hardware via the adapter."""
    actor = _actor()
    created = 0
    conn = _conn()
    try:
        seeds: list[dict] = []
        try:
            from tools.doc_modernization.packs.crypto_protocols import load_rulebook
            for rule in load_rulebook():
                seeds.append({
                    "domain": "crypto_protocols", "category": rule.get("entity_type", "protocol"),
                    "product": rule.get("replacement", ""), "status": "approved",
                    "metadata": {"rule_id": rule["id"]},
                })
                seeds.append({
                    "domain": "crypto_protocols", "category": rule.get("entity_type", "protocol"),
                    "product": rule.get("pattern", ""), "status": "deprecated",
                    "metadata": {"rule_id": rule["id"], "pattern": True},
                })
        except Exception as exc:
            logger.warning("standards-catalog: crypto seed unavailable: %s", exc)
        try:
            from tools.doc_modernization.packs.policy_refs import load_supersession_map
            for rule in load_supersession_map():
                seeds.append({
                    "domain": "policy_refs", "category": "standard",
                    "product": rule.get("superseded_by", ""), "status": "approved",
                    "metadata": {"rule_id": rule["id"]},
                })
        except Exception as exc:
            logger.warning("standards-catalog: policy seed unavailable: %s", exc)

        for s in seeds:
            if not s["product"]:
                continue
            entry_id = f"cat-{uuid.uuid4().hex[:10]}"
            # vendor/version use '' not NULL — SQL NULLs never collide in the
            # UNIQUE constraint, which would break seed idempotency.
            cur = conn.execute(
                """INSERT INTO docmod_catalog_entries
                   (entry_id, domain, category, vendor, product, version, status,
                    metadata_json, source, is_builtin, created_by, created_at, updated_at)
                   VALUES (%s,%s,%s,'',%s,'',%s,%s,'imported',1,%s,%s,%s)
                   ON CONFLICT (domain, category, vendor, product, version) DO NOTHING""",
                (entry_id, s["domain"], s["category"], s["product"], s["status"],
                 json.dumps(s.get("metadata") or {}), actor, _now(), _now()),
            )
            if getattr(cur, "rowcount", 0):
                _audit(conn, entry_id, "import", actor, {"seed": True, **s["metadata"]})
                created += cur.rowcount if cur.rowcount > 0 else 0
        conn.commit()
        return jsonify({"seeded": created})
    finally:
        conn.close()


@docmod_bp.route("/api/audit/<entry_id>", methods=["GET"])
def api_entry_audit(entry_id: str):
    conn = _conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM docmod_catalog_audit WHERE entry_id = %s ORDER BY recorded_at",
            (entry_id,),
        ).fetchall()]
        return jsonify(rows)
    finally:
        conn.close()
