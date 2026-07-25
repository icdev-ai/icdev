# CUI // SP-CTI
"""Confidence sampler — randomly audit what the system was already sure about.

TRUST gates on confidence. Confidence is a self-report; nothing has ever checked
whether it predicts correctness.

**Why sampling, and not just "look at the errors".** Items above the gate flow
through automatically — a promotion, a redline, a heal — and nobody re-reads
them. Items below it are dropped silently. So errors in the trusted band are not
rare, they are *structurally unobservable*: the only decisions a human ever sees
are the ones the system chose to show. Random sampling is the only thing that
forces a look at what nothing else will ever surface.

**Why it samples below the gate too.** Every oracle_prediction on the live system
is >= 0.7, and it is worth being precise about why, because the obvious
explanation is wrong. The promotion gate does NOT truncate the table: the write
in gap_detector._write_gap_prediction is unconditional, and the confidence filter
lives at READ time in suggested_card_writer._load_pending_predictions. Rejections
would be persisted.

The real reason there is nothing below 0.7 is that no enabled rule ever claims
less. `confidence` is not computed per prediction — it is a per-rule constant
copied out of awareness_config.yaml (`float(rule_config["confidence"])`). Every
enabled rule is hand-assigned 0.70..0.95; the one rule below the gate
(stale_code, 0.50) is `enabled: false`. Hence 423 predictions carry exactly 7
distinct confidence values.

So sampling below the gate costs nothing and stays honest: if a sub-threshold
rule is ever enabled, its rows are already in the table and this reflex will find
them. Today it draws zero there, and that zero is the finding.

**Why stratified.** A flat random draw is dominated by whichever rule fired most
(165 of 423 rows are tool_not_in_manifest alone) and starves the rest.

**Read the bands as rules, not as confidence levels.** Because confidence is a
per-rule constant, a band is mostly a single rule wearing a number: the 0.7 band
is 41/41 route_no_e2e, the 0.9 band is 54/55 broken_test_reference. "The 0.9 band
is right a third of the time" is therefore a claim about one RULE, not about
high confidence in general. `decision` records the prediction_type so the
per-rule cut is recoverable — that is the cut that can actually be acted on.

This reflex writes measurement rows only. It never promotes, resolves, or
modifies the thing it samples — an audit that changes its subject is not an
audit.

Registered in tools/genesis/daemon.py REFLEX_NAMES and args/genesis_config.yaml
(all three points — known gotcha).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import random
import uuid
from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 24

# Sampled rows live under their own reflex name so their statistics stay clean.
# Mixing them with organic rows would pool an unbiased sample with a biased one
# (organic labels exist only where the system chose to surface something), and
# the combined average would inherit the bias while looking like more evidence.
SAMPLE_REFLEX_PREFIX = "sampled:"

_DEFAULT_BANDS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
_DEFAULT_PER_BAND = 3          # reviewer time is the scarce resource
_DEFAULT_BAND_WIDTH = 0.1


def sample_reflex_name(surface: str) -> str:
    return f"{SAMPLE_REFLEX_PREFIX}{surface}"


def _already_sampled(conn, surface: str) -> set[str]:
    """Ids this sampler has already drawn — never re-draw one.

    Re-drawing would let a single item vote twice, and would keep re-asking about
    the same easy cases while the rest of the population stays unaudited.
    """
    try:
        cur = conn.execute(
            "SELECT task_id FROM harness_eval WHERE reflex = %s",
            (sample_reflex_name(surface),),
        )
        return {
            (list(r.values())[0] if isinstance(r, dict) else r[0])
            for r in cur.fetchall()
        }
    except Exception as exc:
        logger.warning("confidence sampler: could not read prior samples: %s", exc)
        # Fail closed: an unreadable history means we cannot guarantee we are not
        # re-drawing, and a duplicate sample corrupts the estimate.
        raise


def _candidates(conn, surface: str) -> list[dict]:
    """Rows eligible for audit: (id, confidence, description).

    Only oracle_predictions for now — it is the one surface with a real
    confidence spread AND a reachable verdict.

    Tombstone filter: only rows whose outcome is still OPEN
    (``NULL`` / ``''`` / ``'pending'``) are drawn. Closed rows are excluded
    for two reasons, both of which keep the sampled statistics honest:

      * ``dismissed*`` rows are tombstones — the operator already ruled the
        prediction a false positive (or a retired rule left it behind). Re-
        auditing a killed prediction and folding a fresh human verdict into
        the calibration aggregate is exactly the "stale tombstone rows keep
        influencing aggregate statistics" defect.
      * ``promoted:*`` rows already have real ground truth via the
        oracle_predictions→kanban_verifications join (the ORGANIC path in
        calibration_report). Sampling them too would double-observe the same
        prediction and let an unbiased sample leak into a biased one — the one
        thing the sampled/organic split exists to prevent.

    This mirrors suggested_card_writer._load_pending_predictions' own
    definition of an open, still-promotable prediction, so the sampler and the
    promoter agree on what "open" means. Below-gate rows are still drawn: they
    are written unconditionally and stay ``pending`` (never promoted), so the
    "samples below the gate too" property is preserved.
    """
    if surface != "oracle_triage":
        raise ValueError(f"unsupported sampling surface: {surface!r}")
    cur = conn.execute(
        """
        SELECT id, confidence, prediction_type, prediction_text, lens_name
          FROM oracle_predictions
         WHERE confidence IS NOT NULL
           AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')
        """
    )
    out = []
    for r in cur.fetchall():
        d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "confidence": r[1], "prediction_type": r[2],
            "prediction_text": r[3], "lens_name": r[4],
        }
        out.append(d)
    return out


def _band_of(confidence: float, width: float) -> float:
    """Which band a confidence falls in — via integer math, deliberately.

    The obvious `math.floor(conf / width) * width` is wrong at exactly the values
    that matter: 0.7 / 0.1 is 6.999999999999999 in binary floating point, so
    floor() returns 6 and a 0.7 lands in the 0.6 band. 41 live predictions sit at
    exactly 0.7 — the promotion gate — and every one of them would be filed one
    band low, leaving the gate band looking empty while the band below it
    inherited its rows. Scaling to integers first makes the boundary exact.
    """
    scale = 1000
    c = int(round(float(confidence) * scale))
    w = int(round(float(width) * scale))
    if w <= 0:
        return 0.0
    return round((c // w) * w / scale, 4)


def draw_sample(
    candidates: list[dict],
    already: set[str],
    bands: list[float],
    per_band: int,
    width: float,
    rng: "random.Random | None" = None,
) -> list[dict]:
    """Stratified random draw: up to `per_band` unsampled rows from each band.

    Pure function of its inputs so the selection logic is testable without a
    database. `rng` is injectable so tests can be deterministic while production
    stays genuinely random — a sampler with a fixed seed audits the same rows
    forever, which is not an audit.
    """
    r = rng or random.Random()
    by_band: dict[float, list[dict]] = {}
    for c in candidates:
        if str(c["id"]) in already:
            continue
        b = _band_of(c["confidence"], width)
        if b not in bands:
            continue
        by_band.setdefault(b, []).append(c)

    drawn: list[dict] = []
    for b in sorted(by_band):
        pool = by_band[b]
        picked = r.sample(pool, min(per_band, len(pool)))
        for p in picked:
            drawn.append({**p, "band": b})
    return drawn


class ConfidenceSamplerReflex:
    """Draw an unbiased audit sample of high-confidence decisions."""

    def run(self, context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
        reflex_id = context.get("reflex_id", f"cs-{uuid.uuid4().hex[:10]}")
        surface = context.get("surface") or "oracle_triage"
        bands = [float(b) for b in (context.get("bands") or _DEFAULT_BANDS)]
        per_band = int(context.get("per_band", _DEFAULT_PER_BAND))
        width = float(context.get("band_width", _DEFAULT_BAND_WIDTH))
        dry_run = bool(context.get("dry_run", False))

        result: Dict[str, Any] = {
            "reflex_id": reflex_id, "surface": surface, "dry_run": dry_run,
            "drawn": 0, "by_band": {}, "errors": [],
        }

        try:
            from tools.db.storage import get_connection
            from tools.genesis.harness.eval_harness import record_decision

            conn = get_connection()
            try:
                already = _already_sampled(conn, surface)
                candidates = _candidates(conn, surface)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("confidence sampler: could not build candidate set: %s", exc)
            return {"success": False, "metric_value": 0.0,
                    "details": {**result, "error": str(exc)}}

        drawn = draw_sample(candidates, already, bands, per_band, width)
        result["by_band"] = {str(b): sum(1 for d in drawn if d["band"] == b) for b in sorted(bands)}

        if dry_run:
            result["drawn"] = len(drawn)
            result["files"] = [d["id"] for d in drawn]
            return {"success": True, "metric_value": float(len(drawn)), "details": result}

        for d in drawn:
            try:
                # A pending row: confidence recorded now, outcome NULL until a
                # human judges it. _compute_ece drops unlabelled rows, so a
                # pending sample cannot move any metric until it is answered.
                record_decision(
                    task_id=str(d["id"]),
                    reflex=sample_reflex_name(surface),
                    decision=str(d.get("prediction_type") or "prediction"),
                    confidence=float(d["confidence"]),
                    metadata={
                        "sampled": True,
                        "band": d["band"],
                        "source_table": "oracle_predictions",
                        "lens": d.get("lens_name"),
                        "prediction_text": str(d.get("prediction_text") or "")[:500],
                        "reflex_id": reflex_id,
                    },
                )
                result["drawn"] += 1
            except Exception as exc:
                result["errors"].append(f"{d['id']}: {exc}")
                logger.warning("confidence sampler: could not record %s: %s", d["id"], exc)

        if result["drawn"]:
            logger.info(
                "confidence sampler: drew %s item(s) for audit across bands %s",
                result["drawn"], result["by_band"],
            )

        # Not `success: True` regardless. A sampler that recorded nothing it drew
        # has produced no evidence, and silence must not read as health — the
        # exact flaw in doc_modernization_sweep, which collects per-step errors
        # and then returns success anyway.
        #
        # An empty draw IS success: it means every candidate is already audited,
        # which is the goal, not a failure.
        success = not result["errors"]
        return {"success": success, "metric_value": float(result["drawn"]), "details": result}


def run(context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
    """Module-level entry point (Genesis daemon dispatch contract)."""
    return ConfidenceSamplerReflex().run(context, db_conn)


# ── CLI — draw, list what is pending, record a verdict ────────────────────────
#
# Without a way to answer, the samples pile up unlabelled and the loop never
# closes. This is the smallest surface that lets a human actually settle one.

def list_pending(surface: str = "oracle_triage", limit: int = 20) -> list[dict]:
    """Sampled rows still awaiting a human verdict."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT task_id, confidence, decision, metadata_json, created_at
              FROM harness_eval
             WHERE reflex = %s AND actual_outcome IS NULL
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (sample_reflex_name(surface), limit),
        )
        return [dict(r) if hasattr(r, "keys") else {
            "task_id": r[0], "confidence": r[1], "decision": r[2],
            "metadata_json": r[3], "created_at": r[4],
        } for r in cur.fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Confidence sampler — audit what the system was sure about")
    p.add_argument("--surface", default="oracle_triage")
    p.add_argument("--draw", action="store_true", help="Draw a new stratified sample")
    p.add_argument("--per-band", type=int, default=_DEFAULT_PER_BAND)
    p.add_argument("--dry-run", action="store_true", help="Show what would be drawn; write nothing")
    p.add_argument("--pending", action="store_true", help="List samples awaiting a verdict")
    p.add_argument("--verdict", metavar="TASK_ID", help="Record a verdict for a sampled item")
    p.add_argument("--outcome", choices=["correct", "incorrect"],
                   help="The verdict: was the prediction right?")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    if args.verdict:
        if not args.outcome:
            print("--verdict requires --outcome correct|incorrect")
            return 2
        from tools.genesis.harness.eval_harness import record_outcome
        record_outcome(args.verdict, args.outcome)
        print(f"recorded {args.verdict} -> {args.outcome}")
        return 0

    if args.pending:
        rows = list_pending(args.surface)
        if args.as_json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            print(f"{len(rows)} sample(s) awaiting a verdict:")
            for r in rows:
                meta = {}
                try:
                    meta = json.loads(r.get("metadata_json") or "{}")
                except Exception:
                    pass
                print(f"  {r['task_id']}  confidence={r['confidence']}  band={meta.get('band')}")
                txt = (meta.get("prediction_text") or "").strip()
                if txt:
                    print(f"      {txt[:100]}")
        return 0

    out = run({"surface": args.surface, "per_band": args.per_band, "dry_run": args.dry_run})
    if args.as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        d = out["details"]
        print(f"drew={d['drawn']} by_band={d['by_band']} success={out['success']}")
        for e in d.get("errors", []):
            print(f"  ! {e}")
    return 0 if out["success"] else 1


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    raise SystemExit(main())
