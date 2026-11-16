# CUI // SP-CTI
"""Seed what the feature-conformance write e2e needs on screen (icx-cnf-08).

The e2e gate (``tools/conformance/feature_audit.py --e2e-gate``) holds that every
write the audit credits to a page by its endpoint id was SENT BY A PAGE in the run.
Some of those controls appear only when there is something to act on. This puts it
there, through the SAME stores the product writes with, never a hand-written INSERT
of derived state:

* a Cortex resolution conflict (``finding_store.record_findings``) so the Explorer
  shows an open finding with Accept/Reject;
* an agent-loop session that hit its turn limit (``agent_loop_session.save_session``)
  so its page offers Run Eval, and its eval suggestions offer Re-run;
* NOVA trust for the e2e-only role ``cnf08_e2e_auto_role``
  (``trust_calibrator.record_trust_event``) so "Turn auto on" is offered for it;
* ``--hitl <instance_id>``: a pending HITL checkpoint on an instance the test
  launched from the page, written exactly as ``CoworkerThread._audit`` writes one.

Everything is named ``cnf08-e2e-*`` so it cannot be mistaken for real data. The
server must be on a throwaway database (globalSetup asserts it). Prints JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SESSION_ID = "cnf08-e2e-session"
ROLE_ID = "cnf08_e2e_auto_role"
FINDING_ENTITY = "cnf08-e2e tls 1.1"


def seed_finding() -> dict:
    from tools.cortex import finding_store

    # One open finding per run: a finding is adjudicated once, so a rerun needs its own.
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    conflict = {
        "entity_key": f"{FINDING_ENTITY} {run}", "entity_label": f"CNF08 E2E TLS 1.1 ({run})",
        "kind": "status", "values": ["current", "deprecated"],
        "sides": [
            {"backend": "currency", "backends": ["currency"], "source": "e2e-catalog",
             "source_id": "cnf08-e2e-1", "status": "deprecated", "authoritative": True},
            {"backend": "rag", "backends": ["rag"], "source": "e2e-runbook",
             "source_id": "cnf08-e2e-2", "status": "current", "authoritative": False},
        ],
        "backends": ["currency", "rag"], "cross_backend": True,
        "citations": [], "uncited_sides": [],
    }
    result = SimpleNamespace(entity=FINDING_ENTITY, verdict="deprecated",
                             conflicts=[conflict], gaps=[], citations=[])
    ctx = SimpleNamespace(tenant_id="default", classification="CUI")
    return finding_store.record_findings(result, ctx, {"resolve": {"persist_findings": True}})


def seed_session() -> dict:
    """A session per run: its page offers Run Eval only while it has no eval."""
    from icdev.tools.llm.agent_loop_session import save_session

    session_id = f"{SESSION_ID}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    result = SimpleNamespace(
        session_id=session_id, result_subtype="error_max_turns", turns=12,
        total_input_tokens=1200, total_output_tokens=300, total_cost_usd=0.0,
        messages=[{"role": "user", "content": "cnf08 e2e: summarise the run"},
                  {"role": "assistant", "content": "cnf08 e2e: ran out of turns"}])
    saved = save_session(result, instance_id="", coworker_id="cnf08-e2e-coworker",
                         llm_function="task_decomposition",
                         system_prompt="cnf08 e2e session (test data)")
    return {"session_id": session_id, "saved": saved}


def seed_trust() -> dict:
    from icdev.tools.ace.trust_calibrator import (TRUST_TRUSTED, get_trust_score,
                                                  record_trust_event)

    out = {}
    for _ in range(12):
        if get_trust_score(ROLE_ID) >= TRUST_TRUSTED + 0.02:
            break
        out = record_trust_event(ROLE_ID, "success", source_task_id="cnf08-e2e")
    return {"role_id": ROLE_ID, "score": get_trust_score(ROLE_ID), **out}


def seed_hitl(instance_id: str) -> dict:
    """One pending checkpoint, the row ``CoworkerThread._audit('hitl_pending')`` writes."""
    from icdev.tools.db.storage import get_canvas_connection

    detail = "cnf08 e2e: approve this step before it runs"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_canvas_connection("ICDEV_ACE_DB_URL")  # tools/ace/blueprint.py _DB_ENV
    try:
        conn.execute(
            "INSERT INTO ace_audit_log (instance_id, coworker_id, action, detail, actor, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (instance_id, "cnf08-e2e-coworker", "hitl_pending", detail, "coworker_thread", now))
        conn.commit()
    finally:
        conn.close()
    return {"instance_id": instance_id, "detail": detail}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--hitl", metavar="INSTANCE_ID", help="add a pending checkpoint to it")
    a = ap.parse_args(argv)
    if a.hitl:
        out = {"hitl": seed_hitl(a.hitl)}
    else:
        out = {"finding": seed_finding(), "session": seed_session(), "trust": seed_trust()}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
