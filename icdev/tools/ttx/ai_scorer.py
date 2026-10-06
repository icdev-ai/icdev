# CUI // SP-CTI
"""TTX Engine — AI scoring: receipt validation + LLM judge + time bonus."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from .constants import TIME_BONUS_BRACKETS

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Step 1: Receipt validation
# ---------------------------------------------------------------------------

def validate_receipts(
    team_id: int,
    session_id: int,
    receipts: list[dict],
    bonus_per_call: int,
    max_bonus: int,
) -> tuple[int, int]:
    """Validate AI tool receipts against ttx_api_log.

    Returns (receipt_pts, valid_count).
    """
    conn = get_connection()
    valid_count = 0
    for r in receipts:
        call_id = r.get("call_id", "")
        if not call_id:
            continue
        row = conn.execute(
            """SELECT 1 FROM ttx_api_log
               WHERE team_id = %s AND session_id = %s AND call_id = %s""",
            (team_id, session_id, call_id),
        ).fetchone()
        if row:
            valid_count += 1

    receipt_pts = min(valid_count * bonus_per_call, max_bonus)
    return receipt_pts, valid_count


# ---------------------------------------------------------------------------
# Step 2: LLM judge
# ---------------------------------------------------------------------------

def judge_response(
    inject_body: str,
    team_response: str,
    rubric: dict,
    receipt_evidence: str = "",
) -> dict[str, Any]:
    """Score a team response using the LLM judge + rubric.

    Returns {dimension_scores, total, rationale, confidence, unscored?}.

    FAIL LOUD: if the LLM is unavailable, its output is unparseable, or there is
    no usable rubric, the response is marked UNSCORED (total 0, ``unscored``
    True, and a reason) — never a fabricated midpoint. A silent 50 is
    indistinguishable from a real score and corrupts the leaderboard/AAR.
    """
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        if not router.has_any_llm():
            raise RuntimeError("no_llm")
    except Exception as exc:
        log.warning("LLM judge unavailable (%s) — response left unscored", exc)
        return _unscored("LLM judge unavailable — response left unscored")

    dims = rubric.get("dimensions", []) if isinstance(rubric, dict) else []
    # FAIL LOUD on a malformed rubric shape rather than crashing. The canonical
    # dimensions-LIST format is produced once at load time by
    # scenario_loader.normalize_rubric_dimensions; a legacy dict-of-dicts rubric
    # (or any other malformed shape) reaching here means the loader was bypassed,
    # so we mark the response unscored instead of raising AttributeError on
    # ``str.get`` (which previously 500'd POST /api/gameday/response for the
    # forge_ascent / hunt_the_fleet / meridian packs).
    if not isinstance(dims, list) or not dims:
        return _unscored("No usable rubric dimensions for inject — response left unscored")
    if not all(isinstance(d, dict) and "id" in d and "weight" in d for d in dims):
        return _unscored("Rubric dimensions malformed (not id/weight dicts) — response left unscored")
    if sum(d.get("weight", 0) for d in dims) == 0:
        return _unscored("Rubric dimensions have zero total weight — response left unscored")

    dim_block = "\n".join(
        f"- {d['id']} (weight {d['weight']}): {d.get('prompt', '')}" for d in dims
    )
    system_prompt = (
        "You are an objective tabletop exercise judge. Score the team response "
        "strictly on the rubric dimensions. Return JSON only — no prose.\n"
        "Format: {\"scores\": {\"<dim_id>\": <0-10>, ...}, \"rationale\": \"...\", \"confidence\": <0.0-1.0>}"
    )
    user_msg = (
        f"INJECT:\n{inject_body}\n\n"
        f"TEAM RESPONSE:\n{team_response}\n\n"
        f"AI TOOL EVIDENCE:\n{receipt_evidence or 'None provided'}\n\n"
        f"RUBRIC DIMENSIONS:\n{dim_block}\n\n"
        "Score each dimension 0-10. Return JSON only."
    )

    try:
        from tools.llm.router import LLMRequest
        req = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system_prompt,
            max_tokens=512,
        )
        resp = router.invoke("ttx_judge", req)
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
    except Exception as exc:
        log.warning("LLM judge output unparseable (%s) — response left unscored", exc)
        return _unscored(f"LLM judge output unparseable — response left unscored ({exc})")

    scores = parsed.get("scores", {})
    total = _weighted_total(scores, dims)
    return {
        "dimension_scores": scores,
        "total": total,
        "rationale": parsed.get("rationale", ""),
        "confidence": parsed.get("confidence", 0.7),
    }


def _weighted_total(scores: dict, dims: list[dict]) -> int:
    total_weight = sum(d["weight"] for d in dims)
    if total_weight == 0:
        # Guarded against in judge_response (returns unscored); 0 here is a
        # defensive floor, never a fabricated midpoint.
        return 0
    weighted = sum(
        scores.get(d["id"], 5) * d["weight"] for d in dims
    )
    return round((weighted / total_weight) * 10)  # scale 0-10 → 0-100


def _unscored(reason: str) -> dict[str, Any]:
    """Explicitly-unscored judge result. NEVER a fabricated midpoint — an
    LLM/parse/rubric failure must be distinguishable from a genuine score so the
    leaderboard and AAR can render it distinctly instead of counting it as 50."""
    return {
        "dimension_scores": {},
        "total": 0,
        "rationale": reason,
        "confidence": 0.0,
        "unscored": True,
        "unscored_reason": reason,
    }


# ---------------------------------------------------------------------------
# Step 3: Time bonus
# ---------------------------------------------------------------------------

def compute_time_bonus(time_taken_s: float | None) -> int:
    if time_taken_s is None:
        return 0
    for threshold, bonus in TIME_BONUS_BRACKETS:
        if time_taken_s <= threshold:
            return bonus
    return 0


# ---------------------------------------------------------------------------
# Orchestrate: score a response end-to-end and persist
# ---------------------------------------------------------------------------

def score_aadc_design(design_id: str, required_checks: list[str]) -> dict[str, Any]:
    """Score an AADC design challenge response by running assess_design().

    Returns {judge_pts, rationale, check_results} where judge_pts is 0-100
    based on the fraction of required_checks that pass.
    Falls back to 0 if AADC is unavailable.
    """
    if not design_id:
        return {"judge_pts": 0, "rationale": "No design_id provided", "check_results": []}
    try:
        from tools.agentic_ai_canvas.db.init_db import init_db as _aadc_init
        from tools.db.storage import get_connection as _gc
        _aadc_init()
        conn = _gc()
        row = conn.execute(
            "SELECT graph_json, metadata_json FROM aadc_designs WHERE design_id = %s",
            (design_id,),
        ).fetchone()
        if not row:
            return {"judge_pts": 0, "rationale": f"Design {design_id} not found", "check_results": []}
        from tools.agentic_ai_canvas.agentic_engine import assess_design
        result = assess_design(row["graph_json"], json.loads(row["metadata_json"] or "{}"))
        all_checks = result.get("checks", [])
        if required_checks:
            relevant = [c for c in all_checks if c.get("id") in required_checks]
        else:
            relevant = all_checks
        passing = [c for c in relevant if c.get("passed")]
        pct = len(passing) / max(len(relevant), 1)
        judge_pts = round(pct * 100)
        check_summary = [{"id": c["id"], "title": c.get("title", ""), "passed": c.get("passed")}
                         for c in relevant]
        rationale = (
            f"AADC assessment: {len(passing)}/{len(relevant)} required checks pass "
            f"({judge_pts}/100). "
            + (f"Still failing: {[c['id'] for c in relevant if not c.get('passed')]}"
               if judge_pts < 100 else "All checks green.")
        )
        return {"judge_pts": judge_pts, "rationale": rationale, "check_results": check_summary}
    except Exception as exc:
        log.warning("AADC design scorer failed: %s", exc)
        return {"judge_pts": 0, "rationale": f"AADC scorer error: {exc}", "check_results": []}


# ---------------------------------------------------------------------------
# Red-first sprint: deterministic scoring of a team-written test (+ optional fix)
# ---------------------------------------------------------------------------

# Points: a test that fails on the buggy module AND passes on the hidden
# reference fix is a discriminating test (70); a team fix that passes the
# hidden acceptance tests earns the rest (30). A test that fails everywhere or
# passes everywhere proves nothing and earns no discrimination points.
RED_FIRST_DISCRIMINATION_PTS = 70
RED_FIRST_FIX_PTS = 30
_RED_FIRST_MARKER = "__RED_FIRST__"
_RED_FIRST_FILES = ("buggy.py", "reference_fix.py", "hidden_tests.py")

# Collected and run inside the code_runner sandbox; pytest is not importable
# there, so plain ``test_*`` functions are collected by hand. ``raises`` is a
# minimal stand-in for ``pytest.raises``.
_RED_FIRST_PREAMBLE = '''
import contextlib as _rf_contextlib


@_rf_contextlib.contextmanager
def raises(exc_type):
    try:
        yield
    except exc_type:
        return
    raise AssertionError("expected " + exc_type.__name__)


class _RFModule:
    pass


{module} = _RFModule()
{module}.__dict__.update({{k: v for k, v in globals().items() if not k.startswith("_")}})
'''

_RED_FIRST_RUNNER = '''
import json as _rf_json
import sys as _rf_sys

_rf_tests = [(n, f) for n, f in list(globals().items())
             if n.startswith("test_") and callable(f)]
_rf_failed = []
for _rf_name, _rf_fn in _rf_tests:
    try:
        _rf_fn()
    except BaseException as _rf_exc:
        _rf_failed.append(_rf_name + ": " + type(_rf_exc).__name__ + " " + str(_rf_exc)[:120])
print("__RED_FIRST__" + _rf_json.dumps({"collected": len(_rf_tests), "failed": _rf_failed}))
_rf_sys.exit(0 if _rf_tests and not _rf_failed else 1)
'''


def _red_first_fixture(rubric: dict) -> tuple[dict[str, str] | None, str]:
    """Load buggy/reference/hidden sources from the pack's fixture dir.

    The reference fix and hidden tests live on disk, NOT in the rubric: the
    rubric travels in ``config_json``, which the player page receives.
    """
    from .scenario_loader import _SCENARIOS_DIR

    rel = rubric.get("fixture_dir", "") if isinstance(rubric, dict) else ""
    if not rel:
        return None, "rubric has no fixture_dir"
    root = _SCENARIOS_DIR.resolve()
    fdir = (root / rel).resolve()
    if not fdir.is_relative_to(root):
        return None, f"fixture_dir escapes the scenarios tree: {rel}"
    files = {}
    for name in _RED_FIRST_FILES:
        path = fdir / name
        if not path.is_file():
            return None, f"fixture file missing: {rel}/{name}"
        files[name] = path.read_text(encoding="utf-8")
    return files, ""


def _parse_red_first_submission(response_text: str) -> tuple[str, str]:
    """Return (test_code, fix_code). JSON ``{test_code, fix_code}`` or bare test code."""
    text = response_text or ""
    if text.strip().startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return str(payload.get("test_code") or ""), str(payload.get("fix_code") or "")
    return text, ""


def _run_red_first(module_src: str, test_src: str, module_name: str) -> dict[str, Any]:
    """Run ``test_src`` against ``module_src`` in the Academy sandbox."""
    import re

    from apps.forge_academy.code_runner import run_code

    # The module's names are already in the shared namespace; an import of the
    # module itself would fail in the sandbox, so drop it.
    name = re.escape(module_name)
    test_src = re.sub(rf"^[ \t]*from[ \t]+{name}[ \t]+import[ \t]+\([^)]*\)", "", test_src, flags=re.M)
    test_src = re.sub(rf"^[ \t]*(from[ \t]+{name}[ \t]+import[ \t].*|import[ \t]+{name}\b.*)$",
                      "", test_src, flags=re.M)
    grader = _RED_FIRST_PREAMBLE.format(module=module_name) + "\n" + test_src + "\n" + _RED_FIRST_RUNNER
    result = run_code(module_src, grader)
    summary = {"collected": 0, "failed": []}
    for line in (result.get("stdout") or "").splitlines():
        if line.startswith(_RED_FIRST_MARKER):
            try:
                summary = json.loads(line[len(_RED_FIRST_MARKER):])
            except Exception:
                pass
    return {
        "passed": bool(result.get("passed")),
        "collected": int(summary.get("collected", 0)),
        "failed": summary.get("failed", []),
        "blocked": result.get("error") == "blocked",
        "stderr": (result.get("stderr") or "")[:400],
    }


def score_red_first(response_text: str, rubric: dict) -> dict[str, Any]:
    """Score a red-first sprint deterministically — no LLM.

    1. the team's test must FAIL against the buggy module (red);
    2. the same test must PASS against the hidden reference fix (proves it is
       not merely always-failing);
    3. the team's fix, if submitted, must pass the hidden acceptance tests.

    Returns {judge_pts, rationale, checks[, unscored]}.
    """
    files, err = _red_first_fixture(rubric)
    if files is None:
        log.warning("red-first scorer: %s", err)
        return {"judge_pts": 0, "rationale": f"Red-first scorer unavailable: {err}",
                "checks": {}, "unscored": True}
    module_name = (rubric.get("module_name") or "chunker").strip()
    if not module_name.isidentifier():
        return {"judge_pts": 0, "rationale": f"Invalid module_name {module_name!r}",
                "checks": {}, "unscored": True}

    test_code, fix_code = _parse_red_first_submission(response_text)
    checks = {"red_on_buggy": False, "green_on_reference": False, "fix_passes_hidden": False}
    notes: list[str] = []

    on_buggy = _run_red_first(files["buggy.py"], test_code, module_name)
    if on_buggy["blocked"]:
        return {"judge_pts": 0, "checks": checks,
                "rationale": f"Test rejected by the sandbox: {on_buggy['stderr']}"}
    if on_buggy["collected"] == 0:
        return {"judge_pts": 0, "checks": checks,
                "rationale": "No test_ functions were collected from the submission."}
    on_ref = _run_red_first(files["reference_fix.py"], test_code, module_name)
    checks["red_on_buggy"] = not on_buggy["passed"]
    checks["green_on_reference"] = on_ref["passed"]

    pts = 0
    if checks["red_on_buggy"] and checks["green_on_reference"]:
        pts += RED_FIRST_DISCRIMINATION_PTS
        notes.append("Test is RED on the buggy code and GREEN on the fix.")
    elif not checks["red_on_buggy"]:
        notes.append("Test does not discriminate: it PASSES on the buggy code.")
    else:
        notes.append("Test does not discriminate: it also FAILS on the reference fix "
                     f"({'; '.join(on_ref['failed'][:2]) or on_ref['stderr'][:160]}).")

    if fix_code.strip():
        on_fix = _run_red_first(fix_code, files["hidden_tests.py"], module_name)
        checks["fix_passes_hidden"] = on_fix["passed"]
        if on_fix["passed"]:
            pts += RED_FIRST_FIX_PTS
            notes.append("Fix passes the hidden acceptance tests.")
        else:
            notes.append(f"Fix fails {len(on_fix['failed']) or 'the'} hidden acceptance test(s).")
    else:
        notes.append("No fix submitted.")

    return {"judge_pts": pts, "checks": checks,
            "rationale": f"Red-first: {pts}/100. " + " ".join(notes)}


def score_response(
    response_id: int,
    team_id: int,
    inject_id: str,
    session_id: int,
    response_text: str,
    inject_body: str,
    receipts: list[dict],
    rubric: dict,
    bonus_per_call: int,
    max_receipt_bonus: int,
    time_taken_s: float | None,
    time_bonus_enabled: bool = True,
    inject_type: str = "",
) -> dict[str, Any]:
    """Run full scoring pipeline and write result to ttx_scores."""
    receipt_pts, receipt_count = validate_receipts(
        team_id, session_id, receipts, bonus_per_call, max_receipt_bonus
    )
    receipt_evidence = "\n".join(
        f"[{r.get('tool', '?')}] call_id={r.get('call_id', '?')}" for r in receipts
    )

    # AADC design challenge: replace LLM judge with automated compliance scoring
    if inject_type == "aadc_design_challenge":
        try:
            payload = json.loads(response_text) if response_text.strip().startswith("{") else {}
        except Exception:
            payload = {}
        design_id = payload.get("design_id", "")
        required_checks = rubric.get("required_checks", [])
        aadc_result = score_aadc_design(design_id, required_checks)
        judge_result = {
            "dimension_scores": {},
            "total": aadc_result["judge_pts"],
            "rationale": aadc_result["rationale"],
            "confidence": 1.0,
            "check_results": aadc_result["check_results"],
        }
    # Red-first sprint: run the team's test against buggy/fixed code instead
    # of asking an LLM whether it looks right.
    elif inject_type == "red_first_sprint":
        rf = score_red_first(response_text, rubric if isinstance(rubric, dict) else {})
        judge_result = {
            "dimension_scores": {},
            "total": rf["judge_pts"],
            "rationale": rf["rationale"],
            "confidence": 0.0 if rf.get("unscored") else 1.0,
            "red_first": rf["checks"],
        }
        if rf.get("unscored"):
            judge_result["unscored"] = True
            judge_result["unscored_reason"] = rf["rationale"]
    else:
        judge_result = judge_response(inject_body, response_text, rubric, receipt_evidence)

    judge_pts = judge_result["total"]
    judge_unscored = bool(judge_result.get("unscored"))
    time_bonus = compute_time_bonus(time_taken_s) if time_bonus_enabled else 0
    # judge_pts is 0 when unscored, so an LLM outage adds no fabricated points —
    # the response simply earns receipt + time bonus and is flagged unscored via
    # judge_rationale_json (persisted below).
    total_pts = receipt_pts + judge_pts + time_bonus

    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_scores
           (response_id, team_id, inject_id,
            receipt_pts, receipt_count, judge_pts, time_bonus_pts, total_pts,
            judge_rationale_json, judged_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            response_id, team_id, inject_id,
            receipt_pts, receipt_count, judge_pts, time_bonus, total_pts,
            json.dumps(judge_result), _now(),
        ),
    )
    conn.commit()

    # Update team aggregate
    conn.execute(
        "UPDATE ttx_teams SET total_score = total_score + %s WHERE team_id = %s",
        (total_pts, team_id),
    )
    conn.commit()

    out: dict[str, Any] = {
        "receipt_pts": receipt_pts,
        "receipt_count": receipt_count,
        "judge_pts": judge_pts,
        "judge_unscored": judge_unscored,
        "time_bonus_pts": time_bonus,
        "total_pts": total_pts,
        "rationale": judge_result.get("rationale", ""),
    }
    if inject_type == "aadc_design_challenge":
        out["aadc_score"] = judge_pts
    if inject_type == "red_first_sprint":
        out["red_first"] = judge_result.get("red_first", {})
    return out
