# CUI // SP-CTI
"""Heuristic Writer — Phase 4 of the continuous harness.

When the harness detects degraded oracle_triage precision (< 0.80) or high ECE
(> 0.15), this module:

  1. Extracts the top error cases from harness_eval (high-confidence wrong calls).
  2. Calls the LLM to propose new heuristics that would have caught those errors.
  3. Writes proposals to args/oracle_heuristics_proposed.yaml for human review.
  4. Creates a kanban card: "Review oracle heuristic proposals".

Gate: ICDEV_HARNESS_COLEARN=true (default off).

Once a human approves (moves the review card to done), the proposals are
merged into args/oracle_heuristics.yaml and picked up on the next
oracle_triage cycle.
"""
from __future__ import annotations


from tools.logging.icdev_logger import get_logger
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

LOG = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
HEURISTICS_FILE = BASE_DIR / "args" / "oracle_heuristics.yaml"
PROPOSED_FILE = BASE_DIR / "args" / "oracle_heuristics_proposed.yaml"

_SYSTEM_PROMPT = """\
You are improving the oracle triage heuristics for ICDEV™, a DevSecOps platform.

The oracle_triage reflex classifies kanban tasks as promote/dismiss/backlog/skip.
You are given cases where it made high-confidence WRONG decisions, along with the
actual outcome.

Your job: propose NEW heuristics that would correctly classify these cases.

Each heuristic must be a YAML mapping with these fields:
  name:                 unique kebab-case slug
  action:               promote | dismiss | backlog
  reason:               one sentence explaining the decision
  match_title_prefix:   (use ONLY if tasks share a clear title prefix)
  match_title_contains: (use if a keyword reliably indicates the action)
  match_id_prefix:      (use ONLY if the task ID prefix is meaningful)

Rules:
- Propose ONLY heuristics supported by multiple error cases, not one-off outliers
- Be conservative: when in doubt, use 'skip' by NOT adding a heuristic at all
- Do NOT duplicate existing baseline heuristics
- Return ONLY a YAML list under a "proposed:" key, nothing else

Example output:
proposed:
  - name: missing-db-index
    match_title_contains: "missing index"
    action: promote
    reason: "DB index gap confirmed by code reference — needs migration"
  - name: docs-only-route
    match_title_contains: "docs only"
    action: dismiss
    reason: "Route appears in docs/plans only, not in Flask code"
"""


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def extract_error_cases(reflex: str = "oracle_triage", limit: int = 10) -> list[dict[str, Any]]:
    """Return high-confidence wrong decisions from harness_eval.

    "Wrong" means:
      - decision=promote/backlog but actual_outcome=false_positive
      - decision=dismiss but actual_outcome=resolved (dismissed something real)
    Both indicate the triage made a confident but incorrect call.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        conn = _conn()
        rows = conn.execute(
            """
            SELECT he.task_id, he.decision, he.confidence, he.actual_outcome,
                   he.metadata_json, kt.title, kt.description
              FROM harness_eval he
              LEFT JOIN kanban_tasks kt ON he.task_id = kt.id
             WHERE he.reflex = %s
               AND he.confidence >= 0.65
               AND he.created_at >= %s
               AND (
                   (he.decision IN ('promote', 'backlog') AND he.actual_outcome = 'false_positive')
                OR (he.decision = 'dismiss' AND he.actual_outcome = 'resolved')
               )
             ORDER BY he.confidence DESC
             LIMIT %s
            """,
            (reflex, cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        LOG.warning("[heuristic_writer] extract_error_cases failed: %s", exc)
        return []


def propose_heuristic_amendments(
    error_cases: list[dict[str, Any]],
    current_heuristics_yaml: str,
) -> str | None:
    """Call the LLM with error cases and current heuristics; return proposed YAML string."""
    if not error_cases:
        return None

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        cases_text = "\n".join(
            f"- task_id={c['task_id']!r} title={c.get('title','?')!r} "
            f"decision={c['decision']!r} confidence={c['confidence']:.2f} "
            f"actual_outcome={c['actual_outcome']!r}"
            for c in error_cases
        )

        user_content = (
            f"Current heuristics:\n```yaml\n{current_heuristics_yaml}\n```\n\n"
            f"Error cases ({len(error_cases)} high-confidence wrong decisions):\n{cases_text}\n\n"
            "Propose new heuristics to prevent these errors:"
        )

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.0,
            skip_injection_scan=True,
        )
        response = router.invoke("oracle_triage_llm", request)
        if response and response.content:
            return response.content.strip()
    except Exception as exc:
        LOG.warning("[heuristic_writer] LLM call failed: %s", exc)
    return None


def write_proposed_heuristics(proposed_yaml: str, error_case_count: int) -> bool:
    """Write the proposed YAML to oracle_heuristics_proposed.yaml."""
    try:
        parsed = yaml.safe_load(proposed_yaml)
        if not isinstance(parsed, dict) or "proposed" not in parsed:
            LOG.warning("[heuristic_writer] LLM output missing 'proposed:' key — skipping")
            return False

        proposals = parsed["proposed"]
        if not isinstance(proposals, list) or not proposals:
            return False

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error_cases_analyzed": error_case_count,
            "proposed": proposals,
        }
        PROPOSED_FILE.write_text(yaml.dump(output, default_flow_style=False, allow_unicode=True), newline="")
        LOG.info("[heuristic_writer] wrote %d proposals to %s", len(proposals), PROPOSED_FILE)
        return True
    except Exception as exc:
        LOG.warning("[heuristic_writer] write_proposed_heuristics failed: %s", exc)
        return False


def merge_approved_proposals() -> int:
    """Merge oracle_heuristics_proposed.yaml into oracle_heuristics.yaml.

    Called when the review kanban card is closed (done). Returns count merged.
    """
    if not PROPOSED_FILE.exists():
        return 0
    try:
        proposed = yaml.safe_load(PROPOSED_FILE.read_text())
        current = yaml.safe_load(HEURISTICS_FILE.read_text()) if HEURISTICS_FILE.exists() else {}

        new_entries = proposed.get("proposed", [])
        if not new_entries:
            return 0

        existing = current.get("heuristics", [])
        existing_names = {h["name"] for h in existing}
        added = [h for h in new_entries if h.get("name") not in existing_names]

        if not added:
            return 0

        current.setdefault("heuristics", []).extend(added)
        current["version"] = current.get("version", 1) + 1
        HEURISTICS_FILE.write_text(yaml.dump(current, default_flow_style=False, allow_unicode=True), newline="")
        PROPOSED_FILE.unlink(missing_ok=True)
        LOG.info("[heuristic_writer] merged %d new heuristic(s) into %s", len(added), HEURISTICS_FILE)
        return len(added)
    except Exception as exc:
        LOG.warning("[heuristic_writer] merge failed: %s", exc)
        return 0


def run_colearn_pass(reflex: str = "oracle_triage", dry_run: bool = False) -> dict[str, Any]:
    """Full co-learning pass: extract errors → LLM proposes → write proposals.

    Called by the harness reflex when ICDEV_HARNESS_COLEARN=true and a
    precision or ECE gate has fired.
    """
    if os.getenv("ICDEV_HARNESS_COLEARN", "").lower() not in ("true", "1"):
        return {"skipped": True, "reason": "ICDEV_HARNESS_COLEARN not enabled"}

    error_cases = extract_error_cases(reflex)
    if not error_cases:
        return {"skipped": True, "reason": "no error cases found (need outcomes recorded)"}

    current_yaml = HEURISTICS_FILE.read_text() if HEURISTICS_FILE.exists() else ""
    proposed_yaml = propose_heuristic_amendments(error_cases, current_yaml)

    if not proposed_yaml:
        return {"skipped": True, "reason": "LLM returned no proposals"}

    if dry_run:
        return {"dry_run": True, "error_cases": len(error_cases), "proposed_yaml": proposed_yaml}

    written = write_proposed_heuristics(proposed_yaml, len(error_cases))
    return {
        "error_cases_analyzed": len(error_cases),
        "proposals_written": written,
        "proposed_file": str(PROPOSED_FILE) if written else None,
    }
