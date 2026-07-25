# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV(TM) System Administrator
"""Contract Clause Risk Engine (crx-gov-02).

Deterministic-first clause risk analysis for incoming solicitations / contracts.

Pipeline:
  1. Clause extraction  -- reuses far_dfars_verifier.detect_clauses() for the
     FAR/DFARS clause catalog (graceful if unavailable).
  2. Indicator detection -- regex indicators from args/govcon/clause_risk_rules.yaml.
  3. Risk-rule evaluation -- deterministic combination rules (e.g. FFP + unbounded
     scope) produce severity + rationale + mitigation, each citing its FAR/DFARS
     clause source. These findings alone determine the numeric risk score.
  4. LLM narrative (OPTIONAL, GATED) -- only after the deterministic pass; the LLM
     EXPLAINS the already-computed findings and NEVER changes the score. Degrades
     silently to None when no provider is configured.

TRUST: every finding carries an inline [source: rule:<id>] / [source: indicator:<id>]
citation plus its FAR/DFARS clause_source. Rules are seeded from PUBLIC FAR/DFARS
knowledge only.

Usage:
    python tools/govcon/clause_risk_engine.py --text "..." --json
    python tools/govcon/clause_risk_engine.py --text-file solicitation.txt --json
    python tools/govcon/clause_risk_engine.py --opportunity-id opp-123 --text "..." --assist --json
    python tools/govcon/clause_risk_engine.py --list-rules --json
    python tools/govcon/clause_risk_engine.py --text "..." --export --format md
"""
import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# =========================================================================
# PATH SETUP
# =========================================================================
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

# Rulebook lives next to the module tree; resolve relative to repo root so both
# the canonical tools/ and mirrored icdev/tools/ copies find it.
_RULEBOOK_PATH = Path(
    os.environ.get(
        "GOVCON_CLAUSE_RISK_RULES",
        str(_ROOT / "args" / "govcon" / "clause_risk_rules.yaml"),
    )
)

_DEFAULT_SEVERITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_DEFAULT_SCORE_BANDS = {"high": 6, "medium": 3, "low": 1}
_SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


# =========================================================================
# DATA MODELS
# =========================================================================
@dataclass
class IndicatorHit:
    """A regex indicator matched in the solicitation text."""
    indicator_id: str
    label: str
    clause_source: str
    evidence: str  # the matched span (trimmed)


@dataclass
class RiskFinding:
    """A fired deterministic risk rule (toxic combination)."""
    rule_id: str
    name: str
    severity: str
    rationale: str
    mitigation: str
    clause_source: str
    matched_indicators: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    citation: str = ""  # inline [source: rule:<id>] provenance marker


@dataclass
class ClauseRiskReport:
    """End-to-end deterministic clause risk assessment."""
    opportunity_id: str
    generated_at: str
    input_hash: str
    detected_clauses: List[Dict[str, Any]] = field(default_factory=list)
    indicator_hits: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    risk_score: int = 0
    risk_level: str = "none"  # none | low | medium | high | critical
    critical_findings: int = 0
    high_findings: int = 0
    rationale: str = ""
    llm_narrative: Optional[str] = None  # gated explanation; never affects score
    llm_narrative_source: Optional[str] = None  # model/provider or "unavailable"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =========================================================================
# RULEBOOK LOADING (cached)
# =========================================================================
_RULEBOOK_CACHE: Dict[str, Any] = {}


def load_rulebook(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load + cache the YAML rulebook. Returns a dict with compiled patterns."""
    p = Path(path) if path else _RULEBOOK_PATH
    key = str(p.resolve())
    if key in _RULEBOOK_CACHE:
        return _RULEBOOK_CACHE[key]

    import yaml  # local import; pyyaml is an ICDEV core dep

    with open(p, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    indicators: Dict[str, Dict[str, Any]] = {}
    for ind in raw.get("indicators", []) or []:
        iid = ind.get("id")
        if not iid or not ind.get("pattern"):
            continue
        try:
            compiled = re.compile(ind["pattern"])
        except re.error:
            continue
        indicators[iid] = {
            "id": iid,
            "label": ind.get("label", iid),
            "clause_source": ind.get("clause_source", ""),
            "regex": compiled,
        }

    rules: List[Dict[str, Any]] = []
    for rule in raw.get("risk_rules", []) or []:
        rid = rule.get("id")
        if not rid:
            continue
        rules.append(
            {
                "id": rid,
                "name": rule.get("name", rid),
                "severity": (rule.get("severity") or "medium").lower(),
                "all_of": list(rule.get("all_of", []) or []),
                "any_of": list(rule.get("any_of", []) or []),
                "none_of": list(rule.get("none_of", []) or []),
                "rationale": (rule.get("rationale") or "").strip(),
                "mitigation": (rule.get("mitigation") or "").strip(),
                "clause_source": rule.get("clause_source", ""),
            }
        )

    book = {
        "version": raw.get("version", 1),
        "severity_weights": {**_DEFAULT_SEVERITY_WEIGHTS, **(raw.get("severity_weights") or {})},
        "score_bands": {**_DEFAULT_SCORE_BANDS, **(raw.get("score_bands") or {})},
        "indicators": indicators,
        "rules": rules,
    }
    _RULEBOOK_CACHE[key] = book
    return book


# =========================================================================
# DETERMINISTIC DETECTION
# =========================================================================
def _trim(span: str, limit: int = 160) -> str:
    span = " ".join(span.split())
    return span if len(span) <= limit else span[:limit] + "..."


def detect_indicators(text: str, rulebook: Optional[Dict[str, Any]] = None) -> List[IndicatorHit]:
    """Match every regex indicator against the text (deterministic)."""
    book = rulebook or load_rulebook()
    hits: List[IndicatorHit] = []
    for iid, ind in book["indicators"].items():
        m = ind["regex"].search(text or "")
        if m:
            hits.append(
                IndicatorHit(
                    indicator_id=iid,
                    label=ind["label"],
                    clause_source=ind["clause_source"],
                    evidence=_trim(m.group(0)),
                )
            )
    return hits


def extract_clauses(text: str) -> List[Dict[str, Any]]:
    """Reuse the FAR/DFARS clause catalog from far_dfars_verifier (graceful)."""
    try:
        from tools.govcon.far_dfars_verifier import detect_clauses
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    try:
        for dc in detect_clauses(text or ""):
            out.append(
                {
                    "clause_id": dc.clause_id,
                    "title": dc.title,
                    "severity": dc.severity,
                    "family": dc.family,
                    "source": dc.source,
                }
            )
    except Exception:
        return []
    return out


def evaluate_rules(text: str, rulebook: Optional[Dict[str, Any]] = None) -> List[RiskFinding]:
    """Fire deterministic combination rules against detected indicators."""
    book = rulebook or load_rulebook()
    hits = {h.indicator_id: h for h in detect_indicators(text, book)}
    present = set(hits.keys())
    findings: List[RiskFinding] = []

    for rule in book["rules"]:
        all_of = set(rule["all_of"])
        any_of = set(rule["any_of"])
        none_of = set(rule["none_of"])

        if all_of and not all_of.issubset(present):
            continue
        if any_of and not (any_of & present):
            continue
        if none_of and (none_of & present):
            continue
        if not all_of and not any_of:
            continue  # a rule with no positive condition never fires

        matched = sorted((all_of | any_of) & present)
        evidence = [f"{hits[i].label}: \"{hits[i].evidence}\"" for i in matched]
        findings.append(
            RiskFinding(
                rule_id=rule["id"],
                name=rule["name"],
                severity=rule["severity"],
                rationale=rule["rationale"],
                mitigation=rule["mitigation"],
                clause_source=rule["clause_source"],
                matched_indicators=matched,
                evidence=evidence,
                citation=f"[source: rule:{rule['id']}]",
            )
        )
    return findings


def _score(findings: List[RiskFinding], rulebook: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic aggregate scoring. Critical rule => critical overall."""
    weights = rulebook["severity_weights"]
    bands = rulebook["score_bands"]
    total = sum(int(weights.get(f.severity, 1)) for f in findings)
    n_crit = sum(1 for f in findings if f.severity == "critical")
    n_high = sum(1 for f in findings if f.severity == "high")

    if n_crit > 0:
        level = "critical"
    elif total >= bands["high"]:
        level = "high"
    elif total >= bands["medium"]:
        level = "medium"
    elif total >= bands["low"]:
        level = "low"
    else:
        level = "none"
    return {"risk_score": total, "risk_level": level, "critical": n_crit, "high": n_high}


# =========================================================================
# LLM NARRATIVE (OPTIONAL, GATED BEHIND THE DETERMINISTIC PASS)
# =========================================================================
def _llm_narrative(report: ClauseRiskReport) -> Optional[tuple]:
    """Generate an explanatory narrative for the ALREADY-computed findings.

    The LLM never sees a scoring role: it is handed the deterministic score and
    findings and asked only to explain them in prose. Returns (text, source) or
    None when no provider is configured / on any failure (graceful degrade).
    """
    if not report.findings:
        return None
    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        if not router.has_any_llm():
            return None
        from tools.llm.provider import LLMRequest
    except Exception:
        return None

    lines = [
        f"Deterministic clause-risk assessment (score={report.risk_score}, "
        f"level={report.risk_level}). Findings already computed by the rule engine:",
    ]
    for f in report.findings:
        lines.append(
            f"- [{f['severity'].upper()}] {f['name']} (clause: {f['clause_source']}); "
            f"rationale: {f['rationale']}"
        )
    context = "\n".join(lines)
    system = (
        "You are a DoD contracts risk advisor. You are given a DETERMINISTIC clause "
        "risk assessment that has ALREADY been scored by a rule engine. Do NOT change, "
        "re-score, or dispute the score or severities. Write a concise (<=180 words) "
        "plain-text narrative that EXPLAINS the findings to a capture manager and "
        "summarizes the recommended mitigations. Cite clause sources as given."
    )
    try:
        req = LLMRequest(
            messages=[{"role": "user", "content": context}],
            system_prompt=system,
            classification="CUI",
            effort="low",
        )
        resp = router.invoke("clause_risk_narrative", req)
        if resp and getattr(resp, "content", None):
            src = getattr(resp, "model_id", None) or getattr(resp, "provider", None) or "llm"
            return (resp.content.strip(), str(src))
    except Exception:
        return None
    return None


# =========================================================================
# END-TO-END ASSESSMENT
# =========================================================================
def assess(
    text: str,
    opportunity_id: str = "",
    *,
    use_llm: bool = False,
    rulebook: Optional[Dict[str, Any]] = None,
) -> ClauseRiskReport:
    """Run the full deterministic-first clause risk assessment."""
    book = rulebook or load_rulebook()
    text = text or ""
    input_hash = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()

    hits = detect_indicators(text, book)
    findings = evaluate_rules(text, book)
    clauses = extract_clauses(text)
    scored = _score(findings, book)

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 0), reverse=True)

    if findings:
        top = findings[0]
        rationale = (
            f"{len(findings)} clause-risk rule(s) fired; highest severity "
            f"'{top.severity}' from '{top.name}'. Overall level: {scored['risk_level']}."
        )
    else:
        rationale = "No toxic clause combinations detected by the deterministic rulebook."

    report = ClauseRiskReport(
        opportunity_id=opportunity_id or "",
        generated_at=datetime.now(timezone.utc).isoformat(),
        input_hash=input_hash,
        detected_clauses=clauses,
        indicator_hits=[asdict(h) for h in hits],
        findings=[asdict(f) for f in findings],
        risk_score=scored["risk_score"],
        risk_level=scored["risk_level"],
        critical_findings=scored["critical"],
        high_findings=scored["high"],
        rationale=rationale,
    )

    if use_llm:
        narrative = _llm_narrative(report)
        if narrative:
            report.llm_narrative, report.llm_narrative_source = narrative
        else:
            report.llm_narrative_source = "unavailable"

    return report


# =========================================================================
# PERSISTENCE
# =========================================================================
def _get_db():
    conn = get_connection(db_path=str(_DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _ensure_table(conn) -> None:
    """Create the assessment table on demand (idempotent).

    Carries tenant_id + classification so the row is RLS-describable even though
    the govcon service layer reads with the default (unscoped) context.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS govcon_clause_risk_assessments (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT,
            tenant_id TEXT DEFAULT 'default',
            classification TEXT DEFAULT 'CUI',
            input_hash TEXT,
            risk_score INTEGER,
            risk_level TEXT,
            critical_findings INTEGER,
            high_findings INTEGER,
            findings TEXT,
            indicator_hits TEXT,
            detected_clauses TEXT,
            rationale TEXT,
            llm_narrative TEXT,
            llm_narrative_source TEXT,
            created_at TEXT
        )
        """
    )


def _audit(conn, action: str, details: str = "") -> None:
    """Append-only audit trail entry (NIST AU-2). Best-effort."""
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(id, created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                "govcon.clause_risk",
                "clause_risk_engine",
                action,
                details,
                "govcon",
            ),
        )
    except Exception:
        pass


def persist(report: ClauseRiskReport, *, tenant_id: str = "default", classification: str = "CUI") -> str:
    """Persist an assessment; returns the row id. Best-effort (never raises)."""
    row_id = f"crk-{uuid.uuid4().hex[:12]}"
    try:
        conn = _get_db()
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO govcon_clause_risk_assessments
            (id, opportunity_id, tenant_id, classification, input_hash, risk_score,
             risk_level, critical_findings, high_findings, findings, indicator_hits,
             detected_clauses, rationale, llm_narrative, llm_narrative_source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row_id,
                report.opportunity_id,
                tenant_id,
                classification,
                report.input_hash,
                report.risk_score,
                report.risk_level,
                report.critical_findings,
                report.high_findings,
                json.dumps(report.findings),
                json.dumps(report.indicator_hits),
                json.dumps(report.detected_clauses),
                report.rationale,
                report.llm_narrative,
                report.llm_narrative_source,
                report.generated_at,
            ),
        )
        _audit(
            conn,
            "assess",
            f"opp={report.opportunity_id} level={report.risk_level} score={report.risk_score}",
        )
        conn.commit()
    except Exception:
        pass
    return row_id


# =========================================================================
# EXPORT
# =========================================================================
def export_markdown(report: ClauseRiskReport) -> str:
    """Render a CUI-marked Markdown risk report."""
    lines = [
        "# CUI // SP-CTI",
        "",
        "# Contract Clause Risk Report",
        "",
        f"- Opportunity: {report.opportunity_id or '(ad hoc)'}",
        f"- Generated: {report.generated_at}",
        f"- Overall risk level: **{report.risk_level.upper()}** (score {report.risk_score})",
        f"- Critical findings: {report.critical_findings} | High findings: {report.high_findings}",
        "",
        f"_{report.rationale}_",
        "",
        "## Findings",
        "",
    ]
    if not report.findings:
        lines.append("No toxic clause combinations detected.")
    for f in report.findings:
        lines += [
            f"### [{f['severity'].upper()}] {f['name']} {f['citation']}",
            "",
            f"- **Clause source:** {f['clause_source']}",
            f"- **Rationale:** {f['rationale']}",
            f"- **Mitigation:** {f['mitigation']}",
            f"- **Evidence:** {'; '.join(f['evidence']) or '(indicator match)'}",
            "",
        ]
    if report.detected_clauses:
        lines += ["## Detected FAR/DFARS clauses", ""]
        for c in report.detected_clauses:
            lines.append(f"- {c['clause_id']} - {c['title']} ({c['severity']})")
        lines.append("")
    if report.llm_narrative:
        lines += [
            "## LLM narrative (explanatory only; does not affect the score)",
            "",
            report.llm_narrative,
            "",
            f"_source: {report.llm_narrative_source}_",
            "",
        ]
    lines.append("# CUI // SP-CTI")
    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Contract Clause Risk Engine")
    p.add_argument("--text", help="Solicitation / contract text to assess")
    p.add_argument("--text-file", help="Path to a text file to assess")
    p.add_argument("--opportunity-id", default="", help="Associate the assessment with an opportunity")
    p.add_argument("--assist", action="store_true", help="Add LLM narrative (gated behind deterministic pass)")
    p.add_argument("--persist", action="store_true", help="Write the assessment to the database")
    p.add_argument("--list-rules", action="store_true", help="List loaded indicators + risk rules")
    p.add_argument("--export", action="store_true", help="Export a Markdown report")
    p.add_argument("--format", default="json", choices=["json", "md"], help="Output format")
    p.add_argument("--json", action="store_true", help="JSON output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_rules:
        book = load_rulebook()
        out = {
            "version": book["version"],
            "indicators": [
                {"id": i["id"], "label": i["label"], "clause_source": i["clause_source"]}
                for i in book["indicators"].values()
            ],
            "rules": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "severity": r["severity"],
                    "clause_source": r["clause_source"],
                }
                for r in book["rules"]
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    text = args.text or ""
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    if not text.strip():
        print(json.dumps({"error": "no text provided (use --text or --text-file)"}))
        return 2

    report = assess(text, args.opportunity_id, use_llm=args.assist)
    if args.persist:
        persist(report)

    if args.export or args.format == "md":
        print(export_markdown(report))
        return 0
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
