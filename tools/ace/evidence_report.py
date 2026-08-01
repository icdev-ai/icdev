# CUI // SP-CTI
"""ACE evidence report generator — ATO evidence chain for a co-worker instance.

Walks ``ace_audit_log``, ``ace_coworkers``, ``ace_instances``, and
``ace_artifacts`` for a given instance and renders a structured evidence
report with classification markings and NIST 800-53 control references.

Reuses ``tools.compliance.classification_manager`` for banners — never
hard-codes CUI/classification strings.

Usage (CLI)::

    python tools/ace/evidence_report.py --instance <id> --format ssp
    python tools/ace/evidence_report.py --instance <id> --format json
    python tools/ace/evidence_report.py --instance <id> --format markdown
    python tools/ace/evidence_report.py --instance latest --format ssp

Usage (library)::

    from icdev.tools.ace.evidence_report import generate
    report = generate(instance_id="inst-abc123", fmt="json")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.evidence_report")

_DB_ENV = "ICDEV_ACE_DB_URL"
_DEFAULT_CLASSIFICATION = "CUI"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(instance_id: str, fmt: str = "ssp") -> dict[str, Any] | str:
    """Generate an evidence report for *instance_id*.

    Args:
        instance_id: ACE instance ID (``inst-*``), or ``"latest"`` to select
            the most-recently created instance.
        fmt: Output format — ``"json"`` (dict), ``"ssp"`` (structured text),
            or ``"markdown"``.

    Returns:
        A dict if *fmt* is ``"json"``; a formatted string otherwise.

    Raises:
        ValueError: Instance not found.
    """
    data = _collect(instance_id)
    _write_generate_audit(data["instance"]["id"], data)
    if fmt == "json":
        return data
    if fmt == "markdown":
        return _render_markdown(data)
    return _render_ssp(data)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _write_generate_audit(
    instance_id: str,
    data: dict[str, Any],
    publish_meta: dict[str, Any] | None = None,
) -> None:
    """Append an ace_audit_log row recording that this report was generated.

    Generating an evidence report is itself an auditable act — it is the
    artifact an assessor is handed, so "who produced this, when, and over what"
    has to be answerable. Reading ace_audit_log without writing to it left that
    unanswered.

    This function was lost, not omitted: it was authored only in the
    ``icdev/tools/`` mirror, and ``sync_package_tree`` copies canonical
    ``tools/`` OVER the mirror, so the next release sync deleted it. Restored
    here in ``tools/`` — the canonical location — so a sync propagates it
    instead of destroying it.

    Non-blocking: exceptions are swallowed so an audit failure never breaks
    report generation.

    Args:
        publish_meta: Optional IDR publish context merged into the detail JSON
            (e.g. ``{"idr_session_id": "...", "formats": ["html", "docx"]}``)
            to link the entry back to the triggering publish event.
    """
    try:
        from icdev.tools.db.storage import get_canvas_connection, sql_placeholder

        summary = data.get("summary", {})
        detail_dict: dict[str, Any] = {
            "generated_at": data.get("generated_at"),
            "classification": data.get("classification"),
            "total_actions": summary.get("total_actions", 0),
            "total_artifacts": summary.get("total_artifacts", 0),
            "control_refs_count": summary.get("control_refs_count", 0),
        }
        if publish_meta:
            detail_dict["publish"] = publish_meta
        detail = json.dumps(detail_dict, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = get_canvas_connection(_DB_ENV)
        try:
            _ph = sql_placeholder(conn)
            conn.execute(
                "INSERT INTO ace_audit_log "
                "(instance_id, action, detail, actor, classification, created_at) "
                f"VALUES ({_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph})",
                (
                    instance_id,
                    "evidence_report.generate",
                    detail,
                    "evidence_report",
                    data.get("classification", _DEFAULT_CLASSIFICATION),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_write_generate_audit: best-effort INSERT into ace_audit_log failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _collect(instance_id: str) -> dict[str, Any]:
    """Load all evidence rows for the instance and return a structured dict."""
    from icdev.tools.db.storage import get_canvas_connection, sql_placeholder

    conn = get_canvas_connection(_DB_ENV)
    _ph = sql_placeholder(conn)
    try:
        # Resolve "latest"
        if instance_id == "latest":
            row = conn.execute(
                "SELECT id FROM ace_instances ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise ValueError("No ACE instances found")
            instance_id = row[0]

        # Instance metadata
        inst_row = conn.execute(
            f"SELECT id, name, role_id, state, trust_tier, created_at, completed_at "
            f"FROM ace_instances WHERE id = {_ph}",
            (instance_id,),
        ).fetchone()
        if not inst_row:
            raise ValueError(f"Instance not found: {instance_id!r}")
        instance = dict(zip(
            ("id", "name", "role_id", "state", "trust_tier", "created_at", "completed_at"),
            inst_row,
        ))

        # Coworkers
        cw_rows = conn.execute(
            "SELECT id, role_id, display_name, state, trust_tier, assigned_step, "
            "last_active_at, created_at "
            f"FROM ace_coworkers WHERE instance_id = {_ph} ORDER BY created_at",
            (instance_id,),
        ).fetchall()
        coworkers = [
            dict(zip(
                ("id", "role_id", "display_name", "state", "trust_tier",
                 "assigned_step", "last_active_at", "created_at"),
                r,
            ))
            for r in cw_rows
        ]

        # Audit trail
        audit_rows = conn.execute(
            "SELECT id, coworker_id, action, detail, actor, classification, "
            "control_refs, created_at "
            f"FROM ace_audit_log WHERE instance_id = {_ph} ORDER BY created_at",
            (instance_id,),
        ).fetchall()

        # Graceful fallback if control_refs column doesn't exist yet
        try:
            audit_entries = [
                dict(zip(
                    ("id", "coworker_id", "action", "detail", "actor",
                     "classification", "control_refs", "created_at"),
                    r,
                ))
                for r in audit_rows
            ]
        except Exception:
            audit_entries = []

        # Artifacts
        artifact_rows = conn.execute(
            "SELECT id, coworker_id, artifact_type, title, classification, created_at "
            f"FROM ace_artifacts WHERE instance_id = {_ph} ORDER BY created_at",
            (instance_id,),
        ).fetchall()
        artifacts = [
            dict(zip(
                ("id", "coworker_id", "artifact_type", "title", "classification", "created_at"),
                r,
            ))
            for r in artifact_rows
        ]

    finally:
        conn.close()

    # Derive overall classification (highest watermark across audit + artifacts)
    classification = _watermark_classification(
        [e.get("classification", _DEFAULT_CLASSIFICATION) for e in audit_entries]
        + [a.get("classification", _DEFAULT_CLASSIFICATION) for a in artifacts]
        + [instance.get("trust_tier", _DEFAULT_CLASSIFICATION)]
    )

    # Collect unique NIST control references
    control_refs: list[str] = sorted({
        ref.strip()
        for e in audit_entries
        for ref in (e.get("control_refs") or "").split(",")
        if ref.strip()
    })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classification": classification,
        "instance": instance,
        "coworkers": coworkers,
        "audit_trail": audit_entries,
        "artifacts": artifacts,
        "control_refs": control_refs,
        "summary": {
            "total_actions": len(audit_entries),
            "total_artifacts": len(artifacts),
            "coworker_count": len(coworkers),
            "control_refs_count": len(control_refs),
        },
    }


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _watermark_classification(values: list[str]) -> str:
    """Return the highest classification watermark across all values."""
    order = {
        "PUBLIC": 0,
        "CUI": 1,
        "ECI": 2,
        "SECRET": 3,
        "TOP SECRET": 4,
        "TOP SECRET//SCI": 5,
    }
    best = _DEFAULT_CLASSIFICATION
    best_rank = order.get(_DEFAULT_CLASSIFICATION, 1)
    for v in values:
        v = (v or "").upper().strip()
        rank = order.get(v, 1)
        if rank > best_rank:
            best = v
            best_rank = rank
    return best


def _get_banner(classification: str) -> dict[str, str]:
    """Return top/bottom classification banners via classification_manager."""
    try:
        from tools.compliance.classification_manager import get_document_banner

        return get_document_banner(classification)
    except Exception:
        # Fallback if classification_manager not available
        banner = f"// {classification} //"
        return {"banner_top": banner, "banner_bottom": banner}


# ---------------------------------------------------------------------------
# SSP text renderer
# ---------------------------------------------------------------------------


def _render_ssp(data: dict[str, Any]) -> str:
    classification = data["classification"]
    banners = _get_banner(classification)
    inst = data["instance"]
    summary = data["summary"]
    lines: list[str] = []

    lines.append(banners["banner_top"])
    lines.append("")
    lines.append("=" * 72)
    lines.append("ACE CO-WORKER ENGINE — ATO EVIDENCE REPORT")
    lines.append("=" * 72)
    lines.append(f"Generated   : {data['generated_at']}")
    lines.append(f"Classification: {classification}")
    lines.append("")

    # Instance section
    lines.append("SECTION 1 — INSTANCE METADATA")
    lines.append("-" * 40)
    lines.append(f"  Instance ID  : {inst['id']}")
    lines.append(f"  Name         : {inst.get('name', '')}")
    lines.append(f"  Role         : {inst.get('role_id', '')}")
    lines.append(f"  State        : {inst.get('state', '')}")
    lines.append(f"  Trust Tier   : {inst.get('trust_tier', '')}")
    lines.append(f"  Created      : {inst.get('created_at', '')}")
    lines.append(f"  Completed    : {inst.get('completed_at') or 'N/A'}")
    lines.append("")

    # Co-workers section
    lines.append("SECTION 2 — CO-WORKERS")
    lines.append("-" * 40)
    for cw in data["coworkers"]:
        lines.append(
            f"  [{cw['state'].upper():<14}] {cw.get('display_name') or cw['role_id']}"
            f" ({cw['id']})"
        )
        if cw.get("assigned_step"):
            lines.append(f"      Last step: {cw['assigned_step']}")
    if not data["coworkers"]:
        lines.append("  (none)")
    lines.append("")

    # Audit trail section
    lines.append("SECTION 3 — AUDIT TRAIL")
    lines.append("-" * 40)
    lines.append(f"  Total actions: {summary['total_actions']}")
    for entry in data["audit_trail"]:
        ctrl = f"  [controls: {entry['control_refs']}]" if entry.get("control_refs") else ""
        lines.append(
            f"  {entry.get('created_at', '')[:19]}  "
            f"{entry.get('action', ''):<30}  "
            f"{entry.get('actor', 'system'):<20}"
            f"{ctrl}"
        )
        if entry.get("detail"):
            lines.append(f"      detail: {entry['detail'][:100]}")
    if not data["audit_trail"]:
        lines.append("  (none)")
    lines.append("")

    # Artifacts section
    lines.append("SECTION 4 — ARTIFACTS")
    lines.append("-" * 40)
    lines.append(f"  Total artifacts: {summary['total_artifacts']}")
    for art in data["artifacts"]:
        lines.append(
            f"  {art.get('created_at', '')[:19]}  "
            f"{art.get('artifact_type', ''):<16}  "
            f"({art.get('classification', _DEFAULT_CLASSIFICATION)})"
            f"  {art.get('title', art['id'])[:60]}"
        )
    if not data["artifacts"]:
        lines.append("  (none)")
    lines.append("")

    # NIST control references
    lines.append("SECTION 5 — NIST 800-53 CONTROL REFERENCES")
    lines.append("-" * 40)
    if data["control_refs"]:
        for ref in data["control_refs"]:
            lines.append(f"  {ref}")
    else:
        lines.append("  (no control references recorded)")
    lines.append("")

    lines.append("=" * 72)
    lines.append(banners["banner_bottom"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_markdown(data: dict[str, Any]) -> str:
    classification = data["classification"]
    banners = _get_banner(classification)
    inst = data["instance"]
    summary = data["summary"]
    lines: list[str] = []

    lines.append(f"> **{banners['banner_top']}**")
    lines.append("")
    lines.append("# ACE Co-Worker Engine — ATO Evidence Report")
    lines.append("")
    lines.append(f"**Generated:** {data['generated_at']}  ")
    lines.append(f"**Classification:** `{classification}`")
    lines.append("")

    lines.append("## Instance Metadata")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Instance ID | `{inst['id']}` |")
    lines.append(f"| Name | {inst.get('name', '')} |")
    lines.append(f"| Role | {inst.get('role_id', '')} |")
    lines.append(f"| State | {inst.get('state', '')} |")
    lines.append(f"| Trust Tier | {inst.get('trust_tier', '')} |")
    lines.append(f"| Created | {inst.get('created_at', '')} |")
    lines.append(f"| Completed | {inst.get('completed_at') or 'N/A'} |")
    lines.append("")

    lines.append("## Summary")
    lines.append(f"- Actions: **{summary['total_actions']}**")
    lines.append(f"- Artifacts: **{summary['total_artifacts']}**")
    lines.append(f"- Co-workers: **{summary['coworker_count']}**")
    lines.append(f"- NIST controls referenced: **{summary['control_refs_count']}**")
    lines.append("")

    if data["control_refs"]:
        lines.append("## NIST 800-53 Control References")
        lines.append(", ".join(f"`{r}`" for r in data["control_refs"]))
        lines.append("")

    lines.append("## Audit Trail")
    lines.append("| Timestamp | Action | Actor | Controls |")
    lines.append("|-----------|--------|-------|----------|")
    for entry in data["audit_trail"]:
        ts = (entry.get("created_at") or "")[:19]
        action = entry.get("action", "")
        actor = entry.get("actor", "system")
        refs = entry.get("control_refs") or "—"
        lines.append(f"| {ts} | {action} | {actor} | {refs} |")
    lines.append("")

    lines.append(f"> **{banners['banner_bottom']}**")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ACE ATO Evidence Report Generator")
    parser.add_argument("--instance", required=True, help='Instance ID or "latest"')
    parser.add_argument(
        "--format",
        choices=["ssp", "json", "markdown"],
        default="ssp",
        help="Output format (default: ssp)",
    )
    parser.add_argument("--output", help="Write report to file (default: stdout)")
    args = parser.parse_args()

    try:
        report = generate(args.instance, fmt=args.format)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc

    if args.format == "json":
        output = json.dumps(report, indent=2, default=str)
    else:
        output = report

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    from pathlib import Path

    main()
