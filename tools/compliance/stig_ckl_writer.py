#!/usr/bin/env python3
# CUI // SP-CTI
####################################################################
# CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
# Distribution: Distribution D -- Authorized DoD Personnel Only
####################################################################
"""DISA STIG checklist EMITTER -- writes .ckl (STIG Viewer 2 XML) and
.cklb (STIG Viewer 3 JSON) from the ``stig_findings`` table.

The repo has been able to READ both dialects for a long time
(``tools/network/stig_import.py``) and has never been able to WRITE one, so a
STIG assessment recorded in ICDEV could not be handed back to an assessor in
the format every DoD reviewer actually opens.

ROUND-TRIP IS THE CONTRACT, and it is enforced by construction rather than by
a matching pair of hand-written tables. ``CAT_TO_SEVERITY`` and
``STATUS_FROM_NORMALIZED`` are INVERTED FROM the parser's own
``SEVERITY_TO_CAT`` / ``CKL_STATUS_MAP`` at import time. A second, independent
copy of either table would drift the first time DISA added a value, and the
emitter would then write a file its own parser silently downgrades --
``SEVERITY_TO_CAT.get(v, "CAT2")`` turns an unrecognised severity into CAT2
without raising, which is exactly the shape of defect that survives a green
test suite.

WHAT IS NOT ROUND-TRIPPED, and why. ``stig_findings`` has no
``finding_details`` column, so ``<FINDING_DETAILS>`` is written EMPTY rather
than filled from ``description`` or ``comments``: a checklist whose
finding-details field carries the rule's own discussion text reads to an
assessor as evidence that somebody looked, when nobody did. The field is
accepted from a caller-supplied finding dict and simply has no database
source today.

Usage:
    python tools/compliance/stig_ckl_writer.py --project-id proj-1 --format both
    python tools/compliance/stig_ckl_writer.py --project-id proj-1 --format ckl --json
"""

import argparse
import json
import sys
import uuid
import xml.etree.ElementTree as ET  # nosec B405 -- serialisation only; this module never parses XML
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from datetime import datetime, timezone

from icdev.core.paths import repo_root
from tools.db.storage import get_connection
from tools.network.stig_import import CKL_STATUS_MAP, SEVERITY_TO_CAT

# xit-decl-03: the ONE root resolver. A `parent.parent.parent` here would be a
# private, hard-coded claim about where this file sits -- true today and
# silently wrong the moment the module moves, which is exactly what the
# ICDEV[domain] split does to every kernel package.
BASE_DIR = repo_root(__file__)
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Vocabularies -- every one INVERTED from the parser, never restated
# ---------------------------------------------------------------------------

# {"CAT1": "high", "CAT2": "medium", "CAT3": "low"}
CAT_TO_SEVERITY = {cat: sev for sev, cat in SEVERITY_TO_CAT.items()}

# {"pass": "NotAFinding", "fail": "Open", "na": "Not_Applicable", "nr": "Not_Reviewed"}
STATUS_FROM_NORMALIZED = {norm: raw for raw, norm in CKL_STATUS_MAP.items()}

# The CKL wire literals, which are also the stig_findings.status CHECK values.
CKL_STATUS_VALUES = tuple(CKL_STATUS_MAP)

# CKL literal -> .cklb (STIG Viewer 3) literal. The two dialects spell the same
# four states differently; cklb is lower-snake.
CKLB_STATUS = {
    "NotAFinding": "not_a_finding",
    "Open": "open",
    "Not_Applicable": "not_applicable",
    "Not_Reviewed": "not_reviewed",
}

# The STIG_DATA attribute order STIG Viewer 2 emits. Order is not semantically
# required by the parser, but a checklist that opens in a diff next to a
# DISA-produced one should not differ on field order alone.
_STIG_DATA_ORDER = (
    ("Vuln_Num", "finding_id"),
    ("Severity", "_severity_word"),
    ("Group_Title", "title"),
    ("Rule_ID", "rule_id"),
    ("Rule_Ver", "stig_id"),
    ("Rule_Title", "title"),
    ("Vuln_Discuss", "description"),
    ("Check_Content", "check_content"),
    ("Fix_Text", "fix_text"),
)

_DEFAULT_ASSET = {
    "role": "None",
    "asset_type": "Computing",
    "host_name": "",
    "ip": "",
    "mac": "",
    "fqdn": "",
    "target_comment": "",
    "tech_area": "",
    "target_key": "",
    "web_or_database": False,
    "web_db_site": "",
    "web_db_instance": "",
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _severity_word(finding):
    """Return the DISA severity word ('high'/'medium'/'low') for a finding.

    Accepts either a CAT level ('CAT1') or an already-lowercase DISA word.
    An unrecognised value falls back to the CAT2 word, matching what the
    parser does on the way back in -- so the emitter and the parser agree on
    the degraded case instead of disagreeing about it.
    """
    raw = str(finding.get("severity") or "").strip()
    if raw in CAT_TO_SEVERITY:
        return CAT_TO_SEVERITY[raw]
    if raw.lower() in SEVERITY_TO_CAT:
        return raw.lower()
    return CAT_TO_SEVERITY.get("CAT2", "medium")


def _ckl_status(finding):
    """Return the CKL STATUS literal for a finding.

    Accepts a CKL literal ('Open') or a normalized parser value ('fail').
    Anything else is 'Not_Reviewed' -- an unknown status must never be written
    as NotAFinding, because that is the one value that silently clears a
    finding.
    """
    raw = str(finding.get("status") or "").strip()
    if raw in CKL_STATUS_MAP:
        return raw
    if raw in STATUS_FROM_NORMALIZED:
        return STATUS_FROM_NORMALIZED[raw]
    return "Not_Reviewed"


def _merge_asset(asset=None):
    """Overlay caller-supplied asset fields onto the STIG Viewer defaults."""
    merged = dict(_DEFAULT_ASSET)
    for key, value in (asset or {}).items():
        if key in merged:
            merged[key] = value
    return merged


def _resolve_stig_info(stig_info=None, findings=None):
    """Resolve the STIG_INFO header.

    ``title`` and ``version`` are the two keys the parser reads back, so they
    are always emitted. When the caller names neither, the title is derived
    from the findings' own ``stig_id`` values rather than invented.
    """
    info = dict(stig_info or {})
    if not info.get("title"):
        ids = sorted({str(f.get("stig_id") or "").strip() for f in (findings or [])} - {""})
        info["title"] = ids[0] if len(ids) == 1 else "ICDEV STIG Assessment"
    if not info.get("version"):
        info["version"] = "1"
    return info


# ---------------------------------------------------------------------------
# .ckl -- STIG Viewer 2 XML
# ---------------------------------------------------------------------------


def build_ckl(findings, asset=None, stig_info=None):
    """Build a DISA STIG Viewer 2 ``.ckl`` document as an XML string.

    Args:
        findings: Iterable of dicts using ``stig_findings`` column names
            (stig_id, finding_id, rule_id, severity, title, description,
            check_content, fix_text, status, comments).
        asset: Optional dict overlaying ``_DEFAULT_ASSET`` (host_name, ip, ...).
        stig_info: Optional dict with ``title`` / ``version``.

    Returns:
        The serialised XML, UTF-8 declaration included.
    """
    findings = list(findings or [])
    resolved_asset = _merge_asset(asset)
    info = _resolve_stig_info(stig_info, findings)

    checklist = ET.Element("CHECKLIST")

    asset_el = ET.SubElement(checklist, "ASSET")
    for tag, key in (
        ("ROLE", "role"),
        ("ASSET_TYPE", "asset_type"),
        ("HOST_NAME", "host_name"),
        ("HOST_IP", "ip"),
        ("HOST_MAC", "mac"),
        ("HOST_FQDN", "fqdn"),
        ("TARGET_COMMENT", "target_comment"),
        ("TECH_AREA", "tech_area"),
        ("TARGET_KEY", "target_key"),
    ):
        ET.SubElement(asset_el, tag).text = str(resolved_asset.get(key) or "")
    ET.SubElement(asset_el, "WEB_OR_DATABASE").text = "true" if resolved_asset.get("web_or_database") else "false"
    ET.SubElement(asset_el, "WEB_DB_SITE").text = str(resolved_asset.get("web_db_site") or "")
    ET.SubElement(asset_el, "WEB_DB_INSTANCE").text = str(resolved_asset.get("web_db_instance") or "")

    stigs_el = ET.SubElement(checklist, "STIGS")
    istig_el = ET.SubElement(stigs_el, "iSTIG")
    stig_info_el = ET.SubElement(istig_el, "STIG_INFO")
    for name in ("version", "title"):
        si = ET.SubElement(stig_info_el, "SI_DATA")
        ET.SubElement(si, "SID_NAME").text = name
        ET.SubElement(si, "SID_DATA").text = str(info.get(name) or "")

    for finding in findings:
        vuln_el = ET.SubElement(istig_el, "VULN")
        for attribute, source in _STIG_DATA_ORDER:
            if source == "_severity_word":
                value = _severity_word(finding)
            else:
                value = str(finding.get(source) or "")
            sd = ET.SubElement(vuln_el, "STIG_DATA")
            ET.SubElement(sd, "VULN_ATTRIBUTE").text = attribute
            ET.SubElement(sd, "ATTRIBUTE_DATA").text = value

        ET.SubElement(vuln_el, "STATUS").text = _ckl_status(finding)
        # Empty unless the caller supplied one: stig_findings has no
        # finding_details column, and borrowing description/comments here would
        # read to an assessor as evidence that somebody reviewed the rule.
        ET.SubElement(vuln_el, "FINDING_DETAILS").text = str(finding.get("finding_details") or "")
        ET.SubElement(vuln_el, "COMMENTS").text = str(finding.get("comments") or "")
        ET.SubElement(vuln_el, "SEVERITY_OVERRIDE").text = ""
        ET.SubElement(vuln_el, "SEVERITY_JUSTIFICATION").text = ""

    body = ET.tostring(checklist, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!--DISA STIG Viewer :: 2.18-->\n' + body + "\n"


# ---------------------------------------------------------------------------
# .cklb -- STIG Viewer 3 JSON
# ---------------------------------------------------------------------------


def build_cklb(findings, asset=None, stig_info=None):
    """Build a STIG Viewer 3 ``.cklb`` document as a dict.

    Same inputs as :func:`build_ckl`. The two emitters read the SAME finding
    dicts through the SAME ``_severity_word`` / ``_ckl_status`` normalisers, so
    a .ckl and a .cklb written from one assessment cannot disagree about a
    severity or a status.
    """
    findings = list(findings or [])
    resolved_asset = _merge_asset(asset)
    info = _resolve_stig_info(stig_info, findings)

    rules = []
    for finding in findings:
        ckl_status = _ckl_status(finding)
        rules.append(
            {
                "uuid": str(uuid.uuid4()),
                "group_id": str(finding.get("finding_id") or ""),
                "rule_id": str(finding.get("rule_id") or ""),
                "rule_version": str(finding.get("stig_id") or ""),
                "group_title": str(finding.get("title") or ""),
                "rule_title": str(finding.get("title") or ""),
                "severity": _severity_word(finding),
                "weight": "10.0",
                "classification": "Unclassified",
                "discussion": str(finding.get("description") or ""),
                "check_content": str(finding.get("check_content") or ""),
                "fix_text": str(finding.get("fix_text") or ""),
                "status": CKLB_STATUS[ckl_status],
                "overrides": {},
                "comments": str(finding.get("comments") or ""),
                "finding_details": str(finding.get("finding_details") or ""),
            }
        )

    return {
        "title": str(info.get("title") or ""),
        "id": str(uuid.uuid4()),
        "active": False,
        "mode": 2,
        "has_path": True,
        "target_data": {
            "target_type": str(resolved_asset.get("asset_type") or "Computing"),
            "host_name": str(resolved_asset.get("host_name") or ""),
            "ip_address": str(resolved_asset.get("ip") or ""),
            "mac_address": str(resolved_asset.get("mac") or ""),
            "fqdn": str(resolved_asset.get("fqdn") or ""),
            "comments": str(resolved_asset.get("target_comment") or ""),
            "role": str(resolved_asset.get("role") or "None"),
            "is_web_database": bool(resolved_asset.get("web_or_database")),
            "technology_area": str(resolved_asset.get("tech_area") or ""),
            "web_db_site": str(resolved_asset.get("web_db_site") or ""),
            "web_db_instance": str(resolved_asset.get("web_db_instance") or ""),
        },
        "stigs": [
            {
                "uuid": str(uuid.uuid4()),
                "stig_name": str(info.get("title") or ""),
                "display_name": str(info.get("title") or ""),
                "stig_id": str(info.get("stig_id") or info.get("title") or ""),
                "release_info": str(info.get("version") or ""),
                "version": str(info.get("version") or ""),
                "reference_identifier": str(info.get("reference_identifier") or ""),
                "size": len(rules),
                "rules": rules,
            }
        ],
        "cklb_version": "1.0",
    }


# ---------------------------------------------------------------------------
# Database-backed emission
# ---------------------------------------------------------------------------


def _get_connection(db_path=None):
    """Open a database connection, refusing a path that does not exist."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    return get_connection(db_path=str(path))


def get_project_findings(project_id, db_path=None, conn=None):
    """Load a project's ``stig_findings`` rows in checklist order."""
    owned = conn is None
    conn = conn or _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT stig_id, finding_id, rule_id, severity, title,
                      description, check_content, fix_text, status,
                      comments, target_type, assessed_by, assessed_at
               FROM stig_findings
               WHERE project_id = %s
               ORDER BY severity, finding_id""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def write_stig_checklist(
    project_id,
    output_dir=None,
    db_path=None,
    fmt="both",
    asset=None,
    stig_info=None,
):
    """Write a project's STIG findings as ``.ckl`` and/or ``.cklb``.

    Args:
        project_id: Project identifier.
        output_dir: Destination directory (default ``.tmp/compliance/<id>/stig``).
        db_path: Override database path.
        fmt: ``ckl`` | ``cklb`` | ``both``.
        asset: Optional asset overlay (host_name, ip, mac, fqdn, ...).
        stig_info: Optional ``{"title": ..., "version": ...}``.

    Returns:
        Dict with ``files``, ``findings_count`` and a per-status ``summary``.
        A ``findings_count`` of 0 writes an EMPTY checklist rather than
        nothing: a zero-finding .ckl is the correct artifact for a system with
        nothing recorded, and refusing to write one would leave the caller
        unable to tell "no findings" from "the emitter did not run".
    """
    if fmt not in ("ckl", "cklb", "both"):
        raise ValueError(f"Unsupported format: {fmt!r}. Expected 'ckl', 'cklb' or 'both'.")

    findings = get_project_findings(project_id, db_path=db_path)

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = BASE_DIR / ".tmp" / "compliance" / str(project_id) / "stig"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {value: 0 for value in CKL_STATUS_VALUES}
    for finding in findings:
        summary[_ckl_status(finding)] += 1

    files = {}
    if fmt in ("ckl", "both"):
        ckl_path = out_dir / f"{project_id}.ckl"
        ckl_path.write_text(build_ckl(findings, asset=asset, stig_info=stig_info), encoding="utf-8")
        files["ckl"] = str(ckl_path)
    if fmt in ("cklb", "both"):
        cklb_path = out_dir / f"{project_id}.cklb"
        cklb_path.write_text(
            json.dumps(build_cklb(findings, asset=asset, stig_info=stig_info), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        files["cklb"] = str(cklb_path)

    return {
        "project_id": project_id,
        "files": files,
        "findings_count": len(findings),
        "summary": summary,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Emit DISA STIG checklists (.ckl / .cklb) from the stig_findings table.",
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--format", choices=["ckl", "cklb", "both"], default="both")
    parser.add_argument("--output-dir", help="Destination directory")
    parser.add_argument("--host-name", help="ASSET/HOST_NAME to record on the checklist")
    parser.add_argument("--stig-title", help="STIG_INFO title (default: derived from the findings)")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON")

    args = parser.parse_args()

    asset = {"host_name": args.host_name} if args.host_name else None
    stig_info = {"title": args.stig_title} if args.stig_title else None

    try:
        result = write_stig_checklist(
            args.project_id,
            output_dir=args.output_dir,
            db_path=args.db_path,
            fmt=args.format,
            asset=asset,
            stig_info=stig_info,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"STIG checklist written for {args.project_id}:")
        for kind, path in result["files"].items():
            print(f"  {kind}: {path}")
        print(f"  Findings: {result['findings_count']}")
        for status, count in result["summary"].items():
            if count:
                print(f"    {status}: {count}")


if __name__ == "__main__":
    main()

####################################################################
# CUI // SP-CTI | Department of Defense
####################################################################
