# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Assessment engine (orchestration).

``assess(source, ...)`` is the single entrypoint that wires the SIPA stages into
one end-to-end assessment of an untrusted artifact, persisting at every step and
returning a compact disposition the route / reflex / CLI can act on:

    ingest.stage            quarantine-first staging + assessment row (status='quarantine')
      -> ingest.reverify    SHA-256 tamper re-baseline (writes tamper_mismatch if changed)
      -> scanners.scan_all  SAST / secrets / deps / malicious-signature -> integrity_findings
      -> capability_extractor.extract  AST behavioral manifest -> integrity_capabilities
      -> (Mode B) claim_parser.parse_claim + intent_reconciler.reconcile_and_persist
                            disclosed-vs-exercised gap -> integrity_findings (reconciliation)
      -> scoring.score      combine all signals -> risk_score + verdict (status='assessed')

Each stage owns its own append-only persistence; the engine simply threads a
single RLS-aware connection through all of them (so the whole assessment commits
against one ``tenant_id`` / ``classification`` context) and reads back the final
counts + disposition.

MODE SELECTION (``mode`` argument):
  * ``'auto'`` (default) resolves to ``provenance_aware`` when a ``project_id`` or
    ``session_id`` is supplied (a formal provenance handle is available) and to
    ``provenance_blind`` otherwise — the primary external-code path.
  * An explicit ``'provenance_aware'`` / ``'provenance_blind'`` overrides the
    auto-selection.
  * The reconciliation stage is mode-specific:
      - ``provenance_blind`` runs **Mode B** (``claim_parser`` +
        ``reconcile_and_persist``): the exercised manifest vs. the author's
        *claimed* set (README / docstrings / declared purpose) ->
        ``undisclosed_capability`` findings.
      - ``provenance_aware`` runs **Mode A** (``reconcile_aware_and_persist``): the
        exercised manifest vs. the RTM-derived *ALLOWED* set (intake requirements
        mapped through the same lexicon) -> ``unauthorized_capability`` findings,
        plus requirement coverage gaps.

SECURITY INVARIANTS (inherited from every stage — the SIPA static-only contract):
  * **Never executes the target.** Staging only copies/clones bytes; scanners run
    in isolated subprocesses against the staged tree; the capability extractor is
    AST-only. Nothing here imports, ``exec``-es, or runs the quarantined artifact.
  * **Quarantine-first.** The artifact lands in ``status='quarantine'`` and stays
    there until scoring transitions it to ``'assessed'``; HITL promote/reject
    (a later stage) owns ``approved`` / ``rejected``.
  * **All DB access via ``get_connection()``** (RLS-aware) with ``tenant_id`` /
    ``classification`` stamped from the active security context.

The feature flag ``constants.FEATURE_FLAG`` (``ICDEV_INTEGRITY_ENABLED``) gates the
*automatic* invokers (route / reflex) via :func:`is_enabled`; ``assess`` itself is
the explicit engine primitive and runs whenever it is called.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

# This module is documented as a path-form CLI (`python tools/integrity/engine.py
# --gate`), and started that way Python puts only `tools/integrity/` on sys.path —
# so the first-party imports below raise ModuleNotFoundError before argparse is
# ever reached. Running it as `python -m` or from a shell whose PYTHONPATH already
# holds the repo root masks this, which is why the CLI appeared to work and only
# ever complained about a missing `--source` (kax-conflict-05).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.integrity import (  # noqa: E402 — must follow the sys.path bootstrap
    capability_extractor,
    claim_parser,
    ingest,
    intent_reconciler,
    provenance,
    scanners,
    scoring,
)
from tools.integrity.constants import FEATURE_FLAG  # noqa: E402
from tools.integrity.db.init_db import init_db  # noqa: E402

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.integrity.engine")

# Canonical mode tokens (subset of constants.ASSESS_MODES that name a concrete
# strategy; 'auto' is resolved away before any row is written).
PROVENANCE_AWARE = "provenance_aware"
PROVENANCE_BLIND = "provenance_blind"

# --------------------------------------------------------------------------- #
# Gate — CI / pre-merge exit-code policy
# --------------------------------------------------------------------------- #
# Exit codes for ``main`` when invoked with ``--gate`` (so the CLI can wire into
# CI / pre-merge hooks). A blocking verdict fails the build; a warn verdict is
# surfaced on stderr but does not fail.
GATE_OK = 0     # verdict cleared the gate (ALLOW, or a warn-only verdict)
GATE_BLOCK = 1  # verdict is in gate.block_on — fail the build

BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/engine.py -> repo root
_CONFIG_PATH = BASE_DIR / "args" / "integrity_config.yaml"

# Fallbacks if integrity_config.yaml is missing/partial: block on QUARANTINE,
# warn (non-blocking) on REVIEW.
_DEFAULT_GATE_BLOCK_ON = ("quarantine",)
_DEFAULT_GATE_WARN_ON = ("review",)


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    """True when SIPA assessment is enabled via ``ICDEV_INTEGRITY_ENABLED``.

    Consulted by the *automatic* invokers (route / reflex) so they no-op when the
    canvas is toggled off. ``assess`` does not self-gate — it is the explicit
    engine primitive and runs whenever a caller invokes it directly.
    """
    return str(os.environ.get(FEATURE_FLAG, "")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #
def resolve_mode(mode: str, project_id: Optional[str], session_id: Optional[str]) -> str:
    """Resolve the assessment ``mode`` to a concrete strategy.

    ``'auto'`` -> ``provenance_aware`` when a provenance handle (``project_id`` or
    ``session_id``) is available, else ``provenance_blind``. An explicit
    ``provenance_aware`` / ``provenance_blind`` is honored verbatim.

    Raises:
        ValueError: ``mode`` is not ``auto`` / ``provenance_aware`` /
            ``provenance_blind``.
    """
    m = (mode or "auto").strip().lower()
    if m == "auto":
        return PROVENANCE_AWARE if (project_id or session_id) else PROVENANCE_BLIND
    if m in (PROVENANCE_AWARE, PROVENANCE_BLIND):
        return m
    raise ValueError(
        f"invalid mode {mode!r}; expected 'auto', "
        f"{PROVENANCE_AWARE!r}, or {PROVENANCE_BLIND!r}"
    )


# --------------------------------------------------------------------------- #
# DB helpers (mirror the sibling modules' backend handling)
# --------------------------------------------------------------------------- #
def _backend_of(conn: Any) -> str:
    """Resolve the backend ('postgresql' | 'sqlite') for a live connection."""
    declared = getattr(conn, "_backend", None)
    if declared:
        return "postgresql" if str(declared).lower().startswith(("postgre", "pg")) else "sqlite"
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    return "postgresql" if backend in ("postgresql", "postgres", "pg") else "sqlite"


def _q(conn: Any, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    return sql.replace("?", "%s") if _backend_of(conn) == "postgresql" else sql


def _scalar(row: Any, key: str) -> Any:
    """Single aliased column of a DB row, dict-cursor or positional.

    Mirrors the ``hasattr(row, "keys")`` probe used across the sibling modules:
    PG dict cursors expose the aliased ``key``; SQLite ``Row`` / positional tuples
    fall back to index 0 (each caller selects exactly one aliased column).
    """
    if row is None:
        return None
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row[0]


def _set_mode(conn: Any, assessment_id: int, mode: str) -> None:
    """Stamp the engine-resolved mode onto the assessment row.

    ``ingest.stage`` derives ``mode`` from provenance presence; when the caller
    forces an explicit ``mode`` the two can diverge, so the engine writes the
    authoritative resolved value back. ``integrity_assessments`` is the single
    mutable-lifecycle table (mode/status/verdict/risk_score), so this UPDATE is
    permitted (it is not one of the append-only findings tables).
    """
    sql = "UPDATE integrity_assessments SET mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    conn.execute(_q(conn, sql), (mode, assessment_id))
    conn.commit()


def _count_findings(conn: Any, assessment_id: int) -> int:
    """Total ``integrity_findings`` rows for an assessment (all scanners)."""
    sql = "SELECT COUNT(*) AS n FROM integrity_findings WHERE assessment_id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return int(_scalar(row, "n") or 0)


def _count_capabilities(conn: Any, assessment_id: int) -> int:
    """Total ``integrity_capabilities`` rows for an assessment."""
    sql = "SELECT COUNT(*) AS n FROM integrity_capabilities WHERE assessment_id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return int(_scalar(row, "n") or 0)


def _get_status(conn: Any, assessment_id: int) -> Optional[str]:
    """Current lifecycle ``status`` of an assessment row."""
    sql = "SELECT status FROM integrity_assessments WHERE id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return _scalar(row, "status")


# --------------------------------------------------------------------------- #
# Gate config
# --------------------------------------------------------------------------- #
def _load_config() -> dict:
    """Load integrity_config.yaml; tolerate a missing/empty file with defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _gate_verdicts(cfg: Optional[dict] = None) -> tuple[set[str], set[str]]:
    """``(block_on, warn_on)`` verdict sets from config (lowercased).

    Reads ``gate.block_on`` / ``gate.warn_on`` from ``integrity_config.yaml``,
    falling back to ``block on QUARANTINE`` / ``warn on REVIEW`` when absent.
    """
    cfg = cfg if cfg is not None else _load_config()
    gate = (cfg or {}).get("gate") or {}
    block = gate.get("block_on")
    warn = gate.get("warn_on")
    block_set = {str(v).strip().lower() for v in (block if block is not None else _DEFAULT_GATE_BLOCK_ON)}
    warn_set = {str(v).strip().lower() for v in (warn if warn is not None else _DEFAULT_GATE_WARN_ON)}
    return block_set, warn_set


def gate_exit_code(verdict: str, cfg: Optional[dict] = None) -> int:
    """Map a verdict to a ``--gate`` exit code.

    ``GATE_BLOCK`` (1) when ``verdict`` is in ``gate.block_on`` (QUARANTINE by
    default, and REVIEW too when the operator adds it to ``block_on``);
    ``GATE_OK`` (0) otherwise. Verdict matching is case-insensitive.
    """
    block_set, _warn = _gate_verdicts(cfg)
    return GATE_BLOCK if str(verdict).strip().lower() in block_set else GATE_OK


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def assess(
    source: str,
    mode: str = "auto",
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    declared_purpose: Optional[str] = None,
    conn: Any = None,
) -> dict:
    """Run a full SIPA assessment of ``source`` and return its disposition.

    Orchestrates the SIPA pipeline end-to-end against a single RLS-aware
    connection, persisting at each step:

      1. **Ingest** — ``ingest.stage`` quarantines the artifact and writes the
         ``integrity_assessments`` row (``status='quarantine'``).
      2. **Tamper re-baseline** — ``ingest.reverify`` records a ``tamper_mismatch``
         finding if the staged tree changed between staging and assessment
         (best-effort; a reverify hiccup never aborts the assessment).
      3. **Scanners** — ``scanners.scan_all`` fans out the enabled static scanners
         (SAST / secrets / deps / malicious-signature) -> ``integrity_findings``.
      4. **Capabilities** — ``capability_extractor.extract`` builds the AST
         behavioral manifest and persists it -> ``integrity_capabilities``.
      5. **Reconciliation** — ``provenance_blind`` runs Mode B (``claim_parser`` +
         ``reconcile_and_persist`` -> ``undisclosed_capability``);
         ``provenance_aware`` runs Mode A (``reconcile_aware_and_persist`` -> RTM
         ``unauthorized_capability`` + coverage gaps). Both append to
         ``integrity_findings``.
      6. **Scoring** — ``scoring.score`` combines every signal into a 0–100
         ``risk_score`` + verdict and transitions the assessment to ``'assessed'``.

    Args:
        source: local path, UNC share, ``file://`` URI, or an allowlisted git
            clone URL (see ``ingest.stage``).
        mode: ``'auto'`` (default), ``'provenance_aware'``, or
            ``'provenance_blind'`` (see :func:`resolve_mode`).
        project_id / session_id: provenance handles; their presence selects
            ``provenance_aware`` under ``mode='auto'``.
        declared_purpose: optional free-text purpose claim folded into the Mode B
            claimed-capability set (e.g. a marketplace blurb / ticket summary).
        conn: optional existing DB connection to reuse (engine / tests); when
            ``None`` an RLS-aware connection is opened and closed internally.

    Returns:
        ``{"assessment_id", "verdict", "risk_score", "findings_count",
        "capabilities_count", "status"}``. ``verdict`` is the canonical lowercase
        token (``constants.VERDICTS``); ``status`` is ``'assessed'`` on success.

    Raises:
        ingest.IngestRejected: the source was refused at ingest (disallowed
            scheme/host, missing path, failed clone). Propagated so the caller
            learns the artifact never entered assessment.
        ValueError: an invalid ``mode``.

    Never executes the target — every stage is static-only (copy/clone, isolated
    scanner subprocesses, AST parsing).
    """
    resolved_mode = resolve_mode(mode, project_id, session_id)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS

        # 1. Ingest — quarantine-first staging + assessment row.
        staged = ingest.stage(
            source,
            project_id=project_id,
            session_id=session_id,
            conn=conn,
        )
        assessment_id = staged["assessment_id"]
        staged_path = staged["staged_path"]
        logger.info(
            "assess: staged %s -> assessment %s (%s) at %s",
            source, assessment_id, resolved_mode, staged_path,
        )

        # Stamp the engine-resolved mode (ingest derived it from provenance; an
        # explicit mode override must win).
        _set_mode(conn, assessment_id, resolved_mode)

        # 2. Tamper re-baseline — writes a tamper_mismatch finding iff changed.
        #    Best-effort: the assessment proceeds even if reverify cannot run.
        try:
            ingest.reverify(assessment_id, conn=conn)
        except Exception as exc:  # noqa: BLE001 — never let tamper-check abort assessment
            logger.warning("assess: reverify failed for assessment %s: %s", assessment_id, exc)

        # 3. Scanners — SAST / secrets / deps / malicious-signature fan-out.
        scan_summary = scanners.scan_all(assessment_id, staged_path=staged_path, conn=conn)

        # 4. Capabilities — AST behavioral manifest (extract once, persist it).
        #    The in-memory manifest also feeds Mode B reconciliation below, so we
        #    extract a single time rather than via extract_and_persist (which would
        #    re-parse the whole tree).
        manifest = capability_extractor.extract(staged_path)
        capability_extractor._persist(conn, assessment_id, manifest)

        # 5. Reconciliation — capability manifest vs. intent.
        #    * provenance_blind: disclosed (claim) vs exercised (manifest) ->
        #      undisclosed_capability findings.
        #    * provenance_aware: RTM-derived ALLOWED set vs exercised (manifest) ->
        #      unauthorized_capability findings + requirement coverage gaps.
        reconciled = False
        allowed_capabilities: dict = {}
        if resolved_mode == PROVENANCE_BLIND:
            claim = claim_parser.parse_claim(staged_path, declared_purpose=declared_purpose)
            intent_reconciler.reconcile_and_persist(assessment_id, manifest, claim, conn=conn)
            reconciled = True
        elif resolved_mode == PROVENANCE_AWARE:
            # Best-effort: a missing RTM / intake_requirements table must never abort
            # the assessment (mirrors the reverify guard above). The reconciler
            # already degrades to "nothing authorized" on a missing requirements
            # table, so this guard only covers unexpected RTM-build failures.
            try:
                aware_summary = intent_reconciler.reconcile_aware_and_persist(
                    assessment_id, manifest, project_id, session_id, conn=conn
                )
                # The Mode A allowed-capability -> requirement map feeds the
                # provenance edges (code -> capability -> requirement).
                allowed_capabilities = aware_summary.get("allowed_capabilities") or {}
                reconciled = True
            except Exception as exc:  # noqa: BLE001 — reconciliation is non-fatal to assess
                logger.warning(
                    "assess: provenance-aware reconciliation failed for %s: %s",
                    assessment_id, exc,
                )

        # 6. Scoring — combine every signal into a risk score + verdict.
        score_result = scoring.score(assessment_id, conn=conn)

        # 7. Provenance linkage — record the assessment + verdict in the PROV graph
        #    (report wasGeneratedBy assessment) and the source-citation registry with
        #    a trust score; Mode A also links authorized capabilities to their
        #    requirement_id. Best-effort: a provenance hiccup never aborts the
        #    assessment — the verdict already stands on the integrity_verdicts row.
        prov_result = None
        try:
            prov_result = provenance.record_assessment_provenance(
                assessment_id,
                staged.get("dir_digest"),
                score_result["verdict"],
                score_result["risk_score"],
                resolved_mode,
                project_id=project_id,
                allowed_capabilities=allowed_capabilities,
                conn=conn,
            )
        except Exception as exc:  # noqa: BLE001 — provenance is non-fatal to assess
            logger.warning(
                "assess: provenance linkage failed for assessment %s: %s",
                assessment_id, exc,
            )

        findings_count = _count_findings(conn, assessment_id)
        capabilities_count = _count_capabilities(conn, assessment_id)
        status = _get_status(conn, assessment_id)
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "assess: assessment %s -> %s (%.1f) | %d finding(s), %d capability(ies), mode=%s%s",
        assessment_id, score_result["verdict"], score_result["risk_score"],
        findings_count, capabilities_count, resolved_mode,
        " (reconciled)" if reconciled else "",
    )

    # DIC Canvas Synergy — emit SIPA events (dsyn-emit-05)
    if score_result["risk_score"] >= 70 and findings_count > 0:
        try:
            from tools.integrity.event_emitter import emit_vulnerability_found
            emit_vulnerability_found(
                file_path=source,
                finding_type=score_result.get("verdict", "high_risk"),
                severity="high" if score_result["risk_score"] >= 70 else "critical",
                assessment_id=str(assessment_id),
                project_id=project_id or "",
            )
        except Exception:
            pass  # event emission never blocks assessment result

    if status == "quarantine":
        try:
            from tools.integrity.event_emitter import emit_quarantine_triggered
            emit_quarantine_triggered(
                file_path=source,
                assessment_id=str(assessment_id),
                reason=score_result.get("verdict", "quarantined"),
                project_id=project_id or "",
            )
        except Exception:
            pass

    return {
        "assessment_id": assessment_id,
        "verdict": score_result["verdict"],
        "risk_score": score_result["risk_score"],
        "findings_count": findings_count,
        "capabilities_count": capabilities_count,
        "status": status,
        "mode": resolved_mode,
        "scanners": scan_summary.get("scanners", {}),
        "provenance": prov_result,
    }


# --------------------------------------------------------------------------- #
# HITL — promote / reject (the only path out of quarantine)
# --------------------------------------------------------------------------- #
# Terminal lifecycle states: once an assessment is approved or rejected its
# disposition is final and neither promote nor reject may act on it again.
_TERMINAL_STATUS = ("approved", "rejected")

_AUTH_INSERT_SQL = (
    "INSERT INTO integrity_authorizations "
    "(assessment_id, authorized, reason, reviewed_by, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _set_status(conn: Any, assessment_id: int, status: str) -> None:
    """Transition the assessment lifecycle ``status`` (the only mutable column set).

    ``integrity_assessments`` is the single mutable-lifecycle table; the findings
    tables are append-only. This UPDATE is the HITL transition out of the
    assessed/quarantine state into the terminal approved/rejected disposition.
    """
    sql = "UPDATE integrity_assessments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    conn.execute(_q(conn, sql), (status, assessment_id))
    conn.commit()


def _insert_authorization(conn: Any, assessment_id: int, authorized: bool, reason: Optional[str],
                          reviewed_by: str) -> int:
    """Append a HITL decision to ``integrity_authorizations`` (append-only).

    The row records the whole-assessment authorization decision (a promote or a
    reject); ``capability_id`` / ``requirement_id`` / ``claim_ref`` stay NULL —
    those columns are for the capability-level Mode A authorization edges. Stamps
    ``tenant_id`` / ``classification`` from the active security context.
    """
    tenant_id, classification, _created_by = ingest._caller_context()
    params = (assessment_id, authorized, reason, reviewed_by, tenant_id, classification)
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_AUTH_INSERT_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(_AUTH_INSERT_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


def _decide(assessment_id: int, reviewed_by: str, reason: Optional[str], *,
            authorized: bool, conn: Any = None) -> dict:
    """Shared HITL transition for :func:`promote` / :func:`reject`.

    Loads the assessment, refuses if it is missing or already in a terminal state,
    flips ``status`` to the target disposition, appends an
    ``integrity_authorizations`` row, and writes the matching append-only
    ``audit_trail`` event. Returns an ``{"error": ...}`` dict (never raises) on a
    missing / already-decided assessment so route + CLI callers can branch on it.
    """
    target_status = "approved" if authorized else "rejected"
    event_type = "integrity_promoted" if authorized else "integrity_rejected"
    verb = "promote" if authorized else "reject"

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: tolerate a not-yet-initialized schema

        status = _get_status(conn, assessment_id)
        if status is None:
            return {"error": f"assessment not found: {assessment_id}"}
        if status in _TERMINAL_STATUS:
            return {"error": f"cannot {verb}: assessment already {status}", "status": status}

        _set_status(conn, assessment_id, target_status)
        authorization_id = _insert_authorization(
            conn, assessment_id, authorized, reason, reviewed_by,
        )

        # Append-only audit trail (NIST AU). Non-fatal: an audit hiccup must not
        # roll back the recorded HITL decision, so log_event swallows its own
        # errors and returns -1.
        try:
            from tools.audit.audit_logger import log_event

            audit_id = log_event(
                event_type=event_type,
                actor=reviewed_by,
                action=f"{verb}d integrity assessment {assessment_id}: {reason or '(no reason given)'}",
                details={
                    "assessment_id": assessment_id,
                    "authorization_id": authorization_id,
                    "status": target_status,
                    "reason": reason,
                },
                classification="CUI",
                conn=conn,
            )
        except Exception as exc:  # noqa: BLE001 — audit is best-effort, decision stands
            logger.warning("%s: audit log_event failed for assessment %s: %s", verb, assessment_id, exc)
            audit_id = -1
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "%s: assessment %s -> %s by %s (authorization %s, audit %s)",
        verb, assessment_id, target_status, reviewed_by, authorization_id, audit_id,
    )
    return {
        "success": True,
        "assessment_id": assessment_id,
        "status": target_status,
        "reviewed_by": reviewed_by,
        "reason": reason,
        "authorization_id": authorization_id,
        "audit_id": audit_id,
    }


def promote(assessment_id: int, reviewed_by: str, reason: Optional[str] = None,
            conn: Any = None) -> dict:
    """HITL-approve a quarantined assessment — the ONLY path to ``status='approved'``.

    Transitions ``integrity_assessments.status`` to ``'approved'``, appends an
    ``integrity_authorizations`` row (``authorized=True``) recording who cleared it
    and why, and writes an ``integrity_promoted`` ``audit_trail`` event. No engine
    code path other than this one sets ``status='approved'``, so quarantined
    external code can only become approved through an explicit human review.

    Args:
        assessment_id: the ``integrity_assessments`` row to promote.
        reviewed_by: the reviewer (ISSO / security officer) recorded on the
            authorization + audit rows.
        reason: optional free-text justification for the approval.
        conn: optional existing RLS-aware connection to reuse (engine / tests);
            when ``None`` one is opened and closed internally.

    Returns:
        ``{"success": True, "assessment_id", "status": "approved", "reviewed_by",
        "reason", "authorization_id", "audit_id"}`` on success, or
        ``{"error": ...}`` when the assessment is missing or already terminal.
    """
    return _decide(assessment_id, reviewed_by, reason, authorized=True, conn=conn)


def reject(assessment_id: int, reviewed_by: str, reason: Optional[str] = None,
           conn: Any = None) -> dict:
    """HITL-reject a quarantined assessment — transition to ``status='rejected'``.

    Mirror of :func:`promote`: flips ``integrity_assessments.status`` to
    ``'rejected'``, appends an ``integrity_authorizations`` row
    (``authorized=False``) with the reviewer + reason, and writes an
    ``integrity_rejected`` ``audit_trail`` event. A rejected assessment is
    terminal — it can be neither re-promoted nor re-rejected.

    Args:
        assessment_id: the ``integrity_assessments`` row to reject.
        reviewed_by: the reviewer recorded on the authorization + audit rows.
        reason: optional free-text justification for the rejection.
        conn: optional existing connection to reuse (see :func:`promote`).

    Returns:
        ``{"success": True, ..., "status": "rejected", ...}`` on success, or
        ``{"error": ...}`` when the assessment is missing or already terminal.
    """
    return _decide(assessment_id, reviewed_by, reason, authorized=False, conn=conn)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None, conn: Any = None) -> int:
    """SIPA engine CLI entrypoint.

    ``python tools/integrity/engine.py --source <path|git-url|unc|uri>
    [--mode auto|provenance_aware|provenance_blind] [--project-id ...]
    [--session-id ...] [--declared-purpose ...] [--gate] [--json]``

    ``--json`` emits the assessment summary as JSON; ``--gate`` turns the verdict
    into a CI / pre-merge exit code (non-zero on ``gate.block_on`` — QUARANTINE by
    default — zero otherwise) so it can fail a build.

    Args:
        argv: optional argument vector (defaults to ``sys.argv[1:]``); passed by
            tests so they can drive the CLI without touching the process args.
        conn: optional DB connection threaded into :func:`assess` (tests inject an
            in-memory SQLite connection); ``None`` lets ``assess`` open its own.

    Returns:
        The process exit code (also raised via ``SystemExit`` so the CLI / tests
        can assert on it): ``GATE_BLOCK`` (1) when ``--gate`` and the verdict is
        blocking, else ``GATE_OK`` (0). The positional ``source`` form is still
        accepted for backward compatibility with the sipa-engine-01 CLI.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="engine.py",
        description="SIPA assessment engine — orchestrate ingest -> scan -> "
        "capability -> (Mode B) reconcile -> score over an untrusted artifact and "
        "emit a risk score + ALLOW / REVIEW / QUARANTINE verdict. Static-only: the "
        "target is never executed.",
    )
    # --source is the canonical flag; a bare positional is accepted too (back-compat).
    parser.add_argument(
        "--source",
        dest="source_opt",
        default=None,
        help="local path / UNC share / file:// URI / git clone URL",
    )
    parser.add_argument(
        "source_pos",
        nargs="?",
        default=None,
        metavar="source",
        help="positional alias for --source (back-compat)",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", PROVENANCE_AWARE, PROVENANCE_BLIND],
        help="provenance strategy (default: auto)",
    )
    parser.add_argument("--project-id", default=None, help="provenance handle (selects aware under auto)")
    parser.add_argument("--session-id", default=None, help="provenance handle (selects aware under auto)")
    parser.add_argument(
        "--declared-purpose",
        default=None,
        help="free-text purpose claim folded into the Mode B claimed-capability set",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero on a blocking verdict (gate.block_on; QUARANTINE by default) for CI / pre-merge",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    source = args.source_opt or args.source_pos
    if not source:
        parser.error("a source is required (--source <path|git-url|unc|uri>)")

    result = assess(
        source,
        mode=args.mode,
        project_id=args.project_id,
        session_id=args.session_id,
        declared_purpose=args.declared_purpose,
        conn=conn,
    )

    verdict = str(result["verdict"]).lower()
    block_set, warn_set = _gate_verdicts()

    if args.json:
        payload = dict(result)
        if args.gate:
            payload["gate"] = {
                "blocked": verdict in block_set,
                "warned": verdict in warn_set,
                "exit_code": GATE_BLOCK if verdict in block_set else GATE_OK,
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"SIPA assessment {result['assessment_id']} — {result['mode']}")
        print(f"  verdict:      {result['verdict'].upper()}")
        print(f"  risk_score:   {result['risk_score']}")
        print(f"  findings:     {result['findings_count']}")
        print(f"  capabilities: {result['capabilities_count']}")
        print(f"  status:       {result['status']}")

    code = GATE_OK
    if args.gate:
        if verdict in block_set:
            code = GATE_BLOCK
            print(f"GATE: BLOCKED - verdict {verdict.upper()} is blocking", file=sys.stderr)
        elif verdict in warn_set:
            print(f"GATE: WARN - verdict {verdict.upper()} (non-blocking)", file=sys.stderr)

    sys.exit(code)


if __name__ == "__main__":
    main()
