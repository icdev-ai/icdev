# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""Map SysML model elements to NIST 800-53 controls based on element type,
stereotype, and name keywords. Creates digital_thread_links entries."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    sys.path.insert(0, str(BASE_DIR / "tools" / "audit"))
    from audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None

# -- Keyword-to-control-family mapping ----------------------------------------

KEYWORD_CONTROL_MAP = {
    "AC": ["access", "auth", "login", "permission", "role", "rbac", "session", "account", "privilege"],
    "AU": ["audit", "log", "trail", "record", "monitor", "event", "trace"],
    "CA": ["assess", "certif", "accredit", "test", "evaluate"],
    "CM": ["config", "baseline", "change", "version", "deploy", "release"],
    "IA": ["identity", "authenticate", "credential", "mfa", "token", "certificate", "pki", "cac"],
    "IR": ["incident", "respond", "alert", "breach", "forensic"],
    "MA": ["maintain", "patch", "update", "upgrade"],
    "MP": ["media", "storage", "backup", "sanitiz"],
    "PE": ["physical", "facility", "badge", "perimeter"],
    "PL": ["plan", "policy", "procedure", "standard"],
    "RA": ["risk", "threat", "vulnerab", "scan"],
    "SC": ["encrypt", "crypto", "tls", "ssl", "network", "firewall", "boundary", "transmit", "protect", "isolat"],
    "SI": ["integrity", "malware", "antivirus", "flaw", "error", "valid", "sanitiz", "input"],
}

STEREOTYPE_CONTROL_MAP = {
    "security": ["AC", "SC", "SI"], "authentication": ["AC", "IA"],
    "authorization": ["AC"], "encryption": ["SC"], "logging": ["AU"],
    "audit": ["AU"], "monitoring": ["AU", "SI"], "configuration": ["CM"],
    "deployment": ["CM"], "incident": ["IR"], "compliance": ["CA"],
    "risk": ["RA"], "network": ["SC"], "boundary": ["SC"],
    "identity": ["IA"], "backup": ["MP"], "maintenance": ["MA"],
    "physical": ["PE"], "planning": ["PL"], "validation": ["SI"],
    "integrity": ["SI"],
}

ELEMENT_TYPE_HINTS = {
    "state_machine": ["CM"], "interface_block": ["SC"],
    "port": ["SC"], "connector": ["SC"],
}

# -- DB helpers ----------------------------------------------------------------

def _get_connection(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _verify_project(conn, project_id):
    row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _log_audit(project_id, action, details, db_path=None):
    if audit_log_event is not None:
        try:
            audit_log_event(event_type="compliance_check", actor="icdev-mbse-engine",
                            action=action, project_id=project_id, details=details,
                            classification="CUI", db_path=db_path)
        except Exception:
            pass

# -- Core matching logic -------------------------------------------------------

def _match_keywords(text: str) -> list:
    """Match text against KEYWORD_CONTROL_MAP, return list of matching family codes."""
    if not text:
        return []
    text_lower = text.lower()
    families = set()
    for family, keywords in KEYWORD_CONTROL_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                families.add(family)
                break
    return sorted(families)


def _match_stereotype(stereotype: str) -> list:
    if not stereotype:
        return []
    s = stereotype.lower()
    families = set()
    for key, fams in STEREOTYPE_CONTROL_MAP.items():
        if key in s:
            families.update(fams)
    return sorted(families)


def _get_controls_for_family(family: str, conn) -> list:
    """Get all compliance_controls IDs for a given family code."""
    rows = conn.execute("SELECT id FROM compliance_controls WHERE family = ? ORDER BY id", (family,)).fetchall()
    return [r["id"] for r in rows]


def _create_thread_link(conn, project_id, source_id, target_id, confidence=0.7, evidence="keyword-mapping"):
    cur = conn.execute(
        """INSERT OR IGNORE INTO digital_thread_links
           (project_id, source_type, source_id, target_type, target_id,
            link_type, confidence, evidence, created_by)
           VALUES (?, 'sysml_element', ?, 'nist_control', ?, 'maps_to', ?, ?, 'icdev-mbse-engine')""",
        (project_id, source_id, target_id, confidence, evidence))
    return cur.rowcount > 0


def _build_evidence(family, name_fams, desc_fams, type_fams, stereo_fams, etype, stereotype):
    parts = []
    if family in name_fams:
        parts.append("name-keyword")
    if family in desc_fams:
        parts.append("description-keyword")
    if family in type_fams:
        parts.append(f"element-type:{etype}")
    if family in stereo_fams:
        parts.append(f"stereotype:{stereotype}")
    return "; ".join(parts) or "keyword-mapping"

# -- Public API ----------------------------------------------------------------

def map_element_to_controls(project_id: str, element_id: str, db_path=None) -> dict:
    """Map a single SysML element to NIST controls. Creates digital_thread_links
    with link_type='maps_to', confidence=0.7. Returns mapping summary dict."""
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        row = conn.execute("SELECT * FROM sysml_elements WHERE id = ? AND project_id = ?",
                           (element_id, project_id)).fetchone()
        if not row:
            raise ValueError(f"SysML element '{element_id}' not found in project '{project_id}'.")
        elem = dict(row)
        all_families = set()
        name_fams = _match_keywords(elem.get("name", ""))
        desc_fams = _match_keywords(elem.get("description", ""))
        etype = elem.get("element_type", "")
        type_fams = ELEMENT_TYPE_HINTS.get(etype, [])
        stereo_fams = _match_stereotype(elem.get("stereotype", ""))
        all_families.update(name_fams, desc_fams, type_fams, stereo_fams)

        controls_mapped, links_created = [], 0
        for family in sorted(all_families):
            ctrl_ids = _get_controls_for_family(family, conn) or [f"{family}-*"]
            for cid in ctrl_ids:
                ev = _build_evidence(family, name_fams, desc_fams, type_fams, stereo_fams,
                                     etype, elem.get("stereotype", ""))
                if _create_thread_link(conn, project_id, element_id, cid, 0.7, ev):
                    links_created += 1
                controls_mapped.append(cid)
        conn.commit()

        result = {"element_id": element_id, "element_name": elem.get("name", ""),
                  "element_type": etype, "families_matched": sorted(all_families),
                  "controls_mapped": controls_mapped, "links_created": links_created,
                  "count": len(controls_mapped)}
        _log_audit(project_id, f"Mapped '{elem.get('name', element_id)}' to {len(controls_mapped)} control(s)",
                   result, db_path)
        return result
    finally:
        conn.close()


def map_all_elements(project_id: str, db_path=None) -> dict:
    """Batch map all unmapped SysML elements to NIST controls.
    Skip elements that already have maps_to links to nist_control targets."""
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        elements = conn.execute(
            "SELECT id, name, element_type, description, stereotype FROM sysml_elements WHERE project_id = ? ORDER BY element_type, name",
            (project_id,)).fetchall()
        already = {r["source_id"] for r in conn.execute(
            "SELECT DISTINCT source_id FROM digital_thread_links WHERE project_id = ? AND source_type = 'sysml_element' AND target_type = 'nist_control' AND link_type = 'maps_to'",
            (project_id,)).fetchall()}

        processed, mapped, total_links, skipped = 0, 0, 0, 0
        for e in elements:
            eid = e["id"]
            if eid in already:
                skipped += 1
                continue
            processed += 1
            fams = set()
            nf = _match_keywords(e["name"] or "")
            df = _match_keywords(e["description"] or "")
            et = e["element_type"] or ""
            tf = ELEMENT_TYPE_HINTS.get(et, [])
            sf = _match_stereotype(e["stereotype"] or "")
            fams.update(nf, df, tf, sf)
            if not fams:
                continue
            mapped += 1
            for fam in sorted(fams):
                cids = _get_controls_for_family(fam, conn) or [f"{fam}-*"]
                for cid in cids:
                    ev = _build_evidence(fam, nf, df, tf, sf, et, e["stereotype"] or "")
                    if _create_thread_link(conn, project_id, eid, cid, 0.7, ev):
                        total_links += 1
        conn.commit()
        result = {"elements_processed": processed, "elements_mapped": mapped,
                  "links_created": total_links, "skipped": skipped}
        _log_audit(project_id, f"Batch mapped {mapped} element(s), {total_links} link(s)", result, db_path)
        return result
    finally:
        conn.close()


def get_control_coverage_from_model(project_id: str, db_path=None) -> dict:
    """Which NIST controls are covered by model element mappings."""
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        pc = conn.execute("SELECT control_id FROM project_controls WHERE project_id = ?", (project_id,)).fetchall()
        all_ids = sorted({r["control_id"] for r in pc})
        if not all_ids:
            all_ids = [r["id"] for r in conn.execute("SELECT id FROM compliance_controls ORDER BY id").fetchall()]
        covered_set = {r["target_id"] for r in conn.execute(
            "SELECT DISTINCT target_id FROM digital_thread_links WHERE project_id = ? AND source_type = 'sysml_element' AND target_type = 'nist_control' AND link_type = 'maps_to'",
            (project_id,)).fetchall()}
        covered = sorted(c for c in all_ids if c in covered_set)
        uncovered = sorted(c for c in all_ids if c not in covered_set)
        total = len(all_ids)
        pct = round(100.0 * len(covered) / total, 1) if total else 0.0
        by_family = {}
        for cid in all_ids:
            f = cid.split("-")[0] if "-" in cid else cid
            by_family.setdefault(f, {"total": 0, "covered": 0, "uncovered": 0})
            by_family[f]["total"] += 1
            by_family[f]["covered" if cid in covered_set else "uncovered"] += 1
        for v in by_family.values():
            v["coverage_pct"] = round(100.0 * v["covered"] / v["total"], 1) if v["total"] else 0.0
        return {"total_controls": total, "covered_controls": len(covered), "coverage_pct": pct,
                "covered": covered, "uncovered": uncovered, "by_family": by_family}
    finally:
        conn.close()


def get_unmapped_controls(project_id: str, db_path=None) -> dict:
    """Controls required by the project that have no model element mapping."""
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        ctrls = conn.execute(
            "SELECT pc.control_id, cc.family, cc.title FROM project_controls pc LEFT JOIN compliance_controls cc ON pc.control_id = cc.id WHERE pc.project_id = ? ORDER BY pc.control_id",
            (project_id,)).fetchall()
        mapped = {r["target_id"] for r in conn.execute(
            "SELECT DISTINCT target_id FROM digital_thread_links WHERE project_id = ? AND source_type = 'sysml_element' AND target_type = 'nist_control' AND link_type = 'maps_to'",
            (project_id,)).fetchall()}
        unmapped = [{"id": c["control_id"], "family": c["family"] or "", "title": c["title"] or ""}
                    for c in ctrls if c["control_id"] not in mapped]
        return {"unmapped_count": len(unmapped), "controls": unmapped}
    finally:
        conn.close()


def get_unmapped_elements(project_id: str, db_path=None) -> dict:
    """Model elements that haven't been mapped to any control."""
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        elems = conn.execute(
            "SELECT id, name, element_type, stereotype FROM sysml_elements WHERE project_id = ? ORDER BY element_type, name",
            (project_id,)).fetchall()
        mapped = {r["source_id"] for r in conn.execute(
            "SELECT DISTINCT source_id FROM digital_thread_links WHERE project_id = ? AND source_type = 'sysml_element' AND target_type = 'nist_control' AND link_type = 'maps_to'",
            (project_id,)).fetchall()}
        unmapped = [{"id": e["id"], "name": e["name"], "element_type": e["element_type"],
                     "stereotype": e["stereotype"] or ""} for e in elems if e["id"] not in mapped]
        return {"unmapped_count": len(unmapped), "elements": unmapped}
    finally:
        conn.close()


def generate_mapping_report(project_id: str, db_path=None) -> str:
    """Generate CUI-marked markdown report of model-to-control mappings."""
    conn = _get_connection(db_path)
    try:
        project = _verify_project(conn, project_id)
        name = project.get("name", project_id)
    finally:
        conn.close()

    cov = get_control_coverage_from_model(project_id, db_path=db_path)
    uc = get_unmapped_controls(project_id, db_path=db_path)
    ue = get_unmapped_elements(project_id, db_path=db_path)

    conn = _get_connection(db_path)
    try:
        links = conn.execute(
            """SELECT dtl.source_id, dtl.target_id, dtl.confidence, dtl.evidence,
                      se.name AS element_name, se.element_type, se.stereotype
               FROM digital_thread_links dtl JOIN sysml_elements se ON dtl.source_id = se.id
               WHERE dtl.project_id = ? AND dtl.source_type = 'sysml_element'
                 AND dtl.target_type = 'nist_control' AND dtl.link_type = 'maps_to'
               ORDER BY se.element_type, se.name, dtl.target_id""",
            (project_id,)).fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = ["CUI // SP-CTI", "", "# Model-to-Control Mapping Report", "",
         f"**Project:** {name}", f"**Project ID:** {project_id}",
         f"**Generated:** {now}", "**Classification:** CUI // SP-CTI", "", "---", "",
         "## 1. Coverage Summary", "",
         "| Metric | Value |", "|--------|-------|",
         f"| Total Controls | {cov['total_controls']} |",
         f"| Covered by Model | {cov['covered_controls']} |",
         f"| Coverage | {cov['coverage_pct']}% |",
         f"| Unmapped Controls | {uc['unmapped_count']} |",
         f"| Unmapped Elements | {ue['unmapped_count']} |", "",
         "## 2. Coverage by Family", "",
         "| Family | Total | Covered | Coverage |", "|--------|------:|--------:|---------:|"]
    for f in sorted(cov.get("by_family", {})):
        d = cov["by_family"][f]
        L.append(f"| {f} | {d['total']} | {d['covered']} | {d['coverage_pct']}% |")
    L += ["", "## 3. Element-to-Control Mappings", ""]
    if links:
        L += ["| Element | Type | Stereotype | Control | Confidence | Evidence |",
              "|---------|------|------------|---------|------------|----------|"]
        for lk in links:
            L.append(f"| {(lk['element_name'] or '')[:30]} | {lk['element_type'] or ''} "
                     f"| {lk['stereotype'] or '--'} | {lk['target_id']} "
                     f"| {lk['confidence']:.1f} | {(lk['evidence'] or '')[:40]} |")
    else:
        L.append("*No model-to-control mappings found.*")
    L += ["", "## 4. Unmapped Controls (Gaps)", ""]
    if uc["controls"]:
        L += ["| Control ID | Family | Title |", "|------------|--------|-------|"]
        for c in uc["controls"]:
            L.append(f"| {c['id']} | {c['family']} | {(c['title'] or '')[:50]} |")
    else:
        L.append("*All project controls are covered by model elements.*")
    L += ["", "## 5. Unmapped Model Elements", ""]
    if ue["elements"]:
        L += ["| Element ID | Name | Type | Stereotype |", "|------------|------|------|------------|"]
        for e in ue["elements"]:
            L.append(f"| {e['id']} | {(e['name'] or '')[:30]} | {e['element_type']} | {e['stereotype'] or '--'} |")
    else:
        L.append("*All model elements have been mapped to controls.*")
    L += ["", "---", "", f"*Generated by ICDEV Model-Control Mapper on {now}*", "", "CUI // SP-CTI"]
    return "\n".join(L)

# -- CLI -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Map SysML model elements to NIST 800-53 controls")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--element-id", help="Map a single element")
    parser.add_argument("--map-all", action="store_true", help="Map all unmapped elements")
    parser.add_argument("--coverage", action="store_true", help="Show control coverage from model")
    parser.add_argument("--gaps", action="store_true", help="Show unmapped controls")
    parser.add_argument("--report", action="store_true", help="Generate mapping report")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()
    db_path = args.db_path

    try:
        if args.element_id:
            r = map_element_to_controls(args.project_id, args.element_id, db_path=db_path)
            if args.json:
                print(json.dumps(r, indent=2))
            else:
                print(f"Mapped '{r['element_name']}' ({r['element_type']}) -> {r['count']} controls "
                      f"[families: {', '.join(r['families_matched'])}] ({r['links_created']} new links)")
        elif args.map_all:
            r = map_all_elements(args.project_id, db_path=db_path)
            if args.json:
                print(json.dumps(r, indent=2))
            else:
                print(f"Processed: {r['elements_processed']} | Mapped: {r['elements_mapped']} | "
                      f"Links: {r['links_created']} | Skipped: {r['skipped']}")
        elif args.coverage:
            r = get_control_coverage_from_model(args.project_id, db_path=db_path)
            if args.json:
                print(json.dumps(r, indent=2))
            else:
                print(f"Coverage: {r['covered_controls']}/{r['total_controls']} ({r['coverage_pct']}%)")
                for f in sorted(r.get("by_family", {})):
                    d = r["by_family"][f]
                    print(f"  {f}: {d['covered']}/{d['total']} ({d['coverage_pct']}%)")
        elif args.gaps:
            r = get_unmapped_controls(args.project_id, db_path=db_path)
            if args.json:
                print(json.dumps(r, indent=2))
            else:
                print(f"Unmapped controls: {r['unmapped_count']}")
                for c in r["controls"]:
                    print(f"  {c['id']:<12} {c['family']:<6} {c['title']}")
        elif args.report:
            print(generate_mapping_report(args.project_id, db_path=db_path))
        else:
            parser.print_help()
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
