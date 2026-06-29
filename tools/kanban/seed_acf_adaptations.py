# CUI // SP-CTI
"""Seed Kanban tasks for ACF adaptations — strengthen the Foundry with Innovation,
Creative, Research, Genesis Harness, Oracle Triage, and Strategos capabilities.

Run:
    python tools/kanban/seed_acf_adaptations.py
    python tools/kanban/seed_acf_adaptations.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PROJECT_ID = "acf"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    {
        "id": "acf-ada-01",
        "title": "Integrate Genesis Harness eval into ACF decision-outcome tracking",
        "description": (
            "Build tools/foundry/harness_bridge.py to wire Genesis Harness eval_harness.py "
            "(record_decision, record_outcome, compute_metrics) into the ACF engine cycle. "
            "After engine.run_cycle() emits tasks, the bridge records the concept approval decision "
            "(deliberator verdict + score gate) as a harness decision row, and when learner.py later "
            "marks the concept shipped/failed, the bridge records the outcome. This closes the loop "
            "between ACF concept generation and Genesis Harness precision/recall metrics. "
            "Acceptance: (1) harness_bridge.py exists and imports cleanly, (2) a mocked run_cycle that "
            "emits a concept writes a harness_eval decision row with decision_type='acf_approve', "
            "(3) a mocked learner outcome writes a harness_eval outcome row, (4) compute_metrics() "
            "for decision_type='acf_approve' returns precision/recall/ECE. "
            "Test plan: pytest tests/test_foundry_harness_bridge.py with monkeypatched harness_eval "
            "and learner fixtures."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "acf-learn-01",
    },
    {
        "id": "acf-ada-02",
        "title": "Meta-harness adaptive thresholds for ACF scorer gates",
        "description": (
            "Build tools/foundry/meta_scorer.py to bring Genesis meta_harness adaptive threshold logic "
            "into ACF's composite score gate. meta_scorer.py consumes foundry_outcomes from learner.py, "
            "computes a sliding-window false-approve rate, and when it exceeds a configurable threshold, "
            "dynamically raises config.scoring.min_composite (persisted back to args/foundry_config.yaml). "
            "It also proposes heuristic retirements when a sub-score weight consistently predicts failure "
            "(like meta_harness._propose_heuristic_retirements). This makes the ACF scorer self-tuning "
            "instead of static. Acceptance: (1) meta_scorer.py exists with adjust_threshold() and "
            "propose_removals(), (2) a fixture with 3 consecutive vv_fail outcomes raises min_composite "
            "by >=0.05, (3) a fixture with 3 consecutive shipped outcomes lowers min_composite by <=0.05 "
            "(bounded), (4) proposed removals are written to args/meta_harness_proposals.yaml. "
            "Test plan: pytest tests/test_foundry_meta_scorer.py with seeded foundry_outcomes fixtures."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-ada-01",
    },
    {
        "id": "acf-ada-03",
        "title": "Oracle-style deterministic concept validation verifiers for ACF",
        "description": (
            "Build tools/foundry/oracle_verifiers.py adapting oracle_triage.py's lens-specific deterministic "
            "verifiers to ACF concepts. Implement three verifiers: (1) concept_not_in_manifest — checks if "
            "the concept's proposed_capability matches an existing tool/manifest entry, (2) route_not_listed — "
            "checks if the concept's canvas_contract implies a dashboard route not registered in "
            "app.py/_CANVAS_DEFS, (3) orphan_db_table — checks if the contract's tables[] lack a corresponding "
            "CREATE TABLE in init_icdev_db.py. Each verifier returns {lens, passed, confidence, detail}. "
            "These augment (not replace) novelty_gate.py. Acceptance: (1) oracle_verifiers.py exists with all "
            "three functions, (2) a concept matching an existing manifest entry fails concept_not_in_manifest, "
            "(3) a concept with a new route missing from app.py fails route_not_listed, (4) a concept with a "
            "table missing from init_icdev_db.py fails orphan_db_table, (5) all verifiers run in <100ms. "
            "Test plan: pytest tests/test_foundry_oracle_verifiers.py with mock manifest/app/init fixtures."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "acf-synth-01",
    },
    {
        "id": "acf-ada-04",
        "title": "Cross-registration bridge between Creative/Innovation engines and ACF",
        "description": (
            "Build tools/foundry/cross_register.py to automatically push ACF-approved concepts into the Creative "
            "and Innovation engine stores, and pull their downstream outputs back into ACF signals. When "
            "deliberator.approve() emits a concept, cross_register writes a creative_gap record "
            "(tools/creative/gap_scorer) and an innovation_signal record (tools/innovation/signal_ranker) so "
            "the existing engines can rank, score, and incubate the idea independently. Conversely, when "
            "creative_engine or signal_ranker marks a concept as highly scored, cross_register normalizes it "
            "back into foundry_signals for the next ACF cycle. This replaces manual copy-paste between engine "
            "silos. Acceptance: (1) cross_register.py exists with push_to_creative() and pull_from_innovation(), "
            "(2) an approved concept creates rows in both creative and innovation stores, (3) a high-ranked "
            "creative gap becomes a foundry_signals row on the next harvest, (4) dedup prevents duplicate "
            "cross-registered signals. Test plan: pytest tests/test_foundry_cross_register.py with SQLite "
            "fixtures and monkeypatched creative/innovation modules."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-harvest-02",
    },
    {
        "id": "acf-ada-05",
        "title": "Quiet-hours scheduling for ACF Genesis reflex",
        "description": (
            "Extend tools/genesis/reflexes/foundry_cycle.py with quiet-hours logic adapted from "
            "tools/creative/creative_engine.py. Add QUIET_HOURS_START/END to args/foundry_config.yaml "
            "(default 22:00-06:00 local). During quiet hours, the reflex logs 'skipped_quiet_hours' and "
            "exits without invoking engine.run_cycle(). Outside quiet hours, it proceeds normally. This prevents "
            "the autonomous foundry from waking users or burning API quota at night. Acceptance: (1) "
            "foundry_cycle.py reads config quiet_hours and respects them, (2) a reflex run at 23:00 logs "
            "skipped status, (3) a reflex run at 10:00 proceeds to engine.run_cycle(), (4) missing config "
            "defaults to no quiet hours (backwards compatible). Test plan: pytest "
            "tests/test_genesis_reflexes_foundry_cycle.py with freezegun or mocked datetime."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-reflex-01",
    },
    {
        "id": "acf-ada-06",
        "title": "Strategos research bridge fan-out for ACF intelligence products",
        "description": (
            "Build tools/foundry/strategos_bridge.py adapting tools/strategos/research_bridge.py to route "
            "ACF-relevant concepts to the four Strategos output channels. When a concept is approved with a "
            "high compliance_risk or market_score, the bridge fans out to: (1) ghost_signals "
            "(tools/strategos/ghost_signals) for dark-web monitoring triggers, (2) hitl_items "
            "(tools/strategos/hitl_items) for human review queue, (3) pir_requirements "
            "(tools/strategos/pir_requirements) for priority intelligence requirements, (4) intelligence_briefs "
            "(tools/strategos/intelligence_briefs) for auto-generated briefs. This ensures ACF concepts that "
            "touch national-security or competitive-intelligence domains get the appropriate ICDEV intelligence "
            "workflow. Acceptance: (1) strategos_bridge.py exists with fan_out(concept) -> dict of channel "
            "results, (2) a high-compliance concept queues a hitl_item and a pir_requirement, (3) a low-risk "
            "concept skips all channels, (4) channel failures are caught and logged without crashing the cycle. "
            "Test plan: pytest tests/test_foundry_strategos_bridge.py with mocked strategos channels."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-harvest-02",
    },
    {
        "id": "acf-ada-07",
        "title": "Heuristic writer error-pattern learning for ACF scorer weights",
        "description": (
            "Build tools/foundry/heuristic_learner.py adapting tools/genesis/harness/heuristic_writer.py to "
            "extract error patterns from failed ACF concepts and propose scorer weight amendments. After "
            "learner.py records a vv_fail outcome, heuristic_learner extracts the concept's signals and scores, "
            "runs a diff against shipped concepts, and writes proposed heuristic amendments to "
            "args/oracle_heuristics_proposed.yaml (e.g., 'if compliance_risk > 0.7 and effort_estimate < 3, "
            "raise effort penalty'). These are gated by ICDEV_HARNESS_COLEARN and require human merge, but the "
            "extraction is fully automated. Acceptance: (1) heuristic_learner.py exists with extract_error_cases() "
            "and propose_amendments(), (2) a vv_fail fixture yields >=1 proposed heuristic, (3) a shipped fixture "
            "yields zero proposals, (4) proposals include a confidence score and trace_id. Test plan: pytest "
            "tests/test_foundry_heuristic_learner.py with seeded foundry_outcomes/concept fixtures."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "acf-ada-01",
    },
    {
        "id": "acf-ada-08",
        "title": "Vertical source scanner registry for ACF harvester",
        "description": (
            "Extend tools/foundry/harvester.py with a SOURCE_SCANNERS function registry pattern adapted from "
            "tools/research/source_scanner.py. Replace the hardcoded per-source calls (innovation, creative, "
            "research, genesis, telemetry) with a registry dict where each source registers a scanner function "
            "under a canonical name. Add a new scanner 'arxiv_acf' as a demonstration: it queries the arXiv API "
            "for papers matching ACF concept keywords (deterministic, no LLM), normalizes into foundry_signals, "
            "and respects per-source caps from args/foundry_config.yaml. This makes adding new signal sources a "
            "one-line registration instead of editing harvester.py. Acceptance: (1) harvester.py uses a registry "
            "dict with register_source(name, fn) and scan(name) APIs, (2) existing sources (innovation, creative, "
            "research, genesis, telemetry) are registered at import, (3) arxiv_acf scanner exists in "
            "tools/foundry/scanners/arxiv.py and produces normalized signals, (4) a new scanner can be added "
            "without modifying harvester.py core. Test plan: pytest tests/test_foundry_harvester_registry.py and "
            "tests/test_foundry_scanners_arxiv.py."
        ),
        "task_type": "build",
        "priority": "low",
        "depends_on_task_id": "acf-harvest-01",
    },
    {
        "id": "acf-ada-09",
        "title": "ACF adaptation integration health gate",
        "description": (
            "Run the full adaptation integration gate after all eight adaptation build tasks are done. "
            "(1) Run 'python tools/workflow/coherence_checker.py --all --fix --gate' and verify no new ACF-related "
            "coherence failures (schema parity, manifest, append-only, sandbox coverage, route uniqueness, "
            "karpathy sync). (2) Run 'ruff check tools/foundry/' clean. (3) Run 'pytest tests/test_foundry_* -v' "
            "green for all adaptation modules. (4) Run 'python tools/testing/health_check.py --json' green. "
            "(5) Run 'python tools/dx/companion.py --sync --write --json' IN THE FOREGROUND to propagate new "
            "modules. Record results in the completion notes. This is the final acceptance gate before ACF "
            "adaptations are considered shipped. Test plan: manual + automated gate run; screenshot of green "
            "terminal to playwright/screenshots/acf-adaptations-green.png."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "acf-ada-08",
        "depends_on": ["acf-ada-01", "acf-ada-02", "acf-ada-03", "acf-ada-04",
                       "acf-ada-05", "acf-ada-06", "acf-ada-07", "acf-ada-08"],
    },
]


def seed(dry_run: bool = False) -> int:
    now = _now()
    for t in TASKS:
        t["status"] = "scheduled"
        t["scheduled_at"] = now
        t["project_id"] = PROJECT_ID
        t["classification"] = "CUI"

    report = create_tasks(
        PROJECT_ID, TASKS, dry_run=dry_run, strict=False, register_project=False
    )
    print(report.summary())
    return len(report.seeded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed ACF adaptation kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
