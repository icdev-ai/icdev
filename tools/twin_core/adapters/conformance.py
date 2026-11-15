# CUI // SP-CTI — Feature-conformance audit governance twin (icx-cnf-03)
"""Governance twin over the feature-conformance audit (icx-cnf-01).

Canvas key ``twin_observatory`` (the audit's console lives there), observing the
feature ``conformance.audit``. The audit itself probes the whole url_map (~25s),
so this twin does not re-run it. The smallest meaningful observation is whether
its RECORD holds together:

* the committed snapshot (``docs/audits/conformance-cortex-coworker.json``) is
  readable;
* the census (``args/feature_conformance_census.txt``) lists exactly the gaps the
  snapshot has, and no more than ``census_max`` — the conditions ``--check``
  enforces, read without the probe;
* each feature with open gaps is a finding, threaded to its snapshot entry.

What it cannot see: whether the snapshot still matches the repository. Only
``feature_audit.py --check`` proves that, and the coherence check runs it.

Threads: ``conformance:<feature key>`` and ``conformance:census``.
"""
from __future__ import annotations

import hashlib

from tools.twin_core.governance import (
    GovernanceTwinAdapter,
    check,
    finding,
    load_policy,
    now_iso,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin


@register_twin
class ConformanceAuditTwin(GovernanceTwinAdapter):
    canvas_key = "twin_observatory"
    display_name = "Feature conformance audit"
    observes = ("conformance.audit",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.twin_core.governance import BASE_DIR

        ev: dict = {"canvas": self.canvas_key, "target_id": "conformance.audit",
                    "observed_at": now_iso(), "policy": load_policy("conformance"),
                    "snapshot": None, "census": None, "errors": {}}
        try:
            from tools.conformance.feature_audit import (
                RULES,
                gap_keys,
                load_config,
                read_census,
            )

            cfg = load_config(BASE_DIR)
            snap_path = BASE_DIR / cfg.get("snapshot", "docs/audits/conformance-cortex-coworker.json")
            import json

            raw = snap_path.read_bytes()
            report = json.loads(raw.decode("utf-8"))
            ev["snapshot"] = {
                "path": snap_path.relative_to(BASE_DIR).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "enforcement": report.get("enforcement"),
                "features": [
                    {"key": f["key"], "label": f.get("label", ""),
                     "gaps": sorted(f"{rule}:{g['id']}" for rule in RULES
                                    for g in f["cells"][rule]["gaps"])}
                    for f in sorted(report.get("features") or [], key=lambda f: f["key"])
                ],
                "gap_keys": gap_keys(report),
            }
            census_path = BASE_DIR / cfg.get("census", "args/feature_conformance_census.txt")
            ev["census"] = {"path": census_path.relative_to(BASE_DIR).as_posix(),
                            "lines": read_census(census_path),
                            "census_max": int(cfg.get("census_max") or 0),
                            "readable": census_path.exists()}
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"]["snapshot"] = str(exc)
        return ev

    def judge(self, evidence: dict) -> dict:
        errors = evidence.get("errors") or {}
        checks, findings = [], []
        snap = evidence.get("snapshot")
        census = evidence.get("census")
        checks.append(check("conformance.snapshot", "Conformance snapshot", snap is not None,
                            errors.get("snapshot") or "snapshot not read"))
        checks.append(check("conformance.census", "Census against the snapshot",
                            bool(census and census["readable"]),
                            errors.get("snapshot") or "census file not read"))
        if snap is not None and census and census["readable"]:
            listed, found = set(census["lines"]), set(snap["gap_keys"])
            if listed != found:
                findings.append(finding(
                    "conformance.census.stale", "critical",
                    "The census and the snapshot disagree",
                    thread_id="conformance:census",
                    detail=f"{len(found - listed)} gap(s) not in the census, "
                           f"{len(listed - found)} closed gap(s) still listed",
                    recommendation="Run feature_audit.py --markdown --census and review the diff."))
            if len(listed) > census["census_max"]:
                findings.append(finding(
                    "conformance.census.over", "critical",
                    f"Census {len(listed)} is over census_max {census['census_max']}",
                    thread_id="conformance:census",
                    recommendation="census_max may only go down; close gaps instead."))
        for f in (snap or {}).get("features") or []:
            if f["gaps"]:
                findings.append(finding(
                    "conformance.feature.gaps", "medium",
                    f"{f['label'] or f['key']}: {len(f['gaps'])} open gap(s)",
                    thread_id=f"conformance:{f['key']}", detail=", ".join(f["gaps"]),
                    recommendation="See docs/audits/conformance-cortex-coworker.md for the "
                                   "remediation card that closes each."))
        summary = {
            "features": len((snap or {}).get("features") or []),
            "conformant": sum(1 for f in (snap or {}).get("features") or [] if not f["gaps"]),
            "open_gaps": len((snap or {}).get("gap_keys") or []),
            "census_max": (census or {}).get("census_max"),
            "enforcement": (snap or {}).get("enforcement"),
        }
        return {"checks": checks, "findings": findings, "summary": summary}
