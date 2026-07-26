#!/usr/bin/env python3
# CUI // SP-CTI
"""App red team over HTTP (oss-redteam-01).

Nothing exercised the RUNNING app for security. Everything in tools/security/
operated on files, config and DB rows; atlas_red_team is static, llm_red_team
targets a model, and the DAST node types in the pipeline only ever generated CI
YAML that was never run.

This is modelled on tools/security/llm_red_team.py exactly — catalog in args/,
detectors, a findings summary, OWASP mapping, ``--gate`` with a non-zero exit —
but over HTTP against our OWN dashboard.

Three hard constraints, none optional:

* **Every request clears the scope-lock** (oss-redteam-02). ``assert_in_scope``
  is called before the client is ever handed a URL, and it RAISES on refusal.
  There is no path that reaches the network without passing it.
* **Every finding must satisfy oss-poc-01.** A probe failing its expectation is a
  *lead*, not a finding, until its reproduction is shown to discriminate. This
  module produces leads and hands them to tools/security/reproduction.py; it
  never mints a confirmed finding on its own.
* **Public-repo redaction.** Specifics never reach a public surface. The CLI's
  default output is the redacted count-only summary; the localising detail goes
  to the private triage path.

Probe families come from where real defects have occurred here — authz matrix,
tenant isolation, classification, IDOR, CSRF — not a generic wordlist. There is
no general-scanner mode by construction: the catalog is fixed and first-party.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger
from tools.security.redteam_scope import (
    RedTeamScope,
    ScopeViolation,
    assert_in_scope,
    load_scope,
    public_summary,
)

logger = get_logger("icdev.security.app_red_team")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CATALOG = BASE_DIR / "args" / "redteam_app_catalog.yaml"


@dataclass
class Probe:
    """One behavioural expectation about the app."""

    id: str
    family: str
    owasp: str
    severity: str
    actor: str
    expectation: str
    detect: Dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict) -> "Probe":
        return cls(
            id=d.get("id", ""),
            family=d.get("family", ""),
            owasp=d.get("owasp", ""),
            severity=d.get("severity", "low"),
            actor=d.get("actor", "anonymous"),
            expectation=d.get("expectation", ""),
            detect=dict(d.get("detect") or {}),
        )


@dataclass
class ProbeLead:
    """A probe whose expectation the app failed — a LEAD, not yet a finding.

    Kept distinct from a confirmed finding on purpose: promotion to a finding
    requires a discriminating reproduction (oss-poc-01), which this module does
    not perform. The naming is the guardrail.
    """

    probe_id: str
    family: str
    owasp: str
    severity: str
    failed: bool
    detail: str = ""                     # PRIVATE — never public
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"lead-{uuid.uuid4().hex[:12]}"

    def public_row(self) -> Dict[str, Any]:
        """Redacted view: severity and family only, no locating detail."""
        return {"family": self.family, "severity": self.severity, "owasp": self.owasp}


def load_catalog(path: Path = DEFAULT_CATALOG) -> List[Probe]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Probe.from_dict(p) for p in (data.get("probes") or [])]


# ── Detectors ────────────────────────────────────────────────────────────────
#
# Each returns True when the app FAILED the expectation (the probe "fired").
# Deliberately small and declarative, mirroring llm_red_team's detector set.


def _det_status_not_in(observation: Dict[str, Any], spec: dict) -> bool:
    allowed = set(spec.get("allowed") or [])
    return observation.get("status") not in allowed


def _det_no_foreign_tenant_rows(observation: Dict[str, Any], spec: dict) -> bool:
    rows = observation.get("rows") or []
    asked = observation.get("tenant")
    return any(r.get("tenant") not in (asked, None) for r in rows)


def _det_no_higher_classification_rows(observation: Dict[str, Any], spec: dict) -> bool:
    order = {"UNCLASS": 0, "CUI": 1, "SECRET": 2, "TOPSECRET": 3}
    clearance = order.get(str(observation.get("clearance", "CUI")).upper(), 1)
    rows = observation.get("rows") or []
    return any(
        order.get(str(r.get("classification", "CUI")).upper(), 1) > clearance
        for r in rows
    )


_DETECTORS: Dict[str, Callable[[Dict[str, Any], dict], bool]] = {
    "status_not_in": _det_status_not_in,
    "no_foreign_tenant_rows": _det_no_foreign_tenant_rows,
    "no_higher_classification_rows": _det_no_higher_classification_rows,
}


def evaluate(probe: Probe, observation: Dict[str, Any]) -> bool:
    """True when the app failed the probe's expectation."""
    det = _DETECTORS.get(probe.detect.get("kind", ""))
    if det is None:
        logger.warning("probe %s: unknown detector %r", probe.id, probe.detect.get("kind"))
        return False
    return det(observation, probe.detect)


def run(
    target_url: str,
    observer: Callable[[Probe], Dict[str, Any]],
    catalog: Optional[List[Probe]] = None,
    scope: Optional[RedTeamScope] = None,
) -> Dict[str, Any]:
    """Run the catalog against *target_url* and return LEADS.

    ``observer`` performs the actual request for a probe and returns an
    observation dict. It is injected rather than built in so this module has no
    direct network dependency in tests, and so the ONE place that talks to the
    network can be audited — the observer is where ``assert_in_scope`` bites.

    Returns a result carrying a public (redacted) summary and the private leads.
    The caller decides what crosses to a public surface; the default is nothing
    but the summary.
    """
    scope = scope or load_scope()
    # The choke point. Refused targets raise here, before any probe runs.
    target = assert_in_scope(target_url, scope)

    probes = catalog if catalog is not None else load_catalog()
    leads: List[ProbeLead] = []
    for probe in probes:
        try:
            observation = observer(probe)
        except ScopeViolation:
            raise                                    # scope refusals must propagate
        except Exception as exc:  # noqa: BLE001
            logger.warning("probe %s: observation failed (%s)", probe.id, exc)
            continue
        failed = evaluate(probe, observation)
        if failed:
            leads.append(
                ProbeLead(
                    probe_id=probe.id,
                    family=probe.family,
                    owasp=probe.owasp,
                    severity=probe.severity,
                    failed=True,
                    detail=f"{probe.expectation} — observed {observation.get('status', observation)}",
                )
            )

    public = public_summary([lead.public_row() for lead in leads])
    return {
        "target": target,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "probes_run": len(probes),
        "leads": leads,                              # PRIVATE
        "public_summary": public,                    # safe to surface
        "note": (
            "Leads are not findings. Each must be confirmed by a discriminating "
            "reproduction (oss-poc-01) before it can gate or be filed."
        ),
    }


def gate_exit_code(result: Dict[str, Any]) -> int:
    """1 if any HIGH-severity lead was raised, else 0.

    Mirrors llm_red_team's --gate. Note this gates on LEADS for a fast signal;
    a lead becomes a blocking FINDING only after oss-poc-01 confirmation, which
    is the authoritative gate. This exit code is the smoke alarm, not the ruling.
    """
    return 1 if any(
        lead.severity.lower() == "high" for lead in result.get("leads", [])
    ) else 0


def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scope-locked app red team (oss-redteam-01/02)"
    )
    parser.add_argument("--target", default="http://localhost:5050",
                        help="Target URL. Must clear the scope-lock (loopback by default).")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--list", action="store_true", help="List the probe catalog and exit")
    parser.add_argument("--scope", action="store_true", help="Print the active scope and exit")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 if any HIGH-severity lead is raised")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)

    if args.scope:
        print(json.dumps(load_scope().to_dict(), indent=2))
        return 0

    if args.list:
        probes = load_catalog(args.catalog)
        rows = [{"id": p.id, "family": p.family, "severity": p.severity, "owasp": p.owasp}
                for p in probes]
        print(json.dumps(rows, indent=2) if args.json_out
              else "\n".join(f"{r['severity']:6s} {r['family']:16s} {r['id']}" for r in rows))
        return 0

    # No built-in live observer: wiring a real authenticated HTTP client to the
    # running dashboard is deliberately a separate, reviewed step. Refusing to
    # ship a turnkey "scan anything now" entry point IS the point of oss-redteam-02.
    print(json.dumps({
        "error": "no live observer wired",
        "reason": (
            "This CLI validates catalog + scope. Executing against a live target "
            "requires an authenticated observer supplied through the API, so that "
            "the scope-lock and authorization record are enforced per run rather "
            "than assumed. See tools/security/app_red_team.run()."
        ),
        "scope": load_scope().to_dict(),
        "probe_count": len(load_catalog(args.catalog)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
