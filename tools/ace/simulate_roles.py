#!/usr/bin/env python3
# CUI // SP-CTI
"""ACE Role Simulation — live coworker activity simulation for SKI profiles.

Runs representative steps for the Product Manager and Software Craftsperson
roles using the LLMRouter, then simulates the A2A handoff between them.

Usage:
    python tools/ace/simulate_roles.py              # both roles, 2 steps each
    python tools/ace/simulate_roles.py --role pm    # Product Manager only
    python tools/ace/simulate_roles.py --role craft # Craftsperson only
    python tools/ace/simulate_roles.py --json       # machine-readable output
    python tools/ace/simulate_roles.py --dry-run    # show prompts, no LLM
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Simulation scenarios
# ---------------------------------------------------------------------------

_PM_STEPS = [
    {
        "step_id": "run_continuous_discovery",
        "label": "Continuous Discovery — SAM opportunity OST",
        "prompt": textwrap.dedent("""\
            You are a GovCon Product Manager using Teresa Torres' Opportunity Solution Tree.
            A new SAM.gov opportunity just arrived:
              Title: AI-Enabled Logistics Optimization for DLA
              Agency: Defense Logistics Agency (DLA)
              NAICS: 541512
              Set-Aside: Small Business
              Estimated Value: $4.5M / 3 years

            Run continuous discovery. Return JSON:
            {
              "desired_outcome": "...",
              "opportunity_statement": "...",
              "top_3_assumptions": [...],
              "stakeholder_personas": [{"role": "...", "goal": "..."}],
              "bid_signal": "go | no-go | conditional",
              "bid_rationale": "..."
            }
        """),
    },
    {
        "step_id": "run_pre_mortem",
        "label": "Pre-Mortem — top 5 failure modes",
        "prompt": textwrap.dedent("""\
            You are a GovCon Product Manager running a pre-mortem for:
              Opportunity: AI-Enabled Logistics Optimization for DLA
              Bid Decision: go
              Team: 8 engineers, 1 PM, 1 compliance lead

            Imagine it's 18 months from now and the project has failed.
            Return JSON:
            {
              "failure_narrative": "...",
              "risks": [
                {"id": "R-001", "description": "...", "probability": "H|M|L",
                 "impact": "H|M|L", "mitigation": "..."}
              ],
              "overall_risk_level": "H|M|L"
            }
            (provide exactly 5 risks)
        """),
    },
]

_CRAFT_STEPS = [
    {
        "step_id": "author_spec",
        "label": "Spec Authoring — AI logistics scoring module",
        "prompt": textwrap.dedent("""\
            You are a Software Craftsperson (addyosmani/agent-skills).
            The Product Manager has approved this PRD:
              Feature: AI-powered spare-parts demand scoring for DLA
              Desired outcome: Reduce emergency part orders by 30%
              Key constraint: Must run air-gapped (no cloud LLM at runtime)

            Author a SPEC.md. Return JSON:
            {
              "title": "...",
              "problem_statement": "...",
              "success_criteria": [...],
              "out_of_scope": [...],
              "interfaces": [{"name": "...", "description": "..."}],
              "testing_strategy": {"unit": "...", "integration": "...", "e2e": "..."},
              "implementation_boundary": "...",
              "adr_triggers": [...]
            }
        """),
    },
    {
        "step_id": "apply_doubt_driven_review",
        "label": "Doubt-Driven Review — challenge spec assumptions",
        "prompt": textwrap.dedent("""\
            You are a Software Craftsperson applying doubt-driven development.
            A colleague wrote a spec for an AI demand-scoring module.
            The spec claims: "Model accuracy will be ≥ 92% from day 1."

            Apply structured doubt. For each assumption, try to falsify it.
            Return JSON:
            {
              "suspicious_claims": [
                {
                  "claim": "...",
                  "falsification_attempt": "...",
                  "verdict": "plausible | questionable | false",
                  "recommendation": "..."
                }
              ],
              "spec_confidence": "low | medium | high",
              "required_experiments": [...]
            }
        """),
    },
]

_HANDOFF_STEP = {
    "step_id": "a2a_handoff",
    "label": "A2A Handoff — PM prd.authored → Craftsperson spec.approved",
    "pm_emit_topic": "prd.authored",
    "craft_listen_topic": "spec.approved",
    "pm_payload_prompt": textwrap.dedent("""\
        You are a GovCon Product Manager finalizing a PRD handoff to Engineering.
        Opportunity: AI-Enabled Logistics Optimization for DLA
        Your pre-mortem is complete. Risk R-001 (data quality) is the top risk.

        Write the handoff message to the Software Craftsperson. Return JSON:
        {
          "topic": "prd.authored",
          "from_role": "product_manager",
          "to_role": "software_craftsperson",
          "prd_summary": "...",
          "top_risk": "...",
          "acceptance_criteria": [...],
          "requested_step": "author_spec"
        }
    """),
    "craft_response_prompt": textwrap.dedent("""\
        You are a Software Craftsperson receiving a PRD handoff.
        PRD summary: AI-powered spare-parts demand scoring for DLA.
        Top risk: data quality — historical demand data has 15% null values.
        Requested: author a spec.

        Acknowledge the handoff and flag any blockers. Return JSON:
        {
          "topic": "spec.approved",
          "from_role": "software_craftsperson",
          "to_role": "product_manager",
          "acknowledgement": "...",
          "blockers": [...],
          "spec_ready_by": "YYYY-MM-DD",
          "first_step": "author_spec"
        }
    """),
}

# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------


def _invoke_llm(function: str, prompt: str, dry_run: bool = False) -> dict:
    if dry_run:
        return {"status": "dry_run", "content": f"[DRY RUN] Would invoke {function}:\n{prompt[:200]}..."}

    try:
        from tools.llm.router import LLMRouter, LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            effort="high",
        )
        resp = router.invoke(function, req)
        content = resp.content if hasattr(resp, "content") else str(resp)

        # Try to extract JSON from the response
        import re
        json_match = re.search(r"\{[\s\S]+\}", content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                return {"status": "ok", "content": content, "parsed": parsed, "model": getattr(resp, "model", "unknown")}
            except json.JSONDecodeError:
                pass
        return {"status": "ok", "content": content, "parsed": None, "model": getattr(resp, "model", "unknown")}

    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300], "content": None}


# ---------------------------------------------------------------------------
# Role simulation
# ---------------------------------------------------------------------------


def _simulate_role(role_id: str, steps: list[dict], llm_function: str,
                   dry_run: bool, verbose: bool) -> dict:
    results = []
    for step in steps:
        if verbose:
            print(f"\n  >> [{role_id}] {step['label']}", flush=True)

        result = _invoke_llm(llm_function, step["prompt"], dry_run=dry_run)
        step_result = {
            "step_id": step["step_id"],
            "label": step["label"],
            "status": result["status"],
            "model": result.get("model", "n/a"),
        }
        if result.get("parsed"):
            step_result["output"] = result["parsed"]
        elif result.get("content"):
            step_result["output"] = (result["content"] or "")[:500]
        if result.get("error"):
            step_result["error"] = result["error"]
            if verbose:
                print(f"       error: {result['error'][:200]}", flush=True)

        results.append(step_result)
        if verbose:
            status_icon = "OK" if result["status"] in ("ok", "dry_run") else "FAIL"
            print(f"     [{status_icon}] status={result['status']} model={result.get('model','n/a')}", flush=True)

    return {"role_id": role_id, "llm_function": llm_function, "steps": results}


def _simulate_handoff(dry_run: bool, verbose: bool) -> dict:
    if verbose:
        print(f"\n  >> [A2A handoff] PM -> prd.authored -> Craftsperson spec.approved", flush=True)

    pm_result = _invoke_llm("agent_product_manager", _HANDOFF_STEP["pm_payload_prompt"], dry_run=dry_run)
    craft_result = _invoke_llm("agent_software_craftsperson", _HANDOFF_STEP["craft_response_prompt"], dry_run=dry_run)

    handoff = {
        "topic_flow": f"{_HANDOFF_STEP['pm_emit_topic']} → {_HANDOFF_STEP['craft_listen_topic']}",
        "pm_emit": {
            "status": pm_result["status"],
            "payload": pm_result.get("parsed") or (pm_result.get("content") or "")[:300],
            "error": pm_result.get("error"),
        },
        "craft_response": {
            "status": craft_result["status"],
            "payload": craft_result.get("parsed") or (craft_result.get("content") or "")[:300],
            "error": craft_result.get("error"),
        },
        "handoff_complete": (
            pm_result["status"] in ("ok", "dry_run") and
            craft_result["status"] in ("ok", "dry_run")
        ),
    }
    if verbose:
        icon = "OK" if handoff["handoff_complete"] else "FAIL"
        print(f"     [{icon}] handoff_complete={handoff['handoff_complete']}", flush=True)
    return handoff


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ACE role live simulation")
    parser.add_argument("--role", choices=["pm", "craft", "both"], default="both")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--dry-run", action="store_true", help="Show prompts, skip LLM calls")
    args = parser.parse_args()
    verbose = not args.json

    report: dict = {
        "simulation": "ski-roles-live",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "roles": [],
        "handoff": None,
    }

    if verbose:
        print(f"\n{'='*60}")
        print("ACE Role Simulation — SKI Initiative Profiles")
        print(f"{'='*60}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE LLM'}")

    # PM simulation
    if args.role in ("pm", "both"):
        if verbose:
            print(f"\n[Product Manager] 2 representative steps")
        pm_report = _simulate_role(
            role_id="product_manager",
            steps=_PM_STEPS,
            llm_function="agent_product_manager",
            dry_run=args.dry_run,
            verbose=verbose,
        )
        report["roles"].append(pm_report)

    # Craftsperson simulation
    if args.role in ("craft", "both"):
        if verbose:
            print(f"\n[Software Craftsperson] 2 representative steps")
        craft_report = _simulate_role(
            role_id="software_craftsperson",
            steps=_CRAFT_STEPS,
            llm_function="agent_software_craftsperson",
            dry_run=args.dry_run,
            verbose=verbose,
        )
        report["roles"].append(craft_report)

    # A2A handoff simulation
    if args.role == "both":
        if verbose:
            print(f"\n[A2A Handoff] PM prd.authored -> Craftsperson spec.approved")
        report["handoff"] = _simulate_handoff(dry_run=args.dry_run, verbose=verbose)

    # Summary
    total_steps = sum(len(r["steps"]) for r in report["roles"])
    ok_steps = sum(
        1 for r in report["roles"]
        for s in r["steps"]
        if s["status"] in ("ok", "dry_run")
    )
    report["summary"] = {
        "total_steps": total_steps,
        "ok_steps": ok_steps,
        "failed_steps": total_steps - ok_steps,
        "handoff_complete": report["handoff"]["handoff_complete"] if report["handoff"] else None,
        "pass": ok_steps == total_steps,
    }

    if verbose:
        print(f"\n{'='*60}")
        print(f"SUMMARY: {ok_steps}/{total_steps} steps OK", end="")
        if report["handoff"]:
            icon = "OK" if report["handoff"]["handoff_complete"] else "FAIL"
            print(f"  |  A2A handoff: {icon}", end="")
        print(f"\n{'='*60}\n")

    if args.json:
        payload = json.dumps(report, indent=2, ensure_ascii=True)
        sys.stdout.buffer.write(payload.encode("ascii") + b"\n")

    return 0 if report["summary"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
