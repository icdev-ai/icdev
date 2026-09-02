#!/usr/bin/env python3
# CUI // SP-CTI
"""ATO package builder — the ONE packager, generalised to take any system.

``POST /api/ato-package/generate`` answered **501 not_implemented** because it
imported ``tools.compliance.ato_packager.generate_package`` and this module did
not exist. Meanwhile ``tools/agentic_ai_canvas/accred_package.py`` had a
WORKING package builder that assembled governance artifacts into a ZIP with a
cover sheet — it just could only ever describe an AADC *design*.

So this module is that builder with the subject lifted out:

    build_package_zip(subject, artifacts, title=...)   the generalised primitive
    generate_package(project_id, ...)                  the ATO caller
    build_accred_zip(...)                              the AADC caller (delegates)

There is deliberately only one implementation of the zip mechanics and one
cover-sheet renderer. A second packager forking is the defect this replaces.

The collectors below are the SAME functions the ``/api/ato-package/*`` GET
routes serve — they were moved out of ``tools/dashboard/api/ato_package.py``
rather than copied, so a package can never describe a readiness the dashboard
disagrees with.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from icdev.core.paths import repo_root

from tools.db.storage import get_connection, table_exists

BASE_DIR = repo_root(__file__)
DEFAULT_DB_PATH = BASE_DIR / "data" / "icdev.db"
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "ato_packages"

PACKAGE_TYPES = ("initial", "renewal", "cato")

PACKAGE_STEPS = [
    {"id": "ssp", "name": "System Security Plan", "required": True},
    {"id": "controls", "name": "Control Implementation", "required": True},
    {"id": "poam", "name": "Plan of Action & Milestones", "required": True},
    {"id": "sar", "name": "Security Assessment Report", "required": True},
    {"id": "stig", "name": "STIG Checklist", "required": True},
    {"id": "sbom", "name": "Software Bill of Materials", "required": True},
    {"id": "evidence", "name": "Evidence Collection", "required": True},
    {"id": "boundary", "name": "System Boundary Diagram", "required": False},
    {"id": "contingency", "name": "Contingency Plan", "required": False},
    {"id": "incident", "name": "Incident Response Plan", "required": False},
]


# ===========================================================================
# The generalised package primitive
# ===========================================================================


@dataclass
class PackageArtifact:
    """One file inside a package.

    ``data`` may be a JSON-serialisable object (written as indented JSON) or a
    ``str``/``bytes`` (written verbatim). ``description`` is what the cover
    sheet's contents table says about it — an artifact nobody can identify is
    evidence nobody will read.
    """

    name: str
    data: Any
    description: str = ""


@dataclass
class PackageSubject:
    """The system a package is ABOUT. Any system — not an AADC design.

    ``metadata`` is an ordered list of ``(label, value)`` rows rendered into the
    cover sheet header, so a caller states its own domain vocabulary rather
    than inheriting one.
    """

    id: str
    name: str = ""
    classification: str = "CUI"
    id_label: str = "System ID"
    metadata: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def coerce(cls, subject) -> "PackageSubject":
        if isinstance(subject, cls):
            return subject
        data = dict(subject or {})
        meta = data.get("metadata") or []
        if isinstance(meta, dict):
            meta = list(meta.items())
        return cls(
            id=str(data.get("id", "unknown")),
            name=str(data.get("name", "") or data.get("id", "unknown")),
            classification=str(data.get("classification") or "CUI"),
            id_label=str(data.get("id_label") or "System ID"),
            metadata=[(str(k), str(v)) for k, v in meta],
        )


def build_package_zip(
    subject,
    artifacts: Sequence[PackageArtifact],
    *,
    title: str = "Compliance Package",
    metrics: Iterable[tuple[str, Any]] | None = None,
    gaps: Sequence[str] | None = None,
    actions: Sequence[str] | None = None,
    generator: str = "ICDEV™ Compliance Packager",
    generated_at: str | None = None,
    cover_name: str = "README.md",
) -> bytes:
    """Assemble ``artifacts`` into a ZIP with a generated cover sheet.

    Returns raw ZIP bytes — write them to an HTTP response or to disk. The
    caller owns *what* goes in; this owns *how* a package is shaped.
    """
    subj = PackageSubject.coerce(subject)
    now = generated_at or datetime.now(timezone.utc).isoformat()

    cover = render_cover_sheet(
        subj,
        artifacts,
        title=title,
        metrics=metrics,
        gaps=gaps,
        actions=actions,
        generator=generator,
        now=now,
        cover_name=cover_name,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(cover_name, cover)
        for artifact in artifacts:
            zf.writestr(artifact.name, _serialise(artifact.data))
    return buf.getvalue()


def _serialise(data: Any) -> str | bytes:
    """JSON for structured data; verbatim for text and bytes."""
    if isinstance(data, (str, bytes)):
        return data
    return json.dumps(data, indent=2, default=str)


def render_cover_sheet(
    subject,
    artifacts: Sequence[PackageArtifact],
    *,
    title: str,
    metrics: Iterable[tuple[str, Any]] | None = None,
    gaps: Sequence[str] | None = None,
    actions: Sequence[str] | None = None,
    generator: str = "ICDEV™ Compliance Packager",
    now: str | None = None,
    cover_name: str = "README.md",
) -> str:
    """Render the package cover sheet as markdown, with classification banners."""
    subj = PackageSubject.coerce(subject)
    now = now or datetime.now(timezone.utc).isoformat()
    cls = subj.classification

    header_rows = [
        f"**Generated:** {now[:19]} UTC",
        f"**Classification:** {cls}",
        f"**{subj.id_label}:** {subj.id}",
    ]
    header_rows += [f"**{label}:** {value}" for label, value in subj.metadata]

    parts = [
        f"# {cls}",
        f"# {title} — {subj.name or subj.id}",
        "",
        "\n".join(header_rows),
        "",
        "---",
        "",
    ]

    metric_rows = list(metrics or [])
    if metric_rows:
        parts += [
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            *[f"| {label} | {value} |" for label, value in metric_rows],
            "",
            "---",
            "",
        ]

    parts += [
        "## Package Contents",
        "",
        "| File | Description |",
        "|------|-------------|",
        f"| `{cover_name}` | This cover sheet |",
        *[f"| `{a.name}` | {a.description or 'Package artifact'} |" for a in artifacts],
        "",
        "---",
        "",
        "## Critical Gaps",
        "",
        "\n".join(f"- {g}" for g in (gaps or [])) or "- None identified",
        "",
        "## Recommended Actions",
        "",
        "\n".join(f"{i + 1}. {a}" for i, a in enumerate(actions or []))
        or "1. No critical actions required",
        "",
        "---",
        "",
        f"*Generated by {generator}*",
        f"*{cls}*",
        "",
    ]
    return "\n".join(parts)


# ===========================================================================
# Backend helpers — moved verbatim from tools/dashboard/api/ato_package.py
# ===========================================================================


def _is_pg(conn) -> bool:
    """True when the connection speaks PostgreSQL."""
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _scalar(row):
    """Extract a scalar from a row regardless of backend (tuple or dict)."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return list(row.values())[0]
    return row[0]


def _count(conn, query, params=()):
    """Execute a COUNT query and return the scalar integer result."""
    return _scalar(conn.execute(query, params).fetchone())


def _date_now_expr(conn) -> str:
    """The SQL expression for 'current date' per backend."""
    return "CURRENT_DATE" if _is_pg(conn) else "DATE('now')"


def _instr_expr(conn, col, char) -> str:
    """INSTR/POSITION expression per backend."""
    if _is_pg(conn):
        return f"POSITION('{char}' IN {col})"
    return f"INSTR({col}, '{char}')"


class PGCompatConnection:
    """Silently pre-translate ``?`` → ``%s`` for PG so translate_sql never warns."""

    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"

    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql

    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def open_connection(db_path: str | None = None):
    """Return a package-ready connection to the compliance database."""
    conn = get_connection(db_path=str(db_path or DEFAULT_DB_PATH))
    return PGCompatConnection(conn)


# ===========================================================================
# Evidence collectors — one implementation, read by the API and the packager
# ===========================================================================


def check_step_status(conn, step_id, project_id):
    """Status for a single package step. Returns ``(status, detail)``."""
    where_project = " WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    if step_id == "ssp":
        if not table_exists(conn, "ssp_documents"):
            return "incomplete", "SSP table not found"
        q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
        if project_id:
            q += " WHERE project_id = ? AND status = 'approved'"
        else:
            q += " WHERE status = 'approved'"
        approved = _count(conn, q, params)
        if approved > 0:
            return "complete", f"{approved} approved SSP document(s)"
        total_q = "SELECT COUNT(*) AS cnt FROM ssp_documents" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "warning", f"{total} SSP document(s) but none approved"
        return "incomplete", "No SSP documents found"

    elif step_id == "controls":
        if not table_exists(conn, "project_controls"):
            return "incomplete", "Controls table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM project_controls" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No controls assigned"
        impl_q = "SELECT COUNT(*) AS cnt FROM project_controls"
        if project_id:
            impl_q += " WHERE project_id = ? AND implementation_status IN ('implemented', 'not_applicable')"
        else:
            impl_q += " WHERE implementation_status IN ('implemented', 'not_applicable')"
        implemented = _count(conn, impl_q, params)
        pct = (implemented / total) * 100 if total > 0 else 0
        if pct >= 80:
            return "complete", f"{implemented}/{total} controls implemented ({pct:.0f}%)"
        elif pct >= 50:
            return "warning", f"{implemented}/{total} controls implemented ({pct:.0f}%) — need 80%"
        return "incomplete", f"{implemented}/{total} controls implemented ({pct:.0f}%) — need 80%"

    elif step_id == "poam":
        if not table_exists(conn, "poam_items"):
            return "incomplete", "POAM table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM poam_items" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} POAM item(s) documented"
        return "incomplete", "No POAM items — document due diligence"

    elif step_id == "sar":
        if not table_exists(conn, "cato_evidence"):
            return "incomplete", "Evidence table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} assessment evidence record(s)"
        return "incomplete", "No assessment evidence collected"

    elif step_id == "stig":
        if not table_exists(conn, "stig_findings"):
            return "incomplete", "STIG findings table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM stig_findings" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No STIG findings recorded"
        cat1_q = "SELECT COUNT(*) AS cnt FROM stig_findings"
        if project_id:
            cat1_q += " WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'"
        else:
            cat1_q += " WHERE severity = 'CAT1' AND status = 'Open'"
        cat1_open = _count(conn, cat1_q, params)
        if cat1_open > 0:
            return "warning", f"{cat1_open} CAT1 Open finding(s) — must remediate"
        return "complete", f"{total} findings recorded, 0 CAT1 Open"

    elif step_id == "sbom":
        if not table_exists(conn, "sbom_records"):
            return "incomplete", "SBOM table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM sbom_records" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            return "complete", f"{total} SBOM record(s) on file"
        return "incomplete", "No SBOM records generated"

    elif step_id == "evidence":
        if not table_exists(conn, "cato_evidence"):
            return "incomplete", "Evidence table not found"
        total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total == 0:
            return "incomplete", "No evidence collected"
        current_q = "SELECT COUNT(*) AS cnt FROM cato_evidence"
        if project_id:
            current_q += " WHERE project_id = ? AND status = 'current'"
        else:
            current_q += " WHERE status = 'current'"
        current = _count(conn, current_q, params)
        pct = (current / total) * 100 if total > 0 else 0
        if pct >= 50:
            return "complete", f"{current}/{total} evidence items current ({pct:.0f}%)"
        return "warning", f"{current}/{total} evidence items current ({pct:.0f}%) — need 50%"

    elif step_id == "boundary":
        if not table_exists(conn, "ssp_documents"):
            return "incomplete", "No boundary diagram available"
        q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
        if project_id:
            q += " WHERE project_id = ? AND system_boundary IS NOT NULL AND system_boundary != ''"
        else:
            q += " WHERE system_boundary IS NOT NULL AND system_boundary != ''"
        count = _count(conn, q, params)
        if count > 0:
            return "complete", "System boundary diagram attached"
        return "incomplete", "No system boundary diagram"

    elif step_id == "contingency":
        if not table_exists(conn, "project_controls"):
            return "incomplete", "No contingency plan controls"
        q = "SELECT COUNT(*) AS cnt FROM project_controls"
        like_params = list(params) + ["CP-%"]
        if project_id:
            q += " WHERE project_id = ? AND control_id LIKE ?"
        else:
            q += " WHERE control_id LIKE ?"
        count = _count(conn, q, tuple(like_params))
        if count > 0:
            return "complete", f"{count} contingency (CP) controls mapped"
        return "incomplete", "No contingency plan controls mapped"

    elif step_id == "incident":
        if not table_exists(conn, "project_controls"):
            return "incomplete", "No incident response controls"
        q = "SELECT COUNT(*) AS cnt FROM project_controls"
        like_params = list(params) + ["IR-%"]
        if project_id:
            q += " WHERE project_id = ? AND control_id LIKE ?"
        else:
            q += " WHERE control_id LIKE ?"
        count = _count(conn, q, tuple(like_params))
        if count > 0:
            return "complete", f"{count} incident response (IR) controls mapped"
        return "incomplete", "No incident response controls mapped"

    return "incomplete", "Unknown step"


def collect_readiness(conn, project_id, steps=None):
    """Readiness across every package step.

    ``readiness_pct`` is ``None`` — never 0 and never 100 — when no required
    step was assessed. A score over an empty denominator is the defect
    ``args/perfect_score_gate.yaml`` exists to refuse: it closes the question
    a missing number would have left open.
    """
    steps = PACKAGE_STEPS if steps is None else steps
    results = []
    required_total = 0
    required_complete = 0
    all_complete = 0

    for step in steps:
        status, detail = check_step_status(conn, step["id"], project_id)
        results.append(
            {
                "id": step["id"],
                "name": step["name"],
                "required": step["required"],
                "status": status,
                "detail": detail,
            }
        )
        if step["required"]:
            required_total += 1
            if status == "complete":
                required_complete += 1
        if status == "complete":
            all_complete += 1

    readiness_pct = (
        round(required_complete / required_total * 100, 1) if required_total > 0 else None
    )

    return {
        "project_id": project_id,
        "steps": results,
        "readiness_pct": readiness_pct,
        "required_complete": required_complete,
        "required_total": required_total,
        "total_complete": all_complete,
        "total_steps": len(steps),
    }


def collect_ssp_documents(conn, project_id):
    """SSP document rows for a project (or every project when unscoped)."""
    if not table_exists(conn, "ssp_documents"):
        return {"ssp_documents": [], "message": "Table not found"}

    cols = (
        "SELECT id, project_id, version, system_name, system_boundary, "
        "authorization_type, status, approved_by, approved_at, "
        "classification, created_at FROM ssp_documents"
    )
    if project_id:
        rows = conn.execute(
            cols + " WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        ).fetchall()
    else:
        rows = conn.execute(cols + " ORDER BY created_at DESC").fetchall()
    return {"ssp_documents": [dict(r) for r in rows]}


def collect_controls_summary(conn, project_id):
    """Control implementation totals grouped by NIST family."""
    if not table_exists(conn, "project_controls"):
        return {
            "families": [],
            "totals": {"total": 0, "implemented": 0, "partial": 0, "planned": 0},
        }

    base_where = " WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    instr_expr = _instr_expr(conn, "control_id", "-")
    q = (
        "SELECT "  # nosec B608 -- table/column names are internal constants, not user input
        f"  CASE WHEN {instr_expr} > 0 "
        f"    THEN SUBSTR(control_id, 1, {instr_expr} - 1) "
        "    ELSE control_id END AS family, "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN implementation_status IN ('implemented', 'not_applicable') THEN 1 ELSE 0 END) AS implemented, "
        "  SUM(CASE WHEN implementation_status = 'partial' THEN 1 ELSE 0 END) AS partial, "
        "  SUM(CASE WHEN implementation_status IN ('planned', 'not_implemented') THEN 1 ELSE 0 END) AS planned "
        "FROM project_controls" + base_where + " GROUP BY family ORDER BY family"
    )
    families = [dict(r) for r in conn.execute(q, params).fetchall()]

    return {
        "families": families,
        "totals": {
            "total": sum(f["total"] for f in families),
            "implemented": sum(f["implemented"] for f in families),
            "partial": sum(f["partial"] for f in families),
            "planned": sum(f["planned"] for f in families),
        },
    }


def collect_poam_summary(conn, project_id):
    """POA&M counts by severity and status, plus the overdue count."""
    if not table_exists(conn, "poam_items"):
        return {"by_severity": {}, "by_status": {}, "overdue": 0, "total": 0}

    base_where = " WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    q_sev = (
        "SELECT severity, COUNT(*) AS count FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
        + base_where
        + " GROUP BY severity"
    )
    by_severity = {r["severity"]: r["count"] for r in conn.execute(q_sev, params).fetchall()}

    q_status = (
        "SELECT status, COUNT(*) AS count FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
        + base_where
        + " GROUP BY status"
    )
    by_status = {r["status"]: r["count"] for r in conn.execute(q_status, params).fetchall()}

    date_now = _date_now_expr(conn)
    q_overdue = (
        "SELECT COUNT(*) AS cnt FROM poam_items"  # nosec B608 -- table/column names are internal constants, not user input
        + base_where
        + (" AND" if project_id else " WHERE")
        + f" milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
    )
    overdue = _count(conn, q_overdue, params)

    q_total = "SELECT COUNT(*) AS cnt FROM poam_items" + base_where  # nosec B608 -- table/column names are internal constants, not user input
    total = _count(conn, q_total, params)

    return {
        "by_severity": by_severity,
        "by_status": by_status,
        "overdue": overdue,
        "total": total,
    }


def collect_checklist(conn, project_id):
    """The pre-submission checklist: six PASS / WARN / FAIL verdicts."""
    checks = []
    where_project = " WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    # 1. No CAT1 Open STIGs
    if table_exists(conn, "stig_findings"):
        q = "SELECT COUNT(*) AS cnt FROM stig_findings"
        if project_id:
            q += " WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'"
        else:
            q += " WHERE severity = 'CAT1' AND status = 'Open'"
        cat1 = _count(conn, q, params)
        checks.append(
            {
                "name": "No CAT1 Open STIG Findings",
                "status": "PASS" if cat1 == 0 else "FAIL",
                "detail": f"{cat1} CAT1 Open finding(s)" if cat1 > 0 else "All CAT1 findings remediated",
            }
        )
    else:
        checks.append(
            {
                "name": "No CAT1 Open STIG Findings",
                "status": "WARN",
                "detail": "STIG findings table not found",
            }
        )

    # 2. SSP Approved
    if table_exists(conn, "ssp_documents"):
        q = "SELECT COUNT(*) AS cnt FROM ssp_documents"
        if project_id:
            q += " WHERE project_id = ? AND status = 'approved'"
        else:
            q += " WHERE status = 'approved'"
        approved = _count(conn, q, params)
        checks.append(
            {
                "name": "SSP Approved",
                "status": "PASS" if approved > 0 else "FAIL",
                "detail": f"{approved} approved SSP(s)" if approved > 0 else "No approved SSP document",
            }
        )
    else:
        checks.append({"name": "SSP Approved", "status": "FAIL", "detail": "SSP table not found"})

    # 3. Controls >= 80% implemented
    if table_exists(conn, "project_controls"):
        total_q = "SELECT COUNT(*) AS cnt FROM project_controls" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        impl_q = "SELECT COUNT(*) AS cnt FROM project_controls"
        if project_id:
            impl_q += " WHERE project_id = ? AND implementation_status IN ('implemented', 'not_applicable')"
        else:
            impl_q += " WHERE implementation_status IN ('implemented', 'not_applicable')"
        implemented = _count(conn, impl_q, params)
        pct = (implemented / total * 100) if total > 0 else 0
        checks.append(
            {
                "name": "Controls >= 80% Implemented",
                "status": "PASS" if pct >= 80 else "FAIL",
                "detail": f"{implemented}/{total} ({pct:.0f}%)",
            }
        )
    else:
        checks.append(
            {
                "name": "Controls >= 80% Implemented",
                "status": "FAIL",
                "detail": "Controls table not found",
            }
        )

    # 4. No critical/high POAMs overdue
    date_now = _date_now_expr(conn)
    if table_exists(conn, "poam_items"):
        q = "SELECT COUNT(*) AS cnt FROM poam_items"
        if project_id:
            q += f" WHERE project_id = ? AND severity IN ('critical', 'high') AND milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
        else:
            q += f" WHERE severity IN ('critical', 'high') AND milestone_date < {date_now} AND status NOT IN ('closed', 'completed', 'resolved')"
        overdue_crit = _count(conn, q, params)
        checks.append(
            {
                "name": "No Critical/High POAMs Overdue",
                "status": "PASS" if overdue_crit == 0 else "FAIL",
                "detail": f"{overdue_crit} overdue critical/high POAM(s)"
                if overdue_crit > 0
                else "No overdue critical/high POAMs",
            }
        )
    else:
        checks.append(
            {
                "name": "No Critical/High POAMs Overdue",
                "status": "WARN",
                "detail": "POAM table not found",
            }
        )

    # 5. Evidence fresh (>= 50% current)
    if table_exists(conn, "cato_evidence"):
        total_q = "SELECT COUNT(*) AS cnt FROM cato_evidence" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        if total > 0:
            current_q = "SELECT COUNT(*) AS cnt FROM cato_evidence"
            if project_id:
                current_q += " WHERE project_id = ? AND status = 'current'"
            else:
                current_q += " WHERE status = 'current'"
            current = _count(conn, current_q, params)
            pct = current / total * 100
            checks.append(
                {
                    "name": "Evidence Freshness >= 50%",
                    "status": "PASS" if pct >= 50 else "WARN",
                    "detail": f"{current}/{total} current ({pct:.0f}%)",
                }
            )
        else:
            checks.append(
                {
                    "name": "Evidence Freshness >= 50%",
                    "status": "WARN",
                    "detail": "No evidence records found",
                }
            )
    else:
        checks.append(
            {
                "name": "Evidence Freshness >= 50%",
                "status": "WARN",
                "detail": "Evidence table not found",
            }
        )

    # 6. SBOM current
    if table_exists(conn, "sbom_records"):
        total_q = "SELECT COUNT(*) AS cnt FROM sbom_records" + where_project  # nosec B608 -- table/column names are internal constants, not user input
        total = _count(conn, total_q, params)
        checks.append(
            {
                "name": "SBOM Current",
                "status": "PASS" if total > 0 else "FAIL",
                "detail": f"{total} SBOM record(s) on file" if total > 0 else "No SBOM generated",
            }
        )
    else:
        checks.append({"name": "SBOM Current", "status": "FAIL", "detail": "SBOM table not found"})

    return {
        "checks": checks,
        "all_pass": all(c["status"] == "PASS" for c in checks),
        "has_failures": any(c["status"] == "FAIL" for c in checks),
        "pass_count": sum(1 for c in checks if c["status"] == "PASS"),
        "total_checks": len(checks),
    }


# ===========================================================================
# The ATO package
# ===========================================================================


def _safe_component(value: str) -> str:
    """Reduce a caller-supplied id to something safe to put in a filename.

    ``project_id`` arrives from a POST body, so it must never reach the path
    join intact — ``../`` in a project id is a directory traversal.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value)).strip("._-")
    return cleaned or "unknown"


def _describe_system(conn, project_id):
    """Best available name and classification for the system being packaged.

    Falls back to the project id rather than inventing a name — a package
    headed "Unknown System" is less misleading than one headed with a guess.
    """
    name, classification = project_id or "unscoped", "CUI"
    if not table_exists(conn, "ssp_documents"):
        return name, classification
    q = "SELECT system_name, classification FROM ssp_documents"
    params = ()
    if project_id:
        q += " WHERE project_id = ?"
        params = (project_id,)
    q += " ORDER BY created_at DESC"
    try:
        row = conn.execute(q, params).fetchone()
    except Exception:
        return name, classification
    if row:
        row = dict(row)
        name = row.get("system_name") or name
        classification = row.get("classification") or classification
    return name, classification


def generate_package(
    project_id,
    package_type: str = "initial",
    output_dir=None,
    db_path=None,
    conn=None,
):
    """Assemble and write an ATO package for ``project_id``.

    Returns a dict describing what was written — including the readiness and
    checklist numbers the package asserts, so the caller never has to re-derive
    them from the ZIP.
    """
    if not project_id:
        raise ValueError("project_id is required")
    if package_type not in PACKAGE_TYPES:
        raise ValueError(
            f"Invalid package_type: {package_type}. Must be one of {', '.join(PACKAGE_TYPES)}"
        )

    owns_conn = conn is None
    conn = conn if conn is not None else open_connection(db_path)
    try:
        readiness = collect_readiness(conn, project_id)
        ssp = collect_ssp_documents(conn, project_id)
        controls = collect_controls_summary(conn, project_id)
        poam = collect_poam_summary(conn, project_id)
        checklist = collect_checklist(conn, project_id)
        system_name, classification = _describe_system(conn, project_id)
    finally:
        if owns_conn:
            conn.close()

    now = datetime.now(timezone.utc)
    slug = _safe_component(project_id)
    manifest = {
        "project_id": project_id,
        "package_type": package_type,
        "generated_at": now.isoformat(),
        "system_name": system_name,
        "classification": classification,
        "generator": "ICDEV™ ATO Package Builder",
        "steps_assessed": readiness["total_steps"],
    }

    artifacts = [
        PackageArtifact(f"readiness-{slug}.json", readiness, "ATO package readiness by step"),
        PackageArtifact(f"ssp-{slug}.json", ssp, "System Security Plan documents"),
        PackageArtifact(
            f"controls-summary-{slug}.json", controls, "NIST 800-53 control implementation by family"
        ),
        PackageArtifact(f"poam-summary-{slug}.json", poam, "Plan of Action & Milestones summary"),
        PackageArtifact(f"checklist-{slug}.json", checklist, "Pre-submission checklist verdicts"),
        PackageArtifact(f"manifest-{slug}.json", manifest, "Package provenance and generation metadata"),
    ]

    gaps = [
        f"{s['name']}: {s['detail']}"
        for s in readiness["steps"]
        if s["required"] and s["status"] != "complete"
    ]
    gaps += [
        f"{c['name']}: {c['detail']}" for c in checklist["checks"] if c["status"] == "FAIL"
    ]

    actions = [f"Close: {g}" for g in gaps] or []

    readiness_display = (
        f"{readiness['readiness_pct']}%"
        if readiness["readiness_pct"] is not None
        else "not assessed"
    )
    metrics = [
        ("Package Type", package_type),
        ("Readiness", readiness_display),
        ("Required Steps Complete", f"{readiness['required_complete']}/{readiness['required_total']}"),
        ("Pre-submission Checks Passed", f"{checklist['pass_count']}/{checklist['total_checks']}"),
        ("Controls Implemented", f"{controls['totals']['implemented']}/{controls['totals']['total']}"),
        ("Open POA&M Items", poam["total"]),
        ("Submission Ready", "✓ YES" if checklist["all_pass"] else "✗ NO"),
    ]

    blob = build_package_zip(
        subject={
            "id": project_id,
            "name": system_name,
            "classification": classification,
            "id_label": "Project ID",
            "metadata": [("System", system_name), ("Package Type", package_type)],
        },
        artifacts=artifacts,
        title="ATO Package",
        metrics=metrics,
        gaps=gaps,
        actions=actions,
        generator="ICDEV™ ATO Package Builder",
        generated_at=now.isoformat(),
    )

    out_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"ato-package-{slug}-{package_type}-{now.strftime('%Y%m%dT%H%M%SZ')}.zip"
    zip_path = out_dir / zip_name
    zip_path.write_bytes(blob)

    return {
        "project_id": project_id,
        "package_type": package_type,
        "system_name": system_name,
        "classification": classification,
        "generated_at": now.isoformat(),
        "zip_path": str(zip_path),
        "zip_name": zip_name,
        "size_bytes": len(blob),
        "artifacts": ["README.md"] + [a.name for a in artifacts],
        "readiness_pct": readiness["readiness_pct"],
        "required_complete": readiness["required_complete"],
        "required_total": readiness["required_total"],
        "checklist_passed": checklist["pass_count"],
        "checklist_total": checklist["total_checks"],
        "submission_ready": checklist["all_pass"],
        "gaps": gaps,
    }


def main():  # pragma: no cover - thin CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(description="Generate an ATO package for a project")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--package-type", default="initial", choices=list(PACKAGE_TYPES))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON")
    args = parser.parse_args()

    result = generate_package(
        project_id=args.project_id,
        package_type=args.package_type,
        output_dir=args.output_dir,
        db_path=args.db_path,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Wrote {result['zip_path']} ({result['size_bytes']} bytes)")
        print(f"Readiness: {result['readiness_pct']}  Submission ready: {result['submission_ready']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
