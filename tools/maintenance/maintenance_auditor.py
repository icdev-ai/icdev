#!/usr/bin/env python3
# CUI // SP-CTI
"""Maintenance Auditor — orchestrates full maintenance audit lifecycle.

Steps:
1. Run dependency scanner to inventory all deps
2. Run vulnerability checker to find CVEs
3. Compute maintenance score (0-100)
4. Check SLA compliance
5. Generate audit report (markdown with CUI markings)
6. Store snapshot in maintenance_audits table
7. Log audit trail event

Scoring: Start at 100, deduct penalties for overdue SLAs and staleness.

CLI: python tools/maintenance/maintenance_auditor.py --project-id <id> [--output-dir DIR] [--json]
"""

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
MAINTENANCE_CONFIG_PATH = BASE_DIR / "args" / "maintenance_config.yaml"
CUI_CONFIG_PATH = BASE_DIR / "args" / "cui_markings.yaml"
SECURITY_GATES_PATH = BASE_DIR / "args" / "security_gates.yaml"

CUI_BANNER = """\
//////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D - Authorized DoD Personnel Only
//////////////////////////////////////////////////////////////////"""

CUI_FOOTER = """\
//////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
//////////////////////////////////////////////////////////////////"""

MAINTENANCE_CONTROLS = {
    "SI-2": "Flaw Remediation", "SA-22": "Unsupported System Components",
    "CM-3": "Configuration Change Control", "CM-8": "System Component Inventory",
    "RA-5": "Vulnerability Monitoring and Scanning",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project record from database."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event for maintenance audit. event_type='maintenance_audit'."""
    try:
        conn.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action, details,
                affected_files, classification) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "maintenance_audit", "icdev-maintenance-engine", action,
             json.dumps(details) if details else None,
             json.dumps([str(file_path)]) if file_path else None, "CUI"))
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _parse_yaml_value(val):
    """Parse a YAML scalar string to Python type."""
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    low = val.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    for cast in (int, float):
        try:
            return cast(val)
        except ValueError:
            pass
    return val


def _parse_simple_yaml(path):
    """Minimal stdlib YAML parser for ICDEV config files (2-level nesting)."""
    if not path.exists():
        return {}
    result, section, ml_key, ml_indent, ml_lines = {}, None, None, 0, []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        # Flush multiline blocks
        if ml_key is not None:
            if raw_line and (len(raw_line) - len(raw_line.lstrip()) >= ml_indent or stripped == ""):
                ml_lines.append(raw_line[ml_indent:] if len(raw_line) >= ml_indent else "")
                continue
            target = result.setdefault(section, {}) if section else result
            target[ml_key] = "\n".join(ml_lines).rstrip("\n")
            ml_key = None
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if stripped.startswith("- ") and section:
            k = list(result.get(section, {}).keys())[-1] if result.get(section) else None
            if k and isinstance(result[section].get(k), list):
                result[section][k].append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" not in stripped:
            continue
        ci = stripped.index(":")
        key, vp = stripped[:ci].strip(), stripped[ci+1:].strip()
        if "#" in vp and not vp.startswith('"'):
            vp = vp[:vp.index("#")].strip()
        if indent == 0:
            if vp == "|":
                ml_key, section, ml_lines = key, None, []
                ml_indent = 2
            elif vp == "":
                section = key
                result.setdefault(key, {})
            else:
                section = None
                result[key] = _parse_yaml_value(vp)
        elif section is not None:
            if vp == "|":
                ml_key, ml_lines = key, []
                ml_indent = indent + 2
            elif vp == "":
                result[section].setdefault(key, {})
            else:
                result[section][key] = _parse_yaml_value(vp)
    if ml_key is not None:
        target = result.setdefault(section, {}) if section else result
        target[ml_key] = "\n".join(ml_lines).rstrip("\n")
    return result


def _load_maintenance_config():
    """Load args/maintenance_config.yaml with defaults."""
    defaults = {
        "sla": {"critical_hours": 48, "high_hours": 168, "medium_hours": 720, "low_hours": 2160},
        "staleness": {"warning_days": 90, "critical_days": 180, "max_acceptable_days": 365},
        "scoring": {"overdue_critical_penalty": 15, "overdue_high_penalty": 10,
                     "overdue_medium_penalty": 5, "overdue_low_penalty": 2,
                     "staleness_penalty_per_day": 0.1, "staleness_baseline_days": 90},
        "reporting": {"trend_lookback_days": 90, "cui_markings": True},
    }
    config = _parse_simple_yaml(MAINTENANCE_CONFIG_PATH)
    for sec, sec_defs in defaults.items():
        if sec not in config:
            config[sec] = sec_defs
        elif isinstance(sec_defs, dict):
            for k, v in sec_defs.items():
                config[sec].setdefault(k, v)
    return config


def _load_cui_config():
    """Load args/cui_markings.yaml for CUI banners."""
    config = _parse_simple_yaml(CUI_CONFIG_PATH)
    config.setdefault("document_header", CUI_BANNER)
    config.setdefault("document_footer", CUI_FOOTER)
    return config


def _load_security_gates():
    """Load args/security_gates.yaml for gate evaluation."""
    return _parse_simple_yaml(SECURITY_GATES_PATH) or {"thresholds": {"dependency": {"max_critical": 0, "max_high": 0}}}

# ---------------------------------------------------------------------------
# Lazy imports for sibling tools
# ---------------------------------------------------------------------------

def _import_dependency_scanner():
    """Lazy import dependency_scanner from same directory."""
    path = Path(__file__).parent / "dependency_scanner.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("dependency_scanner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_vulnerability_checker():
    """Lazy import vulnerability_checker from same directory."""
    path = Path(__file__).parent / "vulnerability_checker.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("vulnerability_checker", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_maintenance_score(audit_data, config=None):
    """Compute maintenance score (0-100) based on SLA compliance and staleness.

    Scoring rules (from maintenance_config.yaml):
    - Start at 100
    - Deduct 15 per overdue critical SLA
    - Deduct 10 per overdue high SLA
    - Deduct 5 per overdue medium SLA
    - Deduct 2 per overdue low SLA
    - Deduct 0.1 per day of average staleness above 90 days baseline
    - Floor at 0

    Args:
        audit_data: dict with overdue_critical, overdue_high, overdue_medium,
                    overdue_low, avg_staleness_days
        config: maintenance_config dict (optional)

    Returns:
        float: score 0.0 to 100.0
    """
    scoring = config.get("scoring", {}) if config else {}
    score = 100.0
    score -= audit_data.get("overdue_critical", 0) * scoring.get("overdue_critical_penalty", 15)
    score -= audit_data.get("overdue_high", 0) * scoring.get("overdue_high_penalty", 10)
    score -= audit_data.get("overdue_medium", 0) * scoring.get("overdue_medium_penalty", 5)
    score -= audit_data.get("overdue_low", 0) * scoring.get("overdue_low_penalty", 2)
    baseline = scoring.get("staleness_baseline_days", 90)
    avg_stale = audit_data.get("avg_staleness_days", 0)
    if avg_stale > baseline:
        score -= (avg_stale - baseline) * scoring.get("staleness_penalty_per_day", 0.1)
    return max(0.0, round(score, 1))

# ---------------------------------------------------------------------------
# Data collection from DB
# ---------------------------------------------------------------------------

def _collect_staleness_stats(conn, project_id):
    """Query dependency_inventory for staleness statistics."""
    rows = conn.execute(
        "SELECT language, package_name, current_version, latest_version, days_stale, scope "
        "FROM dependency_inventory WHERE project_id = ?", (project_id,)).fetchall()
    if not rows:
        return {"total_dependencies": 0, "outdated_count": 0, "avg_staleness_days": 0.0,
                "max_staleness_days": 0, "by_language": {},
                "staleness_distribution": {"current": 0, "warning": 0, "critical": 0, "unacceptable": 0}}
    cfg = _load_maintenance_config().get("staleness", {})
    warn_d, crit_d, max_d = cfg.get("warning_days", 90), cfg.get("critical_days", 180), cfg.get("max_acceptable_days", 365)
    stale_vals = [r["days_stale"] or 0 for r in rows]
    total = len(rows)
    outdated = sum(1 for r in rows if r["latest_version"] and r["current_version"] != r["latest_version"])
    # By language
    by_lang = {}
    for r in rows:
        d = by_lang.setdefault(r["language"], {"total": 0, "outdated": 0, "avg_staleness": 0.0, "_stale": []})
        d["total"] += 1
        d["_stale"].append(r["days_stale"] or 0)
        if r["latest_version"] and r["current_version"] != r["latest_version"]:
            d["outdated"] += 1
    for d in by_lang.values():
        d["avg_staleness"] = round(sum(d["_stale"]) / len(d["_stale"]), 1) if d["_stale"] else 0.0
        del d["_stale"]
    # Distribution
    dist = {"current": 0, "warning": 0, "critical": 0, "unacceptable": 0}
    for s in stale_vals:
        if s <= warn_d:
            dist["current"] += 1
        elif s <= crit_d:
            dist["warning"] += 1
        elif s <= max_d:
            dist["critical"] += 1
        else:
            dist["unacceptable"] += 1
    return {"total_dependencies": total, "outdated_count": outdated,
            "avg_staleness_days": round(sum(stale_vals) / total, 1), "max_staleness_days": max(stale_vals),
            "by_language": by_lang, "staleness_distribution": dist}


def _collect_vulnerability_stats(conn, project_id):
    """Query dependency_vulnerabilities for SLA compliance stats."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT severity, sla_category, sla_deadline, status, fix_available, "
        "exploit_available, cve_id, title FROM dependency_vulnerabilities "
        "WHERE project_id = ? AND status NOT IN ('remediated','false_positive')", (project_id,)).fetchall()
    zero_sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    if not rows:
        return {"vulnerable_count": 0, "by_severity": dict(zero_sev), "overdue_critical": 0,
                "overdue_high": 0, "overdue_medium": 0, "overdue_low": 0, "sla_compliant_pct": 100.0,
                "fix_available_count": 0, "exploit_available_count": 0, "overdue_items": []}
    by_sev = dict(zero_sev)
    overdue = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    overdue_items, fix_avail, exploit_avail, sla_total, sla_met = [], 0, 0, 0, 0
    for r in rows:
        sev = r["severity"] or "unknown"
        by_sev[sev] = by_sev.get(sev, 0) + 1
        if r["fix_available"]:
            fix_avail += 1
        if r["exploit_available"]:
            exploit_avail += 1
        if r["sla_deadline"]:
            sla_total += 1
            if r["sla_deadline"] < now:
                cat = r["sla_category"] or sev
                if cat in overdue:
                    overdue[cat] += 1
                overdue_items.append({"cve_id": r["cve_id"], "severity": sev,
                    "sla_category": r["sla_category"], "sla_deadline": r["sla_deadline"],
                    "title": r["title"], "status": r["status"]})
            else:
                sla_met += 1
    return {"vulnerable_count": len(rows), "by_severity": by_sev,
            "overdue_critical": overdue["critical"], "overdue_high": overdue["high"],
            "overdue_medium": overdue["medium"], "overdue_low": overdue["low"],
            "sla_compliant_pct": round(100.0 * sla_met / sla_total, 1) if sla_total else 100.0,
            "fix_available_count": fix_avail, "exploit_available_count": exploit_avail,
            "overdue_items": overdue_items}

# ---------------------------------------------------------------------------
# Trend Analysis
# ---------------------------------------------------------------------------

def _generate_trend_analysis(conn, project_id, lookback_days=90):
    """Compare current audit against previous audits for trend analysis.

    Returns dict with:
    - previous_audits: list of {date, score, total_deps, vulnerable_count}
    - score_trend: "improving" | "stable" | "degrading"
    - vulnerability_trend: "improving" | "stable" | "degrading"
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT audit_date, maintenance_score, total_dependencies, vulnerable_count, "
        "outdated_count, avg_staleness_days, critical_vulns, high_vulns "
        "FROM maintenance_audits WHERE project_id = ? AND audit_date >= ? ORDER BY audit_date ASC",
        (project_id, cutoff)).fetchall()
    previous = [{"date": r["audit_date"], "score": r["maintenance_score"],
                  "total_deps": r["total_dependencies"], "vulnerable_count": r["vulnerable_count"]}
                 for r in rows]
    score_trend, vuln_trend = "stable", "stable"
    if len(previous) >= 2:
        sd = previous[-1]["score"] - previous[0]["score"]
        vd = previous[-1]["vulnerable_count"] - previous[0]["vulnerable_count"]
        if sd > 5:
            score_trend = "improving"
        elif sd < -5:
            score_trend = "degrading"
        if vd < 0:
            vuln_trend = "improving"
        elif vd > 0:
            vuln_trend = "degrading"
    return {"previous_audits": previous, "score_trend": score_trend,
            "vulnerability_trend": vuln_trend, "audit_count": len(previous),
            "lookback_days": lookback_days}

# ---------------------------------------------------------------------------
# Gate Evaluation
# ---------------------------------------------------------------------------

def _evaluate_gate(audit_data, config=None):
    """Evaluate maintenance gate pass/fail.

    Blocking conditions (from security_gates.yaml):
    - overdue_critical_sla: any overdue critical = FAIL
    - maintenance_score_below_50: score < 50 = FAIL

    Warning conditions:
    - maintenance_score_below_70: score < 70 = WARN
    - avg_staleness_over_180_days: avg > 180 = WARN

    Returns: dict with gate_status (PASS/WARN/FAIL), blockers, warnings
    """
    gates = config or _load_security_gates()
    dep_t = gates.get("thresholds", {}).get("dependency", {})
    blockers, warnings = [], []
    oc = audit_data.get("overdue_critical", 0)
    if oc > 0:
        blockers.append(f"{oc} overdue critical SLA(s) — remediation past deadline")
    cv = audit_data.get("critical_vulns", 0)
    if cv > dep_t.get("max_critical", 0):
        blockers.append(f"{cv} critical vulnerabilities (max: {dep_t.get('max_critical', 0)})")
    hv = audit_data.get("high_vulns", 0)
    if hv > dep_t.get("max_high", 0):
        blockers.append(f"{hv} high vulnerabilities (max: {dep_t.get('max_high', 0)})")
    score = audit_data.get("maintenance_score", 100.0)
    if score < 50:
        blockers.append(f"Maintenance score {score} below minimum threshold 50")
    elif score < 70:
        warnings.append(f"Maintenance score {score} below recommended threshold 70")
    if audit_data.get("avg_staleness_days", 0) > 180:
        warnings.append(f"Average staleness {audit_data['avg_staleness_days']:.0f} days exceeds 180-day warning")
    oh = audit_data.get("overdue_high", 0)
    if oh > 0:
        warnings.append(f"{oh} overdue high-severity SLA(s)")
    status = "FAIL" if blockers else ("WARN" if warnings else "PASS")
    return {"gate_status": status, "blockers": blockers, "warnings": warnings}

# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

def _generate_audit_report(audit_data, output_dir, project_name):
    """Generate CUI-marked maintenance audit report.

    Report sections:
    1. CUI Banner
    2. Executive Summary (score, overall status, key metrics)
    3. Dependency Inventory Summary (by language, total, outdated)
    4. Vulnerability Summary (by severity, SLA compliance)
    5. SLA Compliance Status (overdue items, deadlines)
    6. Staleness Analysis (avg, max, distribution)
    7. Trend Analysis (vs previous audits)
    8. Remediation Recommendations (prioritized list)
    9. NIST 800-53 Control Mapping (SI-2, SA-22, CM-3)
    10. CUI Footer

    Returns: report file path
    """
    cui = _load_cui_config()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    score = audit_data.get("maintenance_score", 0)
    gate = audit_data.get("gate", {})
    sl = audit_data.get("staleness_stats", {})
    vl = audit_data.get("vulnerability_stats", {})
    tr = audit_data.get("trend", {})
    recs = audit_data.get("recommendations", [])
    status_label = "HEALTHY" if score >= 80 else ("AT RISK" if score >= 50 else "CRITICAL")

    L = [cui.get("document_header", CUI_BANNER), "",
         "# Maintenance Audit Report", "",
         f"**Project:** {project_name} ({audit_data.get('project_id','N/A')})",
         f"**Date:** {ts}", "**Classification:** CUI // SP-CTI", "",
         "## Executive Summary", "",
         "| Metric | Value |", "|--------|-------|",
         f"| **Maintenance Score** | **{score}/100** |",
         f"| **Status** | {status_label} |",
         f"| **Gate** | {gate.get('gate_status','N/A')} |",
         f"| Total Dependencies | {sl.get('total_dependencies',0)} |",
         f"| Outdated | {sl.get('outdated_count',0)} |",
         f"| Vulnerable | {vl.get('vulnerable_count',0)} |",
         f"| SLA Compliance | {vl.get('sla_compliant_pct',100.0)}% |",
         f"| Avg Staleness | {sl.get('avg_staleness_days',0)} days |",
         f"| Score Trend | {tr.get('score_trend','N/A')} |", ""]

    if gate.get("blockers"):
        L += ["### Gate Blockers (FAIL)", ""] + [f"- **BLOCKER:** {b}" for b in gate["blockers"]] + [""]
    if gate.get("warnings"):
        L += ["### Gate Warnings", ""] + [f"- WARNING: {w}" for w in gate["warnings"]] + [""]

    # Dependency Inventory
    L += ["## Dependency Inventory", ""]
    by_lang = sl.get("by_language", {})
    if by_lang:
        L += ["| Language | Total | Outdated | Avg Staleness (days) |",
              "|----------|-------|----------|----------------------|"]
        for lang, s in sorted(by_lang.items()):
            L.append(f"| {lang} | {s['total']} | {s['outdated']} | {s['avg_staleness']:.0f} |")
    else:
        L.append("*No dependencies inventoried.*")
    L.append("")

    # Vulnerability Summary
    L += ["## Vulnerability Summary", "", "| Severity | Count |", "|----------|-------|"]
    for sev in ("critical", "high", "medium", "low", "unknown"):
        c = vl.get("by_severity", {}).get(sev, 0)
        if c > 0:
            L.append(f"| {sev.upper()} | {c} |")
    L += [f"| **Total** | **{vl.get('vulnerable_count',0)}** |", "",
          f"- Fixes available: {vl.get('fix_available_count',0)}",
          f"- Known exploits: {vl.get('exploit_available_count',0)}", ""]

    # SLA Compliance
    L += ["## SLA Compliance", "", f"**Overall SLA Compliance:** {vl.get('sla_compliant_pct',100.0)}%", ""]
    oi = vl.get("overdue_items", [])
    if oi:
        L += ["### Overdue SLA Items", "",
              "| CVE | Severity | SLA Category | Deadline | Title |",
              "|-----|----------|--------------|----------|-------|"]
        for item in oi[:20]:
            L.append(f"| {item.get('cve_id','N/A')} | {item.get('severity','?')} | "
                     f"{item.get('sla_category','?')} | {item.get('sla_deadline','?')} | "
                     f"{item.get('title','')[:60]} |")
        L += ["", f"- Critical overdue: {vl.get('overdue_critical',0)}",
              f"- High overdue: {vl.get('overdue_high',0)}",
              f"- Medium overdue: {vl.get('overdue_medium',0)}",
              f"- Low overdue: {vl.get('overdue_low',0)}", ""]
    else:
        L += ["All remediations within SLA deadlines.", ""]

    # Staleness Analysis
    dist = sl.get("staleness_distribution", {})
    L += ["## Staleness Analysis", "",
          f"- **Average:** {sl.get('avg_staleness_days',0)} days",
          f"- **Maximum:** {sl.get('max_staleness_days',0)} days", "",
          "| Category | Count | Threshold |", "|----------|-------|-----------|",
          f"| Current | {dist.get('current',0)} | 0-90 days |",
          f"| Warning | {dist.get('warning',0)} | 91-180 days |",
          f"| Critical | {dist.get('critical',0)} | 181-365 days |",
          f"| Unacceptable | {dist.get('unacceptable',0)} | >365 days |", ""]

    # Trend Analysis
    L += ["## Trend Analysis", "",
          f"- Lookback: {tr.get('lookback_days',90)} days | Audits: {tr.get('audit_count',0)}",
          f"- Score trend: {tr.get('score_trend','N/A')} | Vuln trend: {tr.get('vulnerability_trend','N/A')}", ""]
    pa = tr.get("previous_audits", [])
    if pa:
        L += ["| Date | Score | Dependencies | Vulnerable |", "|------|-------|--------------|------------|"]
        for p in pa[-10:]:
            L.append(f"| {p['date']} | {p['score']} | {p['total_deps']} | {p['vulnerable_count']} |")
        L.append("")

    # Recommendations
    L += ["## Remediation Recommendations", ""]
    for r in recs:
        L += [f"### {r['priority']}. {r['category']}", "",
              f"**Action:** {r['recommendation']}", f"**Impact:** {r['impact']}", ""]

    # NIST Control Mapping
    L += ["## NIST 800-53 Control Mapping", "",
          "| Control | Title | Relevance |", "|---------|-------|-----------|",
          "| SI-2 | Flaw Remediation | Vulnerability tracking, SLA enforcement, patch management |",
          "| SA-22 | Unsupported System Components | Staleness analysis, end-of-life detection |",
          "| CM-3 | Configuration Change Control | Dependency version tracking, change auditing |",
          "| CM-8 | System Component Inventory | Complete dependency inventory with PURLs |",
          "| RA-5 | Vulnerability Monitoring and Scanning | CVE detection, severity assessment |", "",
          "---", "", f"*Generated by ICDEV Maintenance Auditor on {ts}*", "",
          cui.get("document_footer", CUI_FOOTER), ""]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"maintenance_audit_{now.strftime('%Y%m%d_%H%M%S')}.md"
    report_file.write_text("\n".join(L), encoding="utf-8")
    return str(report_file)


def _generate_remediation_recommendations(vuln_stats, staleness_stats, config=None):
    """Generate prioritized remediation recommendations list."""
    recs, p = [], 0
    by_sev = vuln_stats.get("by_severity", {})
    dist = staleness_stats.get("staleness_distribution", {})

    if by_sev.get("critical", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "CRITICAL VULNERABILITY",
            "recommendation": f"Remediate {by_sev['critical']} critical vulnerabilities immediately. "
                              f"{vuln_stats.get('fix_available_count',0)} fixes available.",
            "impact": "Blocks deployment gate. Required within 48 hours per SLA."})
    if vuln_stats.get("overdue_critical", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "SLA VIOLATION",
            "recommendation": f"{vuln_stats['overdue_critical']} critical SLA(s) overdue. Escalate immediately.",
            "impact": "Blocks deployment gate. NIST SI-2 non-compliance."})
    if by_sev.get("high", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "HIGH VULNERABILITY",
            "recommendation": f"Remediate {by_sev['high']} high-severity vulnerabilities within 7-day SLA.",
            "impact": "Blocks deployment gate if count exceeds threshold."})
    if vuln_stats.get("exploit_available_count", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "ACTIVE EXPLOIT",
            "recommendation": f"{vuln_stats['exploit_available_count']} vulnerabilities have known exploits.",
            "impact": "Elevated risk of active exploitation."})
    if dist.get("unacceptable", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "UNSUPPORTED COMPONENTS",
            "recommendation": f"{dist['unacceptable']} dependencies exceed 365-day staleness limit.",
            "impact": "NIST SA-22 finding — unsupported system components."})
    if dist.get("critical", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "STALE DEPENDENCIES",
            "recommendation": f"{dist['critical']} dependencies critically stale (180-365 days).",
            "impact": "Increases vulnerability surface and maintenance burden."})
    if by_sev.get("medium", 0) > 0:
        p += 1
        recs.append({"priority": p, "category": "MEDIUM VULNERABILITY",
            "recommendation": f"Plan remediation for {by_sev['medium']} medium vulnerabilities within 30-day SLA.",
            "impact": "Affects maintenance score. 5-point penalty per overdue SLA."})
    if not recs:
        recs.append({"priority": 1, "category": "MAINTENANCE",
            "recommendation": "No urgent actions. Continue routine dependency updates.",
            "impact": "Maintain current compliance posture."})
    return recs

# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

def run_maintenance_audit(project_id, output_dir=None, offline=False, db_path=None):
    """Run full maintenance audit lifecycle.

    Steps:
    1. Connect to DB, load project
    2. Import and run dependency_scanner.scan_dependencies()
    3. Import and run vulnerability_checker.check_vulnerabilities()
    4. Query dependency_inventory for staleness stats
    5. Query dependency_vulnerabilities for SLA compliance
    6. Compute maintenance score
    7. Generate trend analysis
    8. Generate CUI-marked audit report
    9. Store snapshot in maintenance_audits table
    10. Log audit event
    11. Print summary
    12. Return result dict

    Returns:
        dict with: project_id, maintenance_score, total_dependencies,
                    outdated_count, vulnerable_count, sla_compliance,
                    by_severity, overdue_counts, trend, report_path,
                    gate_status (PASS/FAIL based on security_gates.yaml)
    """
    conn = _get_connection(db_path)
    config = _load_maintenance_config()
    scanner_error = checker_error = None

    try:
        # 1. Load project
        project = _get_project(conn, project_id)
        project_name = project.get("name", project_id)
        project_dir = project.get("directory_path", "")

        # 2. Run dependency scanner (graceful on failure)
        scanner = _import_dependency_scanner()
        if scanner:
            try:
                fn = getattr(scanner, "scan_dependencies", None)
                if fn:
                    fn(project_id=project_id, project_dir=project_dir or None, offline=offline, db_path=db_path)
                else:
                    scanner_error = "dependency_scanner.scan_dependencies() not found"
            except Exception as e:
                scanner_error = f"Dependency scanner failed: {e}"
                print(f"Warning: {scanner_error}", file=sys.stderr)
        else:
            scanner_error = "dependency_scanner.py not found — using existing DB data"
            print(f"Warning: {scanner_error}", file=sys.stderr)

        # 3. Run vulnerability checker (graceful on failure)
        checker = _import_vulnerability_checker()
        if checker:
            try:
                fn = getattr(checker, "check_vulnerabilities", None)
                if fn:
                    fn(project_id=project_id, offline=offline, db_path=db_path)
                else:
                    checker_error = "vulnerability_checker.check_vulnerabilities() not found"
            except Exception as e:
                checker_error = f"Vulnerability checker failed: {e}"
                print(f"Warning: {checker_error}", file=sys.stderr)
        else:
            checker_error = "vulnerability_checker.py not found — using existing DB data"
            print(f"Warning: {checker_error}", file=sys.stderr)

        # 4-5. Collect stats from DB
        staleness_stats = _collect_staleness_stats(conn, project_id)
        vuln_stats = _collect_vulnerability_stats(conn, project_id)

        # 6. Compute score
        score_input = {k: vuln_stats[k] for k in ("overdue_critical", "overdue_high", "overdue_medium", "overdue_low")}
        score_input["avg_staleness_days"] = staleness_stats["avg_staleness_days"]
        maintenance_score = _compute_maintenance_score(score_input, config)

        # 7. Trend analysis
        lookback = config.get("reporting", {}).get("trend_lookback_days", 90)
        trend = _generate_trend_analysis(conn, project_id, lookback)

        # Recommendations and gate
        recommendations = _generate_remediation_recommendations(vuln_stats, staleness_stats, config)
        gate_input = {"overdue_critical": vuln_stats["overdue_critical"],
                      "overdue_high": vuln_stats["overdue_high"],
                      "critical_vulns": vuln_stats["by_severity"].get("critical", 0),
                      "high_vulns": vuln_stats["by_severity"].get("high", 0),
                      "maintenance_score": maintenance_score,
                      "avg_staleness_days": staleness_stats["avg_staleness_days"]}
        gate = _evaluate_gate(gate_input)

        # 8. Generate report
        if output_dir:
            rpt_dir = output_dir
        elif project_dir:
            rpt_dir = str(Path(project_dir) / "compliance" / "maintenance")
        else:
            rpt_dir = str(BASE_DIR / ".tmp" / "maintenance" / project_id)

        report_path = _generate_audit_report(
            {"project_id": project_id, "maintenance_score": maintenance_score,
             "staleness_stats": staleness_stats, "vulnerability_stats": vuln_stats,
             "trend": trend, "recommendations": recommendations, "gate": gate},
            rpt_dir, project_name)

        # 9. Store snapshot
        try:
            conn.execute(
                "INSERT INTO maintenance_audits (project_id, total_dependencies, outdated_count, "
                "vulnerable_count, critical_vulns, high_vulns, medium_vulns, low_vulns, "
                "avg_staleness_days, max_staleness_days, sla_compliant_pct, overdue_critical, "
                "overdue_high, maintenance_score, languages_audited, report_path, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, staleness_stats["total_dependencies"], staleness_stats["outdated_count"],
                 vuln_stats["vulnerable_count"], vuln_stats["by_severity"].get("critical", 0),
                 vuln_stats["by_severity"].get("high", 0), vuln_stats["by_severity"].get("medium", 0),
                 vuln_stats["by_severity"].get("low", 0), staleness_stats["avg_staleness_days"],
                 staleness_stats["max_staleness_days"], vuln_stats["sla_compliant_pct"],
                 vuln_stats["overdue_critical"], vuln_stats["overdue_high"], maintenance_score,
                 json.dumps(sorted(staleness_stats.get("by_language", {}).keys())), report_path, "CUI"))
            conn.commit()
        except Exception as e:
            print(f"Warning: Could not store audit snapshot: {e}", file=sys.stderr)

        # 10. Log audit event
        _log_audit_event(conn, project_id, f"Maintenance audit completed — score {maintenance_score}", {
            "maintenance_score": maintenance_score, "total_dependencies": staleness_stats["total_dependencies"],
            "vulnerable_count": vuln_stats["vulnerable_count"], "sla_compliant_pct": vuln_stats["sla_compliant_pct"],
            "gate_status": gate["gate_status"], "report_path": report_path}, report_path)

        # 11. Print summary
        print("=" * 60)
        print("  MAINTENANCE AUDIT COMPLETE")
        print("=" * 60)
        print(f"  Project:        {project_name} ({project_id})")
        print(f"  Score:          {maintenance_score}/100")
        print(f"  Gate:           {gate['gate_status']}")
        print(f"  Dependencies:   {staleness_stats['total_dependencies']}")
        print(f"  Outdated:       {staleness_stats['outdated_count']}")
        print(f"  Vulnerable:     {vuln_stats['vulnerable_count']}")
        print(f"  SLA Compliance: {vuln_stats['sla_compliant_pct']}%")
        print(f"  Avg Staleness:  {staleness_stats['avg_staleness_days']} days")
        print(f"  Score Trend:    {trend['score_trend']}")
        print(f"  Report:         {report_path}")
        if gate["blockers"]:
            print("\n  BLOCKERS:")
            for b in gate["blockers"]:
                print(f"    ! {b}")
        if gate["warnings"]:
            print("\n  WARNINGS:")
            for w in gate["warnings"]:
                print(f"    ~ {w}")
        if scanner_error:
            print(f"\n  Note: {scanner_error}")
        if checker_error:
            print(f"  Note: {checker_error}")
        print("=" * 60)

        # 12. Return result
        return {
            "project_id": project_id, "project_name": project_name,
            "maintenance_score": maintenance_score,
            "total_dependencies": staleness_stats["total_dependencies"],
            "outdated_count": staleness_stats["outdated_count"],
            "vulnerable_count": vuln_stats["vulnerable_count"],
            "sla_compliance": vuln_stats["sla_compliant_pct"],
            "by_severity": vuln_stats["by_severity"],
            "overdue_counts": {s: vuln_stats[f"overdue_{s}"] for s in ("critical","high","medium","low")},
            "staleness": {"avg_days": staleness_stats["avg_staleness_days"],
                          "max_days": staleness_stats["max_staleness_days"],
                          "distribution": staleness_stats["staleness_distribution"]},
            "trend": {"score_trend": trend["score_trend"],
                      "vulnerability_trend": trend["vulnerability_trend"],
                      "audit_count": trend["audit_count"]},
            "report_path": report_path, "gate_status": gate["gate_status"],
            "gate_blockers": gate["blockers"], "gate_warnings": gate["warnings"],
            "scanner_error": scanner_error, "checker_error": checker_error,
        }
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run full maintenance audit")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output-dir", help="Output directory for report")
    parser.add_argument("--offline", action="store_true", help="Skip online registry checks")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gates")
    parser.add_argument("--db-path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_maintenance_audit(
            project_id=args.project_id, output_dir=args.output_dir,
            offline=args.offline, db_path=Path(args.db_path) if args.db_path else None)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        if args.gate and result.get("gate_status") == "FAIL":
            sys.exit(1)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
