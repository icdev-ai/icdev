# CUI // SP-CTI
"""Oracle Network Lens — bridges NDC (Network Design Canvas) predictors into the
platform Oracle anticipatory-intelligence engine.

The NDC canvas ships seven working predictors (``tools/network/*_predictor.py``
and ``supply_chain_risk_scorer.py``) that persist scored findings to
append-only ``nc_*`` result tables.  Those findings were invisible to the
platform Oracle — there were zero ``oracle_``/``kanban_`` references anywhere in
``tools/network/``.

This lens closes that gap.  It follows the standard ``BaseLens`` three-phase
pipeline (analyze → score → propose) used by every other lens
(``QualityLens``, ``MigrationLens``, ``WorkflowPatternLens``,
``TrajectoryLens``):

  1. ``analyze()``  — connect READ-ONLY to the NDC canvas DB and pull the most
                      recent high-signal rows from each predictor result table.
                      No heavy predictor re-computation; we read what the
                      predictors already wrote.
  2. ``score()``    — map each NDC finding into an ``OraclePrediction`` (the
                      canonical oracle record shape) with ``lens='network'``,
                      severity/confidence mapped from the NDC risk tier/score,
                      and device/topology context carried in ``data``.
  3. ``propose()``  — attach short, deterministic remediation recommendations so
                      the downstream ``oracle_kanban_bridge_sync`` can
                      materialize suggested kanban cards.

Design constraints (per task ndc-brg-01):
  * READ-ONLY.  We never write to any ``nc_*`` canvas table and never touch the
    predictors themselves.
  * Graceful degradation.  If the canvas DB is unreachable, or a result table is
    absent/empty (e.g. the PNA/PVM migrations have not run), the affected source
    is skipped and the lens returns whatever it could gather — possibly ``[]`` —
    without raising.

Scanner-tier only (zero LLM tokens).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger
from tools.oracle.base_lens import BaseLens, OraclePrediction

logger = get_logger(__name__)

# Oracle lens identity.  ``oracle_lens_status`` / the oracle_predictions listing
# group by this ``lens`` value, so every prediction this lens emits carries
# lens="network" (its "source" in Oracle terms).
LENS_NAME = "network"

# Only surface findings at or above this risk score, OR whose NDC risk tier is
# critical/high.  Keeps low-signal noise out of the Oracle → kanban pipeline.
_MIN_RISK = 0.60
_HIGH_SIGNAL_TIERS = {"critical", "high"}

# Per-table row cap (append-only tables accumulate history; we only want recent
# rows and then dedupe to the latest per subject).
_ROW_CAP = 300

# NDC risk tier → Oracle severity (Oracle severity vocabulary: info|warning|critical).
_TIER_TO_SEVERITY = {
    "critical": "critical",
    "high": "warning",
    "medium": "info",
    "low": "info",
}


def _tier_from_score(score: float) -> str:
    """Derive an NDC-style risk tier from a 0–1 risk score (for tables that have
    no explicit tier column, e.g. nc_vuln_predictions)."""
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _severity_from_tier(tier: str | None) -> str:
    return _TIER_TO_SEVERITY.get((tier or "").lower(), "info")


def _f(row: dict, key: str, default: float = 0.0) -> float:
    """Read a float column defensively."""
    try:
        val = row.get(key)
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _s(row: dict, key: str, default: str = "") -> str:
    val = row.get(key)
    return str(val) if val is not None else default


# ---------------------------------------------------------------------------
# Per-source record builders — (title, description, category, recommendations)
# ---------------------------------------------------------------------------


def _build_eol(row: dict) -> tuple[str, str, list[str]]:
    dev = _s(row, "device_name", "unknown device")
    vendor = _s(row, "vendor")
    model = _s(row, "model")
    eos = _s(row, "eos_date") or "unknown"
    days = row.get("days_remaining")
    cves = int(_f(row, "active_cve_count"))
    hw = " ".join(p for p in (vendor, model) if p).strip() or "device"
    title = f"EOL risk: {dev} ({hw}) — EOS {eos}"
    days_txt = f"{days} days to EOS" if days is not None else "EOS date unknown"
    desc = (
        f"Device {dev} ({hw}) is approaching end-of-support ({days_txt}) "
        f"with {cves} active CVE(s)."
    )
    recs = [
        f"Plan hardware refresh / replacement for {dev} before {eos}.",
        "Confirm vendor EOS/EOL dates and support-contract coverage.",
    ]
    if cves:
        recs.append(f"Prioritise patching the {cves} active CVE(s) on this device.")
    return title, desc, recs


def _build_bgp(row: dict) -> tuple[str, str, list[str]]:
    dev = _s(row, "device_name", "unknown device")
    peer = _s(row, "peer_ip", "unknown peer")
    flaps = int(_f(row, "flap_count_24h"))
    outage = row.get("predicted_outage_hrs")
    title = f"BGP instability: {dev} peer {peer}"
    outage_txt = (
        f"~{float(outage):.1f}h to likely outage" if outage is not None else "elevated flap risk"
    )
    desc = (
        f"BGP session {dev} ↔ {peer} shows {flaps} flap(s) in 24h; "
        f"{outage_txt}."
    )
    recs = [
        f"Investigate BGP session stability on {dev} toward {peer}.",
        "Review interface errors, MTU, and BFD timers for the peering.",
    ]
    return title, desc, recs


def _build_vuln(row: dict) -> tuple[str, str, list[str]]:
    adv = _s(row, "advisory_id", "unknown advisory")
    trend = _s(row, "trend", "stable")
    r30 = _f(row, "risk_score_30d")
    r90 = _f(row, "risk_score_90d")
    title = f"Vulnerability risk rising: advisory {adv} ({trend})"
    desc = (
        f"Predicted composite vulnerability risk for advisory {adv} is {trend}; "
        f"30d={r30:.2f}, 90d={r90:.2f}."
    )
    recs = [
        f"Triage advisory {adv} and schedule remediation before risk peaks.",
        "Correlate against attack surface and KEV to confirm exposure.",
    ]
    return title, desc, recs


def _build_compliance(row: dict) -> tuple[str, str, list[str]]:
    dev = _s(row, "device_name", "unknown device")
    fw = _s(row, "framework", "baseline")
    d2f = row.get("days_to_failure")
    failing = int(_f(row, "critical_controls_failing"))
    title = f"Compliance drift: {dev} ({fw})"
    d2f_txt = f"{d2f} days to projected failure" if d2f is not None else "trending below baseline"
    desc = (
        f"Device {dev} is drifting from {fw} ({d2f_txt}); "
        f"{failing} critical control(s) failing."
    )
    recs = [
        f"Re-apply {fw} hardening baseline to {dev}.",
        "Open a POA&M for the failing critical controls.",
    ]
    return title, desc, recs


def _build_capacity(row: dict) -> tuple[str, str, list[str]]:
    dev = _s(row, "device_name", "unknown device")
    iface = _s(row, "interface_name", "interface")
    d2s = row.get("days_to_saturation")
    util = _f(row, "current_util_pct")
    title = f"Capacity exhaustion: {dev} {iface}"
    d2s_txt = f"~{d2s} days to saturation" if d2s is not None else "saturation projected"
    desc = (
        f"Interface {iface} on {dev} at {util:.0f}% utilisation; {d2s_txt}."
    )
    recs = [
        f"Plan bandwidth upgrade / load-balancing for {dev} {iface}.",
        "Validate the growth trend against recent traffic telemetry.",
    ]
    return title, desc, recs


def _build_change(row: dict) -> tuple[str, str, list[str]]:
    cr = _s(row, "change_request_id", "unknown change")
    dev = _s(row, "device_name", "unknown device")
    prob = _f(row, "failure_probability")
    blast = int(_f(row, "blast_radius_size"))
    title = f"High change-failure risk: {cr} on {dev}"
    desc = (
        f"Change {cr} on {dev} has {prob:.0%} predicted failure probability "
        f"(blast radius {blast})."
    )
    recs = [
        f"Add extra review / rollback plan for change {cr}.",
        "Schedule within an approved maintenance window and dry-run in the twin.",
    ]
    return title, desc, recs


def _build_supply(row: dict) -> tuple[str, str, list[str]]:
    vendor = _s(row, "vendor", "unknown vendor")
    cves = int(_f(row, "cve_count"))
    kev = int(_f(row, "kev_count"))
    devs = int(_f(row, "device_count"))
    title = f"Supply-chain risk: vendor {vendor}"
    desc = (
        f"Vendor {vendor} spans {devs} device(s) with {cves} CVE(s) "
        f"({kev} on the CISA KEV list)."
    )
    recs = [
        f"Review supply-chain exposure for {vendor} devices.",
        "Prioritise KEV-listed CVEs and validate SBOM coverage.",
    ]
    return title, desc, recs


# ---------------------------------------------------------------------------
# Declarative source map — one entry per NDC predictor result table.
# ---------------------------------------------------------------------------


class _Source:
    """Static description of one predictor → oracle mapping."""

    __slots__ = (
        "predictor", "table", "category", "risk_col", "tier_col",
        "conf_col", "key_cols", "order_col", "builder", "risk_invert",
    )

    def __init__(
        self,
        predictor: str,
        table: str,
        category: str,
        risk_col: str,
        tier_col: str | None,
        conf_col: str,
        key_cols: list[str],
        order_col: str,
        builder: Callable[[dict], tuple[str, str, list[str]]],
        risk_invert: bool = False,
    ) -> None:
        self.predictor = predictor
        self.table = table
        self.category = category
        self.risk_col = risk_col
        self.tier_col = tier_col
        self.conf_col = conf_col
        self.key_cols = key_cols
        self.order_col = order_col
        self.builder = builder
        self.risk_invert = risk_invert  # True when the column is a *stability* score


# NOTE: table names below are a fixed internal allowlist (never user input), so
# the f-string interpolation in _pull() is safe (nosec B608).
_SOURCES: list[_Source] = [
    _Source("eol_predictor", "nc_eol_predictions", "eol_risk",
            "risk_score", "risk_tier", "risk_score",
            ["device_name"], "predicted_at", _build_eol),
    _Source("bgp_predictor", "nc_bgp_predictions", "bgp_instability",
            "stability_score", "flap_risk", "confidence",
            ["session_key"], "predicted_at", _build_bgp, risk_invert=True),
    _Source("vuln_predictor", "nc_vuln_predictions", "vuln_risk",
            "risk_score_composite", None, "confidence",
            ["advisory_id"], "predicted_at", _build_vuln),
    _Source("compliance_drift_predictor", "nc_compliance_drift", "compliance_drift",
            "risk_score", "risk_tier", "risk_score",
            ["device_name", "framework"], "assessed_at", _build_compliance),
    _Source("capacity_predictor", "nc_capacity_predictions", "capacity_exhaustion",
            "risk_score", "risk_tier", "confidence",
            ["device_name", "interface_name"], "predicted_at", _build_capacity),
    _Source("change_failure_predictor", "nc_change_risk", "change_failure",
            "failure_probability", "risk_tier", "failure_probability",
            ["change_request_id"], "predicted_at", _build_change),
    _Source("supply_chain_risk_scorer", "nc_supply_chain_risk", "supply_chain_risk",
            "risk_score", "vendor_risk_rating", "risk_score",
            ["vendor"], "assessed_at", _build_supply),
]


class NetworkLens(BaseLens):
    """Oracle lens that surfaces high-signal NDC predictor findings as
    OraclePredictions (source='network')."""

    name = LENS_NAME
    description = "NDC network predictors (EOL, BGP, vuln, compliance, capacity, change, supply-chain)"

    def __init__(self, min_risk: float = _MIN_RISK) -> None:
        self.min_risk = min_risk

    # -- Phase 1 -------------------------------------------------------------
    def analyze(self) -> dict[str, Any]:
        """Read recent high-signal rows from each NDC predictor result table.

        Returns ``{source_category: [row_dict, ...]}``.  Any source whose table
        is missing / empty / unreadable is simply skipped (graceful degrade).
        """
        conn = self._connect()
        if conn is None:
            return {}

        findings: dict[str, list[dict]] = {}
        try:
            for src in _SOURCES:
                rows = self._pull(conn, src)
                if rows:
                    findings[src.category] = rows
        finally:
            try:
                conn.close()
            except Exception:  # nosec B110 — best-effort close on read-only conn
                pass
        return findings

    # -- Phase 2 -------------------------------------------------------------
    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Map each NDC finding into an OraclePrediction (source='network')."""
        by_category = {s.category: s for s in _SOURCES}
        predictions: list[OraclePrediction] = []

        for category, rows in (analysis or {}).items():
            src = by_category.get(category)
            if src is None:
                continue
            for row in rows:
                pred = self._to_prediction(src, row)
                if pred is not None:
                    predictions.append(pred)
        return predictions

    # -- Phase 3 -------------------------------------------------------------
    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Recommendations are attached in score(); nothing more to enrich."""
        return predictions

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _connect():
        """READ-ONLY connection to the NDC canvas DB.

        Imported lazily (and via the module, not the symbol) so tests can
        monkeypatch ``tools.network.db.init_db.get_connection`` and so the large
        init_db module is only imported when the lens actually runs.  Returns
        ``None`` on any failure — the canvas may be absent entirely.
        """
        try:
            from tools.network.db import init_db as _ndc_db

            return _ndc_db.get_connection()
        except Exception as exc:  # canvas DB absent / import error / conn error
            logger.debug("NetworkLens: NDC canvas DB unavailable: %s", exc)
            return None

    def _pull(self, conn, src: _Source) -> list[dict]:
        """Fetch, dedupe-to-latest, and high-signal-filter rows for one source.

        Returns [] (never raises) when the table is missing/empty.
        """
        sql = (
            f"SELECT * FROM {src.table} "  # nosec B608 — table from fixed allowlist
            f"ORDER BY {src.order_col} DESC LIMIT ?"
        )
        try:
            cur = conn.execute(sql, (_ROW_CAP,))
            raw = cur.fetchall()
        except Exception as exc:
            logger.debug("NetworkLens: table %s unreadable (skipped): %s", src.table, exc)
            return []

        seen: set = set()
        out: list[dict] = []
        for r in raw:
            try:
                row = dict(r)
            except Exception:
                continue
            key = tuple(row.get(k) for k in src.key_cols)
            if key in seen:
                continue  # keep only the most-recent row per subject
            seen.add(key)
            if self._is_high_signal(src, row):
                out.append(row)
        return out

    def _is_high_signal(self, src: _Source, row: dict) -> bool:
        risk = self._risk_of(src, row)
        tier = self._tier_of(src, row, risk)
        return risk >= self.min_risk or tier in _HIGH_SIGNAL_TIERS

    @staticmethod
    def _risk_of(src: _Source, row: dict) -> float:
        raw = _f(row, src.risk_col)
        risk = (1.0 - raw) if src.risk_invert else raw
        return max(0.0, min(1.0, risk))

    @staticmethod
    def _tier_of(src: _Source, row: dict, risk: float) -> str:
        if src.tier_col:
            tier = _s(row, src.tier_col).lower()
            if tier:
                return tier
        return _tier_from_score(risk)

    def _to_prediction(self, src: _Source, row: dict) -> OraclePrediction | None:
        try:
            risk = self._risk_of(src, row)
            tier = self._tier_of(src, row, risk)
            severity = _severity_from_tier(tier)
            confidence = max(0.0, min(1.0, _f(row, src.conf_col, risk)))
            title, description, recs = src.builder(row)

            device = _s(row, "device_name") or _s(row, "vendor") or _s(row, "advisory_id")
            data = {
                "source": LENS_NAME,
                "predictor": src.predictor,
                "table": src.table,
                "risk_tier": tier,
                "risk_score": round(risk, 4),
                "device": device,
                "context": self._context(src, row),
            }
            return OraclePrediction(
                lens=LENS_NAME,
                title=title[:200],
                description=description,
                confidence=round(confidence, 4),
                severity=severity,
                category=src.category,
                recommendations=recs,
                data=data,
                # Higher risk → more bearish (lower sentiment weight).
                sentiment_weight=round(max(0.0, min(1.0, 1.0 - risk)), 4),
            )
        except Exception as exc:
            logger.debug("NetworkLens: failed mapping row from %s: %s", src.table, exc)
            return None

    @staticmethod
    def _context(src: _Source, row: dict) -> dict:
        """A small, JSON-safe device/topology context bundle for the card."""
        ctx: dict[str, Any] = {}
        for col in (
            "device_name", "vendor", "model", "os_version", "peer_ip",
            "session_key", "interface_name", "framework", "advisory_id",
            "change_request_id", "eos_date", "days_remaining",
            "days_to_failure", "days_to_saturation",
        ):
            if col in row and row[col] is not None:
                ctx[col] = row[col]
        # Ensure JSON-serialisable (dates/decimals → str).
        try:
            json.dumps(ctx, default=str)
        except Exception:
            ctx = {k: str(v) for k, v in ctx.items()}
        return ctx


# ---------------------------------------------------------------------------
# CLI — quick manual run (scanner-tier, zero tokens)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Oracle Network Lens — NDC predictor bridge")
    p.add_argument("--json", action="store_true", help="Emit predictions as JSON")
    p.add_argument("--min-risk", type=float, default=_MIN_RISK, help="High-signal risk floor")
    args = p.parse_args(argv)

    preds = NetworkLens(min_risk=args.min_risk).run()
    if args.json:
        print(json.dumps([pr.to_dict() for pr in preds], indent=2, default=str))
    else:
        print(f"NetworkLens: {len(preds)} high-signal prediction(s)")
        for pr in preds:
            print(f"  [{pr.severity:8}] {pr.category:20} {pr.title}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
