# CUI // SP-CTI — Twin high-side compatibility report + ATO acceleration (twx-fed-03)
"""High-side / target-environment compatibility report + ATO acceleration.

SHIFT Patterns 7+8. This module is **integration, not new compliance logic** —
it *composes* engines that already exist:

* fed-02 :func:`tools.twin_core.target_presets.evaluate_target` (which itself
  engages the fed-01 air-gap rules) → the pass/warn/fail signal per resource;
* :mod:`tools.compliance.crosswalk_engine` → the baseline of NIST 800-53
  controls required at a DoD impact level, and framework cross-mapping;
* ``args/iac_control_map.yaml`` → a deterministic resource-type → control map;
* :func:`tools.compliance.classification_manager.get_document_banner` → CUI /
  SECRET markings (NEVER hardcoded here);
* :mod:`tools.viz` → the rendered HTML report artifact;
* the existing ``project_controls`` / ``poam_items`` tables that
  ``ssp_generator`` / ``poam_generator`` / the BDC cATO engine already read —
  :func:`feed_cato_evidence` writes the twin-derived evidence there so cATO
  picks up the IaC-twin control implementations without re-implementing OSCAL.

Two public surfaces:

1. :func:`generate_compatibility_report` — executive verdict + per-resource
   pass/warn/fail + required IAM/network changes + dependency replacements +
   the ATO control-coverage / evidence checklist, all under a classification
   banner. Deterministic; no LLM.
2. :func:`feed_cato_evidence` — the ATO-acceleration wiring: persist the
   generated control-implementation statements (each citing the IaC resource
   that grounds it — TRUST) and POA&M gap items into the DB the ATO engines read.

TRUST: every generated control statement carries an inline ``[source: <iac
resource address>]`` citation grounded in the design; nothing is asserted
without a resource behind it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.twin_core.schema import (
    derive_verdict_from_violations,
    summarize_violations,
)
from tools.twin_core.target_presets import evaluate_target, get_preset

logger = get_logger("icdev.twin_core.compat_report")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MAP_PATH = _REPO_ROOT / "args" / "iac_control_map.yaml"
_DEFAULT_RETENTION = 20
_GAP_MILESTONE_DAYS = 90

_MAP_CACHE: dict | None = None


# ── config ────────────────────────────────────────────────────────────────────

def load_control_map(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load (and cache) the IaC-resource → NIST-control map. Missing → empty."""
    global _MAP_CACHE
    if _MAP_CACHE is not None and path is None and not force:
        return _MAP_CACHE
    cfg_path = Path(path) if path else _DEFAULT_MAP_PATH
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("iac control map not found at %s", cfg_path)
        cfg = {"resources": {}, "token_rules": []}
    if path is None:
        _MAP_CACHE = cfg
    return cfg


# ── resource extraction ───────────────────────────────────────────────────────

def _extract_resources(design: Any) -> list[dict]:
    """Best-effort: pull IaC resources out of any supported design shape.

    Handles a Terraform ``plan -json`` (``resource_changes`` /
    ``planned_values`` / ``values``) and a canvas design graph (``nodes`` with a
    ``type`` / ``service`` / ``resource_type``). Returns ``[{type,name,address}]``
    deduplicated by address.
    """
    out: list[dict] = []
    if not isinstance(design, dict):
        return out

    def add(t, n, addr):
        if not t:
            return
        t = str(t)
        n = str(n) if n else t
        out.append({"type": t, "name": n, "address": str(addr) if addr else f"{t}.{n}"})

    for rc in design.get("resource_changes", []) or []:
        if isinstance(rc, dict):
            add(rc.get("type"), rc.get("name"), rc.get("address"))
    for key in ("planned_values", "values"):
        rm = (design.get(key, {}) or {}).get("root_module", {}) or {}
        for r in rm.get("resources", []) or []:
            if isinstance(r, dict):
                add(r.get("type"), r.get("name"), r.get("address"))
    for node in design.get("nodes", []) or []:
        if isinstance(node, dict):
            t = node.get("type") or node.get("service") or node.get("resource_type")
            n = node.get("label") or node.get("name") or node.get("id")
            add(t, n, node.get("address") or node.get("id"))

    seen: set[str] = set()
    deduped: list[dict] = []
    for r in out:
        if r["address"] in seen:
            continue
        seen.add(r["address"])
        deduped.append(r)
    return deduped


# ── ATO acceleration: IaC resource → control statements + POA&M gaps ──────────

def _controls_for_resource(rtype: str, cmap: dict) -> tuple[list[str], str]:
    """Return (controls, responsible_role) for a resource type; exact match wins."""
    rt = rtype.lower()
    exact = (cmap.get("resources", {}) or {}).get(rt)
    if exact:
        return [c.upper() for c in exact.get("controls", [])], exact.get("role", "DevSecOps")
    for rule in cmap.get("token_rules", []) or []:
        if any(str(tok).lower() in rt for tok in rule.get("match", [])):
            return [c.upper() for c in rule.get("controls", [])], rule.get("role", "DevSecOps")
    return [], "DevSecOps"


def _baseline_controls(il_level: str) -> list[str]:
    """NIST 800-53 controls required at ``il_level`` (via the existing crosswalk).

    Best-effort — if the crosswalk data/engine is unavailable, returns [] and the
    caller falls back to the mappable-control universe.
    """
    try:
        from tools.compliance.crosswalk_engine import get_controls_for_impact_level

        entries = get_controls_for_impact_level(il_level)
    except Exception as exc:  # noqa: BLE001 — crosswalk is advisory here
        logger.debug("crosswalk baseline unavailable for %s: %s", il_level, exc)
        return []
    ids = set()
    for e in entries:
        cid = (e.get("nist_id") or e.get("nist_800_53") or "").upper()
        if cid:
            ids.add(cid)
    return sorted(ids)


def _frameworks_for(control_id: str) -> dict:
    try:
        from tools.compliance.crosswalk_engine import get_frameworks_for_control

        return get_frameworks_for_control(control_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def map_iac_to_controls(
    design: Any,
    *,
    il_level: str = "IL5",
    map_path: str | Path | None = None,
) -> dict:
    """Compose IaC resources → NIST 800-53 control-implementation evidence.

    Returns a dict with:
      * ``implemented``: ``{control_id: [{resource, resource_type, statement,
        citation, frameworks}]}`` — each statement TRUST-cites its IaC resource;
      * ``poam_items``: gap POA&M rows (poam_items-compatible) for infra-relevant
        controls in the baseline that NO resource satisfies;
      * ``control_coverage`` + ``evidence_checklist``.
    """
    cmap = load_control_map(map_path)
    resources = _extract_resources(design)

    implemented: dict[str, list[dict]] = {}
    for r in resources:
        controls, _role = _controls_for_resource(r["type"], cmap)
        for cid in controls:
            citation = f"[source: {r['address']}]"
            statement = (
                f"{cid} is implemented by IaC resource {r['address']} "
                f"(type {r['type']}). {citation}"
            )
            implemented.setdefault(cid, []).append({
                "resource": r["address"],
                "resource_type": r["type"],
                "statement": statement,
                "citation": citation,
                "frameworks": _frameworks_for(cid),
            })

    # The universe of controls that IaC *can* evidence (from the map).
    mappable: set[str] = set()
    for entry in (cmap.get("resources", {}) or {}).values():
        mappable |= {c.upper() for c in entry.get("controls", [])}
    for rule in cmap.get("token_rules", []) or []:
        mappable |= {c.upper() for c in rule.get("controls", [])}

    baseline = _baseline_controls(il_level)
    baseline_set = set(baseline) if baseline else set(mappable)
    # Infra-relevant controls we should be able to evidence with IaC.
    in_scope = sorted(mappable & baseline_set)
    covered = set(implemented.keys())
    gaps = sorted((mappable & baseline_set) - covered)

    milestone = (datetime.now(timezone.utc) + timedelta(days=_GAP_MILESTONE_DAYS)).date().isoformat()
    poam_items = [{
        "weakness_id": f"IDC-TWIN-{cid}",
        "control_id": cid,
        "weakness_description": (
            f"No IaC resource in the design implements NIST 800-53 {cid}; the "
            f"target ATO baseline ({il_level}) requires it."
        ),
        "severity": "moderate",
        "source": "idc-twin-compat",
        "status": "open",
        "corrective_action": (
            f"Add an IaC resource that satisfies {cid} "
            f"(see args/iac_control_map.yaml for eligible resource types)."
        ),
        "milestone_date": milestone,
        "responsible_party": "DevSecOps",
    } for cid in gaps]

    checklist = []
    for cid in in_scope:
        ev = implemented.get(cid, [])
        checklist.append({
            "control_id": cid,
            "status": "satisfied" if ev else "gap",
            "evidence": [e["resource"] for e in ev],
        })

    return {
        "il_level": il_level,
        "implemented": implemented,
        "poam_items": poam_items,
        "evidence_checklist": checklist,
        "control_coverage": {
            "baseline_infra_controls": len(in_scope),
            "covered": len([c for c in in_scope if c in covered]),
            "gaps": len(gaps),
            "extra_covered": sorted(covered - baseline_set),
        },
    }


# ── compatibility report ──────────────────────────────────────────────────────

def _per_resource_verdict(resources: list[dict], violations: list[dict]) -> list[dict]:
    """Assign each resource a pass/warn/fail from violations that reference it."""
    out = []
    for r in resources:
        addr = r["address"].lower()
        rtype = r["type"].lower()
        matched = []
        for v in violations:
            det = (v.get("detail") or "").lower()
            title = (v.get("title") or "").lower()
            if (det and (det in rtype or det in addr)) or (addr and addr in title):
                matched.append(v)
        out.append({
            "resource": r["address"],
            "type": r["type"],
            "verdict": derive_verdict_from_violations(matched) if matched else "pass",
            "violations": [v.get("rule_id") for v in matched],
        })
    return out


def _preset_name(preset: str | dict) -> str:
    if isinstance(preset, str):
        p = get_preset(preset)
        return (p or {}).get("display_name", preset) if p else preset
    return preset.get("display_name", "custom")


def generate_compatibility_report(
    design: Any,
    preset: str | dict,
    *,
    target_id: str = "idc-design",
    source_canvas: str = "idc",
    classification: str = "CUI",
    il_level: str = "IL5",
    presets_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    map_path: str | Path | None = None,
) -> dict:
    """Generate a high-side compatibility report for ``design`` against ``preset``.

    Composes fed-02 target evaluation (which engages fed-01 air-gap rules) with
    the IaC→control ATO mapping, under a classification banner. Deterministic.
    """
    from tools.compliance.classification_manager import get_document_banner

    violations = evaluate_target(
        design, preset, source_canvas=source_canvas,
        presets_path=presets_path, catalog_path=catalog_path,
    )
    resources = _extract_resources(design)
    counts = summarize_violations(violations)
    verdict = derive_verdict_from_violations(violations)

    by_cat: dict[str, list[dict]] = {}
    for v in violations:
        by_cat.setdefault(v.get("category", "compliance"), []).append(v)

    ato = map_iac_to_controls(design, il_level=il_level, map_path=map_path)
    banner = get_document_banner(classification)

    report = {
        "kind": "twin_compatibility_report",
        "target_id": target_id,
        "source_canvas": source_canvas,
        "target_preset": _preset_name(preset),
        "classification": classification,
        "banner": banner,
        "executive": {
            "verdict": verdict,
            "counts": counts,
            "blockers": counts.get("blocker", 0) + counts.get("critical", 0),
            "resource_total": len(resources),
            "ato_control_coverage": ato["control_coverage"],
        },
        "per_resource": _per_resource_verdict(resources, violations),
        "required_changes": {
            "iam": by_cat.get("iam", []),
            "network": by_cat.get("network", []),
            "security": by_cat.get("security", []),
        },
        "dependency_replacements": by_cat.get("service_parity", []),
        "ato": ato,
        "violations": violations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report["content_hash"] = _content_hash(report)
    return report


def _content_hash(report: dict) -> str:
    """Stable hash over the meaningful content (excludes generated_at/banner)."""
    payload = {
        "target_id": report.get("target_id"),
        "target_preset": report.get("target_preset"),
        "executive": {k: v for k, v in report.get("executive", {}).items()},
        "violations": report.get("violations"),
        "ato_coverage": report.get("ato", {}).get("control_coverage"),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── rendered artifact (tools/viz) ─────────────────────────────────────────────

def render_compatibility_report(
    report: dict,
    out_path: str | Path | None = None,
    *,
    theme: str = "midnight_executive",
) -> str:
    """Render ``report`` to a self-contained HTML artifact via ``tools/viz``.

    Returns the HTML string (and writes it to ``out_path`` when given).
    """
    from tools.viz.render_html import kpis_to_html, table_to_html
    from tools.viz.spec import KpiSpec, KpiTile, TableSpec

    ex = report.get("executive", {})
    cov = ex.get("ato_control_coverage", {})
    banner = report.get("banner", {})

    kpis = KpiSpec(title="Compatibility Verdict", tiles=[
        KpiTile(label="Verdict", value=str(ex.get("verdict", "unknown")).upper()),
        KpiTile(label="Blockers", value=str(ex.get("blockers", 0))),
        KpiTile(label="Resources", value=str(ex.get("resource_total", 0))),
        KpiTile(label="ATO controls covered",
                value=f"{cov.get('covered', 0)}/{cov.get('baseline_infra_controls', 0)}"),
    ])

    per_res = TableSpec(
        title="Per-resource compatibility",
        headers=["Resource", "Type", "Verdict", "Findings"],
        rows=[[r["resource"], r["type"], r["verdict"].upper(),
               ", ".join(r["violations"]) or "-"]
              for r in report.get("per_resource", [])] or [["(no resources)", "-", "-", "-"]],
    )
    deps = TableSpec(
        title="Dependency replacements (service not available on high side)",
        headers=["Finding", "Recommendation"],
        rows=[[v.get("title", ""), v.get("recommendation", "")]
              for v in report.get("dependency_replacements", [])] or [["(none)", "-"]],
    )
    poam = TableSpec(
        title="ATO gaps (POA&M candidates)",
        headers=["Weakness", "Control", "Corrective action", "Milestone"],
        rows=[[p["weakness_id"], p["control_id"], p["corrective_action"], p["milestone_date"]]
              for p in report.get("ato", {}).get("poam_items", [])] or [["(none)", "-", "-", "-"]],
    )

    parts = [
        f'<pre style="font-family:monospace;white-space:pre-wrap;">{banner.get("header", "")}</pre>',
        f'<h2>High-Side Compatibility Report — {report.get("target_preset", "")}</h2>',
        kpis_to_html(kpis, theme),
        table_to_html(per_res, theme),
        table_to_html(deps, theme),
        table_to_html(poam, theme),
        f'<pre style="font-family:monospace;white-space:pre-wrap;">{banner.get("footer", "")}</pre>',
    ]
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Compatibility Report — {report.get('target_preset', '')}</title></head>"
        f"<body style='background:#0b1020;color:#e6ecff;padding:24px;'>{''.join(parts)}</body></html>"
    )
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(html, encoding="utf-8")
    return html


# ── persistence (twin_compat_reports — dedup + retention, NOT append-only) ────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS twin_compat_reports (
    id             TEXT PRIMARY KEY,
    target_id      TEXT NOT NULL,
    source_canvas  TEXT,
    target_preset  TEXT,
    verdict        TEXT,
    blocker_count  INTEGER NOT NULL DEFAULT 0,
    content_hash   TEXT NOT NULL,
    report_json    TEXT NOT NULL,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_twin_compat_target ON twin_compat_reports(target_id, created_at);
"""


def _ensure_schema(conn) -> None:
    for stmt in _SCHEMA.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except (RuntimeError, ValueError):
            pass  # DDL already applied on some PG wrappers
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass


def persist_report(
    report: dict,
    *,
    conn=None,
    tenant_id: str | None = None,
    retention: int = _DEFAULT_RETENTION,
    db_path: str | None = None,
) -> dict:
    """Persist a report with content-hash dedup + bounded retention per target.

    PDC snapshot pattern (NOT append-only): if the newest row for this
    ``target_id`` already has the same ``content_hash`` the write is a no-op
    (``deduped=True``); otherwise insert and prune to ``retention`` newest rows.
    """
    import uuid

    own = False
    if conn is None:
        from tools.db.storage import get_connection

        conn = get_connection(db_path)  # translating wrapper (%s→? on sqlite)
        own = True
    _ensure_schema(conn)

    target_id = report.get("target_id", "idc-design")
    chash = report.get("content_hash") or _content_hash(report)
    try:
        row = conn.execute(
            "SELECT content_hash FROM twin_compat_reports WHERE target_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (target_id,),
        ).fetchone()
        latest = (row[0] if isinstance(row, (list, tuple)) else (row["content_hash"] if row else None)) if row else None
        if latest == chash:
            return {"id": None, "deduped": True, "target_id": target_id}

        rid = "compat-" + uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO twin_compat_reports "
            "(id, target_id, source_canvas, target_preset, verdict, blocker_count, "
            "content_hash, report_json, tenant_id, classification, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                rid, target_id, report.get("source_canvas"), report.get("target_preset"),
                report.get("executive", {}).get("verdict"),
                int(report.get("executive", {}).get("blockers", 0)),
                chash, json.dumps(report), tenant_id,
                report.get("classification", "CUI"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        # Retention: keep the newest ``retention`` rows for this target.
        rows = conn.execute(
            "SELECT id FROM twin_compat_reports WHERE target_id = %s ORDER BY created_at DESC",
            (target_id,),
        ).fetchall()
        ids = [(r[0] if isinstance(r, (list, tuple)) else r["id"]) for r in rows]
        for old in ids[retention:]:
            conn.execute("DELETE FROM twin_compat_reports WHERE id = %s", (old,))
        conn.commit()
        return {"id": rid, "deduped": False, "target_id": target_id}
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ── ATO-acceleration wiring: feed twin evidence into the cATO engine's tables ─

def feed_cato_evidence(
    report: dict,
    project_id: str,
    *,
    conn=None,
    tenant_id: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Write the report's IaC-grounded control evidence into the tables the ATO
    engines read (``project_controls`` for ``ssp_generator`` / cATO readiness,
    ``poam_items`` for ``poam_generator``), so cATO picks up the IDC-twin
    evidence without re-implementing OSCAL/SSP/POA&M.

    Idempotent: control rows upsert on ``(project_id, control_id)``; POA&M rows
    skip on an existing ``weakness_id``. Best-effort per row — a missing table or
    constraint on one row never aborts the batch; failures are counted + returned.
    """
    own = False
    if conn is None:
        from tools.db.storage import get_connection

        conn = get_connection(db_path)  # translating wrapper (%s→? on sqlite)
        own = True

    classification = report.get("classification", "CUI")
    ato = report.get("ato", {})
    controls_written = 0
    poam_written = 0
    errors: list[str] = []

    try:
        for cid, evidence in (ato.get("implemented", {}) or {}).items():
            if not evidence:
                continue
            role = "DevSecOps"
            statements = "; ".join(e["statement"] for e in evidence)
            evidence_path = ", ".join(sorted({e["resource"] for e in evidence}))
            try:
                exists = conn.execute(
                    "SELECT id FROM project_controls WHERE project_id = %s AND control_id = %s",
                    (project_id, cid),
                ).fetchone()
                if exists:
                    conn.execute(
                        "UPDATE project_controls SET implementation_status = %s, "
                        "implementation_description = %s, responsible_role = %s, "
                        "evidence_path = %s WHERE project_id = %s AND control_id = %s",
                        ("implemented", statements, role, evidence_path, project_id, cid),
                    )
                else:
                    conn.execute(
                        "INSERT INTO project_controls "
                        "(project_id, control_id, implementation_status, "
                        "implementation_description, responsible_role, evidence_path, "
                        "classification) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (project_id, cid, "implemented", statements, role,
                         evidence_path, classification),
                    )
                controls_written += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"project_controls {cid}: {exc}")

        for p in ato.get("poam_items", []) or []:
            try:
                exists = conn.execute(
                    "SELECT id FROM poam_items WHERE project_id = %s AND weakness_id = %s",
                    (project_id, p["weakness_id"]),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO poam_items "
                    "(project_id, weakness_id, weakness_description, severity, source, "
                    "control_id, status, corrective_action, milestone_date, "
                    "responsible_party, classification) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (project_id, p["weakness_id"], p["weakness_description"], p["severity"],
                     p["source"], p["control_id"], p["status"], p["corrective_action"],
                     p["milestone_date"], p.get("responsible_party", "DevSecOps"),
                     classification),
                )
                poam_written += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"poam_items {p['weakness_id']}: {exc}")

        try:
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
        return {
            "project_id": project_id,
            "controls_written": controls_written,
            "poam_written": poam_written,
            "errors": errors,
        }
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Twin high-side compatibility report (twx-fed-03)")
    ap.add_argument("--design", required=True, help="Path to a design graph / terraform plan JSON")
    ap.add_argument("--preset", required=True, help="Target preset name (see args/twin_target_presets.yaml)")
    ap.add_argument("--target-id", default="idc-design")
    ap.add_argument("--il-level", default="IL5")
    ap.add_argument("--classification", default="CUI")
    ap.add_argument("--render", help="Write an HTML artifact to this path")
    ap.add_argument("--persist", action="store_true", help="Persist to twin_compat_reports")
    ap.add_argument("--feed-cato", metavar="PROJECT_ID", help="Write evidence into project_controls/poam_items")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.design, encoding="utf-8") as fh:
        design = json.load(fh)

    report = generate_compatibility_report(
        design, args.preset, target_id=args.target_id,
        il_level=args.il_level, classification=args.classification,
    )
    if args.render:
        render_compatibility_report(report, args.render)
    if args.persist:
        report["_persist"] = persist_report(report)
    if args.feed_cato:
        report["_feed_cato"] = feed_cato_evidence(report, args.feed_cato)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        ex = report["executive"]
        print(f"Verdict: {ex['verdict'].upper()}  blockers={ex['blockers']}  "
              f"resources={ex['resource_total']}  "
              f"ATO {ex['ato_control_coverage']['covered']}/"
              f"{ex['ato_control_coverage']['baseline_infra_controls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
