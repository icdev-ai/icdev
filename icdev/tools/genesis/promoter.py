#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Knowledge Bridge — the only gateway from v2.0 to v1.x (D-GEN-4).

Manages Genesis Knowledge Packets (GKPs): structured JSON artifacts that
carry validated knowledge from the experimental lab to the production core.

Usage:
    python tools/genesis/promoter.py --list --json                  # List pending GKPs
    python tools/genesis/promoter.py --export --reflex research \\
        --artifact-type research_signal --payload '{}' --json       # Create a GKP
    python tools/genesis/promoter.py --promote <gkp_id> --json      # Promote to v1.x
    python tools/genesis/promoter.py --reject <gkp_id> --json       # Reject a GKP
    python tools/genesis/promoter.py --auto-promote --json          # Promote all auto-eligible
    python tools/genesis/promoter.py --stats --json                 # Promotion statistics
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.genesis.promoter")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GKP_VERSION = "1.0"
EXPORTS_DIR = BASE_DIR / "data" / "genesis" / "exports"

# Deduplication — loaded from promoter.dedup.similarity_threshold in genesis_config.yaml.
# Kept as a named constant so the anomaly_detection layer can reference it when the
# config is unavailable (e.g. air-gap bootstrap).
_DEDUP_SIMILARITY_THRESHOLD_DEFAULT = 0.85

# Artifact types
ARTIFACT_TYPES = [
    "research_signal",
    "compliance_knowledge",
    "quality_baseline",
    "proven_pattern",
    "capability_update",
    "code_patch",
    "training_pair",
    "anticipation_report",
    # hgx-obs-02: staged output of an ORANGE-tier reflex run in proposal mode.
    # Deliberately has NO _import_to_v1x handler — an ORANGE proposal must be
    # acted on by a human, so it can never auto-promote.
    "orange_proposal",
]

# Promotion statuses
STATUS_PENDING = "pending_review"
STATUS_AUTO_PROMOTED = "auto_promoted"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
STATUS_DEDUP_SKIPPED = "dedup_skipped"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _generate_id() -> str:
    return f"gkp-{uuid.uuid4().hex[:10]}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _log_audit(event_type: str, gkp_id: str = None, details: Dict = None) -> None:
    """Log to genesis_audit (append-only)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO genesis_audit
                (id, event_type, reflex_name, details, gkp_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """,
            (
                f"aud-{uuid.uuid4().hex[:10]}",
                event_type,
                None,
                json.dumps(details) if details else None,
                gkp_id,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best-effort audit
        logger.warning("_log_audit: best-effort INSERT into genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _load_promoter_config() -> Dict[str, Any]:
    """Load promoter config from genesis_config.yaml."""
    try:
        import yaml

        config_path = BASE_DIR / "args" / "genesis_config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            return config.get("promoter", {})
    except ImportError:
        pass
    return {}


def _load_dedup_threshold() -> float:
    """Return the dedup similarity threshold from config, falling back to the named default."""
    try:
        config = _load_promoter_config()
        return config.get("dedup", {}).get("similarity_threshold", _DEDUP_SIMILARITY_THRESHOLD_DEFAULT)
    except Exception:
        return _DEDUP_SIMILARITY_THRESHOLD_DEFAULT


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def _check_duplicate(payload_hash: str, artifact_type: str) -> Optional[str]:
    """Check if a similar GKP already exists.  Returns existing GKP ID or None.

    The similarity threshold is loaded from promoter.dedup.similarity_threshold in
    genesis_config.yaml so the anomaly_detection layer can tune it without code changes.
    """
    _threshold = _load_dedup_threshold()  # config-driven; referenced for future fuzzy match
    conn = get_connection()
    try:
        # Exact hash match
        row = conn.execute(
            """
            SELECT id FROM genesis_gkp
            WHERE sha256 = %s AND artifact_type = %s
              AND promotion_status NOT IN ('rejected', 'dedup_skipped')
        """,
            (payload_hash, artifact_type),
        ).fetchone()
        if row:
            return row["id"]
    finally:
        conn.close()
    return None


# ---------------------------------------------------------------------------
# GKP Operations
# ---------------------------------------------------------------------------
def export_gkp(
    reflex: str, artifact_type: str, payload: Dict[str, Any], confidence: float = 0.0, evidence: Dict = None
) -> Dict[str, Any]:
    """Create a new Genesis Knowledge Packet (D-GEN-3).

    Returns the created GKP record.
    """
    if artifact_type not in ARTIFACT_TYPES:
        return {"error": f"Unknown artifact type: {artifact_type}", "valid_types": ARTIFACT_TYPES}

    gkp_id = _generate_id()
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = _sha256(payload_json)

    # Deduplication check
    existing = _check_duplicate(payload_hash, artifact_type)
    if existing:
        _log_audit("genesis.promoter.dedup_skipped", gkp_id, {"existing_gkp": existing, "artifact_type": artifact_type})
        return {"status": "dedup_skipped", "existing_gkp": existing, "gkp_id": gkp_id}

    # Store in DB
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO genesis_gkp
                (id, gkp_version, artifact_type, genesis_reflex, confidence,
                 evidence, payload, sha256, promotion_status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                gkp_id,
                GKP_VERSION,
                artifact_type,
                reflex,
                confidence,
                json.dumps(evidence) if evidence else None,
                payload_json,
                payload_hash,
                STATUS_PENDING,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Also write to filesystem for portability
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    gkp_file = EXPORTS_DIR / f"{gkp_id}.gkp.json"
    gkp_doc = {
        "gkp_version": GKP_VERSION,
        "id": gkp_id,
        "artifact_type": artifact_type,
        "genesis_reflex": reflex,
        "confidence": confidence,
        "evidence": evidence,
        "payload": payload,
        "sha256": payload_hash,
        "promotion_status": STATUS_PENDING,
        "created_at": _utcnow_iso(),
    }
    gkp_file.write_text(json.dumps(gkp_doc, indent=2), encoding="utf-8", newline="")

    _log_audit(
        "genesis.promoter.exported",
        gkp_id,
        {
            "artifact_type": artifact_type,
            "reflex": reflex,
            "confidence": confidence,
        },
    )

    return {"status": "exported", "gkp_id": gkp_id, "artifact_type": artifact_type}


def promote_gkp(gkp_id: str, auto: bool = False) -> Dict[str, Any]:
    """Promote a GKP from v2.0 to v1.x knowledge stores.

    This is the import gateway — it reads the GKP payload and writes
    to the appropriate v1.x table/config.
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM genesis_gkp WHERE id = %s", (gkp_id,)).fetchone()
        if not row:
            return {"error": f"GKP not found: {gkp_id}"}

        current_status = row["promotion_status"]
        if current_status in (STATUS_PROMOTED, STATUS_AUTO_PROMOTED):
            return {"status": "already_promoted", "gkp_id": gkp_id}
        if current_status == STATUS_REJECTED:
            return {"error": "Cannot promote rejected GKP", "gkp_id": gkp_id}

        artifact_type = row["artifact_type"]
        payload = json.loads(row["payload"])
        confidence = row["confidence"]

        # Pre-promotion coherence gate (D-WF-8)
        try:
            from tools.workflow.coherence_checker import run_checks as coherence_check

            coherence = coherence_check()
            if not coherence.overall_pass:
                _log_audit(
                    "genesis.promoter.coherence_warning",
                    gkp_id,
                    {
                        "failed_checks": coherence.failed_checks,
                        "warned_checks": coherence.warned_checks,
                    },
                )
                if auto:
                    return {
                        "status": "coherence_blocked",
                        "gkp_id": gkp_id,
                        "error": f"Coherence failed: {coherence.failed_checks} failures, {coherence.warned_checks} warnings",
                    }
                # Manual promotions proceed with warning logged
        except Exception:
            pass  # Graceful degradation — coherence unavailable does not block

        # Route to appropriate v1.x import handler
        import_result = _import_to_v1x(artifact_type, payload, confidence)

        if import_result.get("success"):
            new_status = STATUS_AUTO_PROMOTED if auto else STATUS_PROMOTED
            conn.execute(
                """
                UPDATE genesis_gkp SET promotion_status = %s, promoted_at = %s
                WHERE id = %s
            """,
                (new_status, _utcnow_iso(), gkp_id),
            )
            conn.commit()

            event = "genesis.promoter.auto_promoted" if auto else "genesis.promoter.promoted"
            _log_audit(
                event,
                gkp_id,
                {
                    "artifact_type": artifact_type,
                    "import_result": import_result,
                },
            )

            return {
                "status": new_status,
                "gkp_id": gkp_id,
                "artifact_type": artifact_type,
                "import_result": import_result,
            }
        else:
            return {
                "status": "import_failed",
                "gkp_id": gkp_id,
                "error": import_result.get("error", "Unknown import error"),
            }
    finally:
        conn.close()


def reject_gkp(gkp_id: str, reason: str = "") -> Dict[str, Any]:
    """Reject a GKP — it will not be promoted."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT promotion_status FROM genesis_gkp WHERE id = %s", (gkp_id,)).fetchone()
        if not row:
            return {"error": f"GKP not found: {gkp_id}"}

        conn.execute(
            """
            UPDATE genesis_gkp SET promotion_status = %s WHERE id = %s
        """,
            (STATUS_REJECTED, gkp_id),
        )
        conn.commit()

        _log_audit("genesis.promoter.rejected", gkp_id, {"reason": reason})
        return {"status": "rejected", "gkp_id": gkp_id, "reason": reason}
    finally:
        conn.close()


def auto_promote_eligible() -> List[Dict[str, Any]]:
    """Promote all GKPs that meet auto-promotion criteria."""
    config = _load_promoter_config()
    auto_rules = config.get("auto_promote", [])
    results = []

    conn = get_connection()
    try:
        pending = conn.execute(
            """
            SELECT * FROM genesis_gkp WHERE promotion_status = %s
            ORDER BY created_at ASC
        """,
            (STATUS_PENDING,),
        ).fetchall()
    finally:
        conn.close()

    for row in pending:
        artifact_type = row["artifact_type"]
        confidence = row["confidence"]
        reflex = row["genesis_reflex"]
        # Extract source from payload for source-based rules
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        source = payload.get("source", "")

        # Check if any auto-promote rule matches (by artifact_type, reflex, or source)
        matched = False
        for rule in auto_rules:
            min_conf = rule.get("min_confidence", 0.0)
            if confidence < min_conf:
                continue
            if rule.get("artifact_type") and rule["artifact_type"] == artifact_type:
                matched = True
            elif rule.get("reflex") and rule["reflex"] == reflex:
                matched = True
            elif rule.get("source_contains") and rule["source_contains"].lower() in source.lower():
                matched = True
            if matched:
                result = promote_gkp(row["id"], auto=True)
                results.append(result)
                break
        if not matched:
            # No auto-promote rule matched — stays pending for human review
            _log_audit(
                "genesis.promoter.human_review_pending",
                row["id"],
                {
                    "artifact_type": artifact_type,
                    "confidence": confidence,
                    "source": source,
                },
            )

    return results


def _import_to_v1x(artifact_type: str, payload: Dict, confidence: float) -> Dict:
    """Import a GKP payload into the appropriate v1.x knowledge store.

    Each artifact type maps to a specific table or config destination.
    """
    try:
        if artifact_type == "research_signal":
            return _import_research_signal(payload)
        elif artifact_type == "compliance_knowledge":
            return _import_compliance_knowledge(payload)
        elif artifact_type == "quality_baseline":
            return _import_quality_baseline(payload)
        elif artifact_type == "proven_pattern":
            return _import_proven_pattern(payload, confidence)
        elif artifact_type == "capability_update":
            return _import_capability_update(payload)
        elif artifact_type == "code_patch":
            return _import_code_patch(payload)
        elif artifact_type == "training_pair":
            return _import_training_pair(payload)
        elif artifact_type == "anticipation_report":
            return _import_anticipation_report(payload, confidence)
        elif artifact_type == "orange_proposal":
            return _import_orange_proposal(payload)
        else:
            return {"success": False, "error": f"No import handler for: {artifact_type}"}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _import_research_signal(payload: Dict) -> Dict:
    """Import a research signal into innovation_signals table."""
    conn = get_connection()
    try:
        signal_id = f"sig-{uuid.uuid4().hex[:8]}"
        title = payload.get("title", "Genesis Research Signal")
        description = payload.get("description", "")
        content_hash = _sha256(f"{title}:{description}")
        conn.execute(
            """
            INSERT INTO innovation_signals
                (id, source, source_type, title, description, content_hash,
                 innovation_score, status, discovered_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                signal_id,
                payload.get("source", "genesis_research"),
                "genesis",
                title,
                description,
                content_hash,
                payload.get("score", 50),
                "new",
                _utcnow_iso(),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return {"success": True, "table": "innovation_signals", "id": signal_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def _import_compliance_knowledge(payload: Dict) -> Dict:
    """Import compliance knowledge into enrichment cache."""
    conn = get_connection()
    try:
        cache_id = f"enr-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO dh_enrichment_cache
                (id, source, query_hash, result_data, expires_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """,
            (
                cache_id,
                payload.get("source", "genesis_comply"),
                _sha256(json.dumps(payload.get("query", ""), sort_keys=True)),
                json.dumps(payload.get("data", {})),
                payload.get("expires_at", _utcnow_iso()),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return {"success": True, "table": "dh_enrichment_cache", "id": cache_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def _import_quality_baseline(payload: Dict) -> Dict:
    """Import quality baseline into code_quality_metrics."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO code_quality_metrics
                (id, project_id, file_path, language, total_functions,
                 avg_cyclomatic, avg_cognitive, avg_nesting, avg_params,
                 avg_loc, maintainability_score, smells, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                f"cqm-{uuid.uuid4().hex[:8]}",
                payload.get("project_id", "sparkpilot"),
                payload.get("file_path", ""),
                payload.get("language", "python"),
                payload.get("total_functions", 0),
                payload.get("avg_cyclomatic", 0),
                payload.get("avg_cognitive", 0),
                payload.get("avg_nesting", 0),
                payload.get("avg_params", 0),
                payload.get("avg_loc", 0),
                payload.get("maintainability_score", 100),
                json.dumps(payload.get("smells", [])),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return {"success": True, "table": "code_quality_metrics"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def _import_proven_pattern(payload: Dict, confidence: float) -> Dict:
    """Import a proven pattern into knowledge_patterns."""
    conn = get_connection()
    try:
        pattern_id = f"pat-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO knowledge_patterns
                (id, pattern_type, pattern_signature, root_cause,
                 remediation, confidence, auto_healable, occurrence_count,
                 source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                pattern_id,
                payload.get("pattern_type", "discovered"),
                payload.get("signature", ""),
                payload.get("root_cause", ""),
                json.dumps(payload.get("remediation", {})),
                confidence,
                1 if payload.get("auto_healable", False) else 0,
                payload.get("occurrence_count", 1),
                "genesis_heal",
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return {"success": True, "table": "knowledge_patterns", "id": pattern_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def _import_capability_update(payload: Dict) -> Dict:
    """Import capability update — writes YAML to context/capabilities/.

    Requires human review (YAML file modification).
    """
    return {
        "success": True,
        "action": "staged_for_review",
        "message": "Capability YAML update staged -- requires human review to apply",
        "payload": payload,
    }


def _import_code_patch(payload: Dict) -> Dict:
    """Import code patch — always requires human cherry-pick (D-GEN-7)."""
    return {
        "success": True,
        "action": "staged_for_review",
        "message": "Code patch staged -- requires human cherry-pick to main",
        "branch": payload.get("branch", "unknown"),
        "files_changed": payload.get("files_changed", []),
    }


def _import_anticipation_report(payload: Dict, confidence: float) -> Dict:
    """Import a regulatory anticipation report — creates a suggested kanban task."""
    try:
        from tools.oracle.kanban_bridge import create_suggested_task

        task_id = create_suggested_task(payload, confidence)
        return {
            "success": True,
            "table": "kanban_tasks",
            "id": task_id,
            "note": "Suggested kanban task created from anticipation_report GKP",
        }
    except Exception as e:
        return {"success": False, "error": f"kanban_bridge.create_suggested_task: {e}"}


def _import_orange_proposal(payload: Dict) -> Dict:
    """Acknowledge a human decision on an ORANGE-tier reflex proposal (hgx-obs-02).

    Promoting an ``orange_proposal`` writes nothing to a v1.x store: the payload
    is the *record of a run already performed in proposal mode*, not a change to
    apply.  Whatever the reflex actually wants merged travels as its own GKP —
    ``evolve`` exports a ``code_patch``, which carries its own ``human_approve``
    rule — and is reviewed separately.

    A handler exists purely so the reviewer's Promote click succeeds and is
    audited instead of erroring with "No import handler".  ``orange_proposal``
    is deliberately absent from ``promoter.auto_promote`` in
    ``args/genesis_config.yaml`` and listed under ``human_approve``, so
    ``auto_promote_eligible()`` never matches it — acknowledgement is always a
    human action.
    """
    return {
        "success": True,
        "table": None,
        "id": payload.get("reflex", ""),
        "note": (
            "ORANGE proposal acknowledged — no v1.x import. Any change the reflex "
            "proposes is carried by its own GKP and reviewed separately."
        ),
    }


def _import_training_pair(payload: Dict) -> Dict:
    """Import training pair — always requires human review before training."""
    conn = get_connection()
    try:
        pair_id = f"ftp-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            INSERT INTO ft_training_pairs
                (id, dataset_id, instruction, input_text, output_text,
                 purpose, approved, source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                pair_id,
                payload.get("dataset_id", "genesis_learn"),
                payload.get("instruction", ""),
                payload.get("input_text", ""),
                payload.get("output_text", ""),
                payload.get("purpose", "general"),
                0,  # Always unapproved — requires human review
                "genesis_learn",
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return {
            "success": True,
            "table": "ft_training_pairs",
            "id": pair_id,
            "note": "Pair stored as unapproved — requires human review",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query Operations
# ---------------------------------------------------------------------------
def list_gkps(status: str = None, limit: int = 50) -> List[Dict]:
    """List GKPs, optionally filtered by status."""
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM genesis_gkp WHERE promotion_status = %s
                ORDER BY created_at DESC LIMIT %s
            """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM genesis_gkp ORDER BY created_at DESC LIMIT %s
            """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    """Get promotion statistics."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) as cnt FROM genesis_gkp").fetchone()
        by_status = conn.execute("""
            SELECT promotion_status, COUNT(*) as cnt
            FROM genesis_gkp GROUP BY promotion_status
        """).fetchall()
        by_type = conn.execute("""
            SELECT artifact_type, COUNT(*) as cnt
            FROM genesis_gkp GROUP BY artifact_type
        """).fetchall()
        by_reflex = conn.execute("""
            SELECT genesis_reflex, COUNT(*) as cnt
            FROM genesis_gkp GROUP BY genesis_reflex
        """).fetchall()

        return {
            "total_gkps": total["cnt"] if total else 0,
            "by_status": {r["promotion_status"]: r["cnt"] for r in by_status},
            "by_artifact_type": {r["artifact_type"]: r["cnt"] for r in by_type},
            "by_reflex": {r["genesis_reflex"]: r["cnt"] for r in by_reflex},
            "timestamp": _utcnow_iso(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Genesis Knowledge Bridge — v2.0 to v1.x Promoter")
    parser.add_argument("--list", action="store_true", help="List GKPs")
    parser.add_argument(
        "--status-filter", type=str, default=None, help="Filter by status (pending_review, promoted, rejected)"
    )
    parser.add_argument("--export", action="store_true", help="Create a new GKP")
    parser.add_argument("--reflex", type=str, help="Source reflex name")
    parser.add_argument("--artifact-type", type=str, help="Artifact type")
    parser.add_argument("--payload", type=str, help="JSON payload string")
    parser.add_argument("--confidence", type=float, default=0.0)
    parser.add_argument("--promote", type=str, metavar="GKP_ID", help="Promote a GKP to v1.x")
    parser.add_argument("--reject", type=str, metavar="GKP_ID", help="Reject a GKP")
    parser.add_argument("--reason", type=str, default="", help="Rejection reason")
    parser.add_argument("--auto-promote", action="store_true", help="Auto-promote all eligible GKPs")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.export:
        if not args.reflex or not args.artifact_type or not args.payload:
            print("ERROR: --export requires --reflex, --artifact-type, --payload", file=sys.stderr)
            sys.exit(1)
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON payload: {e}", file=sys.stderr)
            sys.exit(1)
        result = export_gkp(args.reflex, args.artifact_type, payload, confidence=args.confidence)
        print(
            json.dumps(result, indent=2)
            if args.json
            else f"GKP {result.get('gkp_id', 'unknown')}: {result.get('status', 'unknown')}"
        )
        return

    if args.promote:
        result = promote_gkp(args.promote)
        print(
            json.dumps(result, indent=2) if args.json else f"Promote {args.promote}: {result.get('status', 'unknown')}"
        )
        return

    if args.reject:
        result = reject_gkp(args.reject, reason=args.reason)
        print(json.dumps(result, indent=2) if args.json else f"Reject {args.reject}: {result.get('status', 'unknown')}")
        return

    if args.auto_promote:
        results = auto_promote_eligible()
        if args.json:
            print(json.dumps({"auto_promoted": len(results), "results": results}, indent=2))
        else:
            print(f"Auto-promoted {len(results)} GKPs")
            for r in results:
                print(f"  {r.get('gkp_id')}: {r.get('status')}")
        return

    if args.stats:
        stats = get_stats()
        print(
            json.dumps(stats, indent=2)
            if args.json
            else f"Total GKPs: {stats['total_gkps']}\n"
            f"By status: {json.dumps(stats['by_status'])}\n"
            f"By type: {json.dumps(stats['by_artifact_type'])}"
        )
        return

    if args.list:
        gkps = list_gkps(status=args.status_filter)
        if args.json:
            print(json.dumps(gkps, indent=2))
        else:
            print(f"{'ID':<16} {'Type':<20} {'Reflex':<10} {'Conf':<6} {'Status':<18} {'Created'}")
            print("-" * 100)
            for g in gkps:
                print(
                    f"{g['id']:<16} {g['artifact_type']:<20} "
                    f"{g['genesis_reflex']:<10} {g['confidence']:<6.2f} "
                    f"{g['promotion_status']:<18} {g.get('created_at', '')[:16]}"
                )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
