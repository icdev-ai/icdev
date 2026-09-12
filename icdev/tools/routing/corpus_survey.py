#!/usr/bin/env python3
# CUI // SP-CTI
"""Routing regression corpus -- replay every case, report agreement per router.

WHY (xrv-route-02). ICDEV keeps FOUR deliberately separate deterministic
routers and the tests asserting "query X routes to Y" totalled ~14 cases across
them, none of which named a skill:

    chat_canvas     tools/chat_router/intent_classifier.py::classify
                    -- WHICH CANVAS (intake or one of nine design canvases)
    cortex_facade   tools/cortex/intent_router.py::route
                    -- WHICH CORTEX FACADE (search | ask | complete | agent)
    rag_shape       tools/rag/query_classifier.py::classify_query
                    -- WHAT SHAPE of answer a RAG query needs
    skill_category  tools/agent/skill_selector.py::select_skills
                    -- WHICH SKILL CATEGORIES to inject

Read the boundary note at the top of ``intent_router.py`` before touching any of
them: the separation is deliberate (cxo-adopt-07) and this module does NOT add a
fifth router. It owns no rule, no keyword and no threshold -- it replays a
declared corpus through the routers that exist and reports where they disagree
with it. The corpus itself is data: ``tests/routing/corpus.yaml``.

REPORT ONLY, no ``--gate``. It measures the ROUTERS against a corpus, which is
what ``tests/routing/test_routing_corpus.py`` already fails on in CI; a survey
carrying a second gate earns itself a ``|| true`` (kpr-fix-03).

ONE STATEMENT OF HOW A ROUTER IS CALLED. ``ROUTERS`` below is the only place in
the tree that says "to ask the cortex router about a query, call route() and read
``intent``". The parametrized test imports it rather than re-spelling it, because
two spellings of one invocation is how a suite and its survey come to disagree
about a router neither of them changed.

DETERMINISM IS A PROPERTY OF THE CORPUS, NOT A CONVENIENCE. Two of the four
routers can reach a provider -- ``classify``'s low-confidence fallback, and
``classify_query``, which is LLM-FIRST -- so their answer moves with whether a
provider happens to be reachable from the host running the survey. Every adapter
therefore asks for the DETERMINISTIC lane explicitly (``allow_llm_fallback=False``
/ ``allow_llm=False``, the public keywords those two functions carry) and the
report SAYS SO per router under ``determinism``. A corpus whose expected values
move with network reachability is not a corpus, and an agreement rate measured
over one is not a measurement. What that lane does NOT cover is stated rather
than implied: the provider fallback is not pinned here and cannot be. The
DISPATCH is pinned separately -- see ``tests/routing/test_routing_corpus.py``,
which asserts the public entry point agrees with the deterministic lane when no
provider answers.

FIVE VERDICTS PER CASE, and ``unmeasurable`` is never folded into the others:

    agrees        the router returned what the corpus declares
    disagrees     it returned something else. Reported BY NAME, with both values.
    known         a case the corpus declares the router gets WRONG today
                  (``known_disagreement``) and which it still gets wrong.
                  Counted apart from ``disagrees`` so a standing defect cannot
                  read as a fresh regression.
    resolved      a known-wrong case that now AGREES -- reported, because a
                  defect silently fixed leaves a stale declaration behind.
    unmeasurable  the router could not be asked: its module would not import, or
                  it raised. NOT a disagreement -- "the rules changed their mind"
                  and "the rules could not be reached" send a reader to different
                  repairs.

Every rate is None -- never 0.0 and never 100.0 -- over an empty denominator
(args/perfect_score_gate.yaml, ratcheted to 0 by rem-hyg-13).

Usage:
    python -m tools.routing.corpus_survey --json
    python -m tools.routing.corpus_survey
    python -m tools.routing.corpus_survey --router cortex_facade --json
    python -m tools.routing.corpus_survey --list-routers
    python -m tools.routing.corpus_survey --validate
    python -m tools.routing.corpus_survey --corpus <path> --json

Exit codes: 0 a report was produced, whatever it says; 1 ``--validate`` found a
broken corpus DECLARATION; 2 a report could not be produced at all, which is
never the same as a clean report.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from icdev.core.paths import repo_root as _repo_root
except Exception:  # pragma: no cover -- installed kernel absent
    def _repo_root(start: str) -> Path:
        return Path(start).resolve().parents[2]

BASE_DIR: Path = Path(_repo_root(__file__))
CORPUS_PATH: Path = BASE_DIR / "tests" / "routing" / "corpus.yaml"

VERDICT_AGREES = "agrees"
VERDICT_DISAGREES = "disagrees"
VERDICT_KNOWN = "known"
VERDICT_RESOLVED = "resolved"
VERDICT_UNMEASURABLE = "unmeasurable"

#: A corpus case may carry these keys and no others. An unknown key is a
#: declaration error rather than a silently ignored field -- a typo'd
#: ``expcted`` would otherwise make a case assert nothing at all.
CASE_KEYS = frozenset({
    "id", "query", "router", "expected", "note", "context",
    "expected_agent_mode", "known_disagreement",
})
REQUIRED_CASE_KEYS = ("query", "router", "expected")

#: The literal a ``skill_category`` case uses for "nothing matched". The
#: selector's own safety fallback injects EVERY category in that situation, so
#: the set it returns says nothing about the query -- flattening it into a list
#: of category names would read as a confident six-category match.
FALLBACK_ALL = "fallback_all"

#: Separator joining a multi-category expectation. A case pins the WHOLE matched
#: set, not "is build in there": a rule that starts matching two extra categories
#: has changed what gets injected into an agent's context, and a membership test
#: would call that unchanged.
CATEGORY_SEP = "+"


class CorpusError(RuntimeError):
    """The corpus itself could not be read, or is not well formed."""


# ---------------------------------------------------------------------------
# Router adapters -- the ONE statement of how each router is invoked
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouterSpec:
    """How to ask one router a question, and what its answers may be."""

    name: str
    entry_point: str
    question: str
    default: str
    determinism: str
    ask: Callable[[Dict[str, Any]], Dict[str, Any]]
    values: Callable[[], Optional[frozenset]] = field(default=lambda: None)

    def describe(self) -> Dict[str, Any]:
        vals = self.values()
        return {
            "name": self.name,
            "entry_point": self.entry_point,
            "question": self.question,
            "default": self.default,
            "determinism": self.determinism,
            "values": sorted(vals) if vals else None,
        }


def _ask_chat_canvas(case: Dict[str, Any]) -> Dict[str, Any]:
    from tools.chat_router.intent_classifier import classify

    raw = classify(case.get("query") or "", allow_llm_fallback=False)
    return {"actual": raw.get("mode"), "detail": raw}


def _ask_cortex_facade(case: Dict[str, Any]) -> Dict[str, Any]:
    from tools.cortex.intent_router import route

    raw = route(case.get("query") or "", allow_llm_fallback=False)
    return {
        "actual": raw.get("intent"),
        "actual_agent_mode": raw.get("agent_mode"),
        "detail": raw,
    }


def _ask_rag_shape(case: Dict[str, Any]) -> Dict[str, Any]:
    from tools.rag.query_classifier import classify_query

    raw = classify_query(case.get("query") or "", case.get("context") or "",
                         allow_llm=False)
    return {"actual": raw.get("label"), "detail": raw}


def _ask_skill_category(case: Dict[str, Any]) -> Dict[str, Any]:
    from tools.agent.skill_selector import select_skills

    raw = select_skills(query=case.get("query") or "")
    if raw.get("status") == "fallback_all":
        return {"actual": FALLBACK_ALL, "detail": raw}
    names = sorted(c["name"] for c in raw.get("matched_categories", []))
    return {"actual": CATEGORY_SEP.join(names) or FALLBACK_ALL, "detail": raw}


def _chat_values() -> Optional[frozenset]:
    try:
        from tools.chat_router.intent_classifier import CANVAS_MODES, INTAKE_MODE

        return frozenset(CANVAS_MODES) | {INTAKE_MODE}
    except Exception:
        return None


def _cortex_values() -> Optional[frozenset]:
    try:
        from tools.cortex.intent_router import INTENT_FACADES

        return frozenset(INTENT_FACADES)
    except Exception:
        return None


def _rag_values() -> Optional[frozenset]:
    try:
        from tools.rag.query_classifier import TAXONOMY_LABELS

        return frozenset(TAXONOMY_LABELS)
    except Exception:
        return None


def _skill_values() -> Optional[frozenset]:
    """Category names the selector can emit, from its own live config.

    Derived rather than hard-coded, so a category added to
    ``args/skill_injection_config.yaml`` does not turn every case naming it into
    a declaration error.
    """
    try:
        from tools.agent.skill_selector import load_config

        cats = (load_config() or {}).get("categories") or {}
        return frozenset(cats) | {FALLBACK_ALL}
    except Exception:
        return None


ROUTERS: Dict[str, RouterSpec] = {
    "chat_canvas": RouterSpec(
        name="chat_canvas",
        entry_point="tools.chat_router.intent_classifier.classify -> mode",
        question="which canvas does this chat message belong to",
        default="intake",
        determinism="keyword rules only (allow_llm_fallback=False); the "
                    "low-confidence provider fallback is NOT exercised",
        ask=_ask_chat_canvas,
        values=_chat_values,
    ),
    "cortex_facade": RouterSpec(
        name="cortex_facade",
        entry_point="tools.cortex.intent_router.route -> intent",
        question="which Cortex facade serves this message",
        default="ask",
        determinism="keyword rules only; its base signal is asked with "
                    "allow_llm_fallback=False",
        ask=_ask_cortex_facade,
        values=_cortex_values,
    ),
    "rag_shape": RouterSpec(
        name="rag_shape",
        entry_point="tools.rag.query_classifier.classify_query -> label",
        question="what shape of answer does this RAG query need",
        default="fact_single",
        determinism="heuristic rules only (allow_llm=False); this classifier is "
                    "LLM-FIRST in its default configuration",
        ask=_ask_rag_shape,
        values=_rag_values,
    ),
    "skill_category": RouterSpec(
        name="skill_category",
        entry_point="tools.agent.skill_selector.select_skills -> matched "
                    "categories, joined with '+'",
        question="which skill categories should be injected for this task",
        default=FALLBACK_ALL,
        determinism="fully deterministic; reaches no provider",
        ask=_ask_skill_category,
        values=_skill_values,
    ),
}


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def load_corpus(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read the corpus. Raises CorpusError when it cannot be read at all."""
    target = Path(path) if path is not None else CORPUS_PATH
    if not target.exists():
        raise CorpusError(f"corpus not found: {target}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover -- pyyaml is a declared dep
        raise CorpusError(f"pyyaml unavailable: {exc}") from exc
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise CorpusError(f"{target}: {exc}") from exc
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise CorpusError(f"{target}: a top-level `cases:` list is required")
    out: List[Dict[str, Any]] = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise CorpusError(f"{target}: case {i} is not a mapping")
        entry = dict(case)
        entry.setdefault("id", f"{entry.get('router', 'case')}-{i:03d}")
        out.append(entry)
    return out


def validate_corpus(cases: Optional[Sequence[Dict[str, Any]]] = None) -> List[str]:
    """Return a list of problems with the corpus DECLARATION. [] = valid.

    This checks the corpus against itself and against each router's declared
    value set. It says nothing about whether a router agrees -- that is
    :func:`replay`, and keeping the two apart is what stops a bad declaration
    reading as a routing regression.
    """
    rows = list(cases) if cases is not None else load_corpus()
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for i, case in enumerate(rows):
        label = case.get("id") or f"<case {i}>"
        unknown = sorted(set(case) - CASE_KEYS)
        if unknown:
            problems.append(f"{label}: unknown key(s): {', '.join(unknown)}")
        for key in REQUIRED_CASE_KEYS:
            if key not in case:
                problems.append(f"{label}: `{key}` is required")
            elif key != "query" and not str(case.get(key) or "").strip():
                problems.append(f"{label}: `{key}` must not be empty")
        # `query` may be empty or whitespace on purpose: "what does this router
        # do with nothing at all" is a real case and two of the four answer it
        # with an explicit early return.
        if "query" in case and not isinstance(case.get("query"), str):
            problems.append(f"{label}: `query` must be a string")
        seen[label] = seen.get(label, 0) + 1
        router = ROUTERS.get(str(case.get("router") or ""))
        if router is None:
            problems.append(
                f"{label}: unknown router {case.get('router')!r} "
                f"(declared: {', '.join(sorted(ROUTERS))})")
            continue
        if not str(case.get("note") or "").strip():
            problems.append(f"{label}: a `note` is required -- a case whose "
                            "intent nobody can read cannot be re-judged later")
        declared = router.values()
        expected = str(case.get("expected") or "")
        if declared:
            parts = (expected.split(CATEGORY_SEP)
                     if router.name == "skill_category" else [expected])
            for part in parts:
                if part and part not in declared:
                    problems.append(
                        f"{label}: expected {part!r} is not a value "
                        f"{router.name} can return")
        if case.get("context") and router.name != "rag_shape":
            problems.append(f"{label}: `context` is only meaningful for rag_shape")
        if case.get("expected_agent_mode") and router.name != "cortex_facade":
            problems.append(f"{label}: `expected_agent_mode` is only meaningful "
                            "for cortex_facade")
        if "known_disagreement" in case and \
                not str(case.get("known_disagreement") or "").strip():
            problems.append(f"{label}: `known_disagreement` must state WHY")
    for label, count in seen.items():
        if count > 1:
            problems.append(f"{label}: case id declared {count} times")
    return problems


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _rate(part: int, total: int) -> Optional[float]:
    """Percentage, or None over an EMPTY denominator -- never 0.0 or 100.0.

    The one place a percentage is computed here. ``pct if total else 100.0``
    would breach args/perfect_score_gate.yaml, ratcheted to 0 by rem-hyg-13: a
    router nobody asked anything must not render as one that answered perfectly.

    100.0 is reserved for a rate that IS 100. An imperfect rate that rounds up
    to it at one decimal place FLOORS to 99.9 instead (dwr-fid-02's rule) --
    it cannot arise at today's 195 cases, where the worst imperfect rate rounds
    to 99.5, and it arises the day the corpus passes ~2000.
    """
    if total <= 0:
        return None
    pct = round(part / total * 100.0, 1)
    if pct >= 100.0 and part < total:
        return 99.9
    return pct


def judge(case: Dict[str, Any], answer: Dict[str, Any]) -> Dict[str, Any]:
    """Compare ONE answer against ONE case. Pure -- no router is called here."""
    expected = str(case.get("expected") or "")
    actual = answer.get("actual")
    known = str(case.get("known_disagreement") or "").strip()
    if answer.get("error"):
        return {"verdict": VERDICT_UNMEASURABLE, "actual": None,
                "reason": answer["error"]}
    matched = actual == expected
    mode_expected = str(case.get("expected_agent_mode") or "").strip()
    mode_actual = answer.get("actual_agent_mode")
    detail = None
    if matched and mode_expected and mode_actual != mode_expected:
        matched = False
        detail = (f"intent {expected} as declared, but agent_mode "
                  f"{mode_actual!r} != {mode_expected!r}")
    if known:
        # A declared-wrong case that is STILL wrong is not news; one that has
        # started agreeing means the declaration is stale and must be removed,
        # so it is reported rather than quietly passing.
        verdict = VERDICT_RESOLVED if matched else VERDICT_KNOWN
    else:
        verdict = VERDICT_AGREES if matched else VERDICT_DISAGREES
    out: Dict[str, Any] = {"verdict": verdict, "actual": actual}
    if mode_actual is not None:
        out["actual_agent_mode"] = mode_actual
    if detail:
        out["reason"] = detail
    if known:
        out["known_disagreement"] = known
    return out


def ask_router(spec: RouterSpec, case: Dict[str, Any]) -> Dict[str, Any]:
    """Put one case to one router. Never raises -- a raise is unmeasurable."""
    try:
        return spec.ask(case)
    except Exception as exc:  # noqa: BLE001 -- an unaskable router is a verdict
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _router_state(total: int, counts: Dict[str, int]) -> str:
    """never_asked | unmeasurable | disagreements | known_only | clean.

    Five, never merged. A router with no case in the corpus is ``never_asked``,
    which is not a clean bill of health: it is the one state a bare agreement
    percentage cannot express, and the one this corpus exists to remove. And a
    router carrying DECLARED defects is ``known_only`` rather than ``clean`` --
    reading "clean" over three standing disagreements is how a known defect
    becomes an accepted one.
    """
    if total == 0:
        return "never_asked"
    if counts[VERDICT_UNMEASURABLE] == total:
        return "unmeasurable"
    if counts[VERDICT_DISAGREES]:
        return "disagreements"
    if counts[VERDICT_KNOWN] or counts[VERDICT_RESOLVED]:
        return "known_only"
    return "clean"


def replay(cases: Optional[Sequence[Dict[str, Any]]] = None,
           routers: Optional[Iterable[str]] = None,
           corpus_path: Optional[Path] = None) -> Dict[str, Any]:
    """Replay the corpus and report agreement per router."""
    rows = list(cases) if cases is not None else load_corpus(corpus_path)
    wanted = set(routers) if routers else set(ROUTERS)
    per_router: Dict[str, Dict[str, Any]] = {}
    for name in sorted(wanted):
        spec = ROUTERS.get(name)
        if spec is None:
            per_router[name] = {"state": "undeclared_router", "total": 0,
                                "measured": 0, "agreement_pct": None}
            continue
        section: Dict[str, Any] = dict(spec.describe())
        counts = {VERDICT_AGREES: 0, VERDICT_DISAGREES: 0, VERDICT_KNOWN: 0,
                  VERDICT_RESOLVED: 0, VERDICT_UNMEASURABLE: 0}
        disagreements: List[Dict[str, Any]] = []
        known: List[Dict[str, Any]] = []
        resolved: List[Dict[str, Any]] = []
        unmeasurable: List[Dict[str, Any]] = []
        for case in rows:
            if case.get("router") != name:
                continue
            verdict = judge(case, ask_router(spec, case))
            counts[verdict["verdict"]] += 1
            record = {"id": case.get("id"), "query": case.get("query"),
                      "expected": case.get("expected"),
                      "actual": verdict.get("actual"),
                      "note": case.get("note")}
            if verdict.get("reason"):
                record["reason"] = verdict["reason"]
            if verdict["verdict"] == VERDICT_DISAGREES:
                disagreements.append(record)
            elif verdict["verdict"] == VERDICT_KNOWN:
                record["known_disagreement"] = verdict.get("known_disagreement")
                known.append(record)
            elif verdict["verdict"] == VERDICT_RESOLVED:
                record["known_disagreement"] = verdict.get("known_disagreement")
                resolved.append(record)
            elif verdict["verdict"] == VERDICT_UNMEASURABLE:
                unmeasurable.append(record)
        total = sum(counts.values())
        # The denominator is what was MEASURED: a case the router could not be
        # asked about is excluded from BOTH sides rather than counted as a miss.
        measured = total - counts[VERDICT_UNMEASURABLE]
        section.update({
            "total": total,
            "measured": measured,
            "agrees": counts[VERDICT_AGREES],
            "disagrees": counts[VERDICT_DISAGREES],
            "known_disagreements": counts[VERDICT_KNOWN],
            "resolved_disagreements": counts[VERDICT_RESOLVED],
            "unmeasurable": counts[VERDICT_UNMEASURABLE],
            # `resolved` counts as agreement: the router returned the declared
            # value. It is ALSO reported separately, because the declaration
            # saying it would not is now wrong.
            "agreement_pct": _rate(
                counts[VERDICT_AGREES] + counts[VERDICT_RESOLVED], measured),
            "state": _router_state(total, counts),
            "disagreement_detail": disagreements,
            "known_detail": known,
            "resolved_detail": resolved,
            "unmeasurable_detail": unmeasurable,
        })
        per_router[name] = section

    totals = {
        "cases": sum(s.get("total", 0) for s in per_router.values()),
        "measured": sum(s.get("measured", 0) for s in per_router.values()),
        "agrees": sum(s.get("agrees", 0) for s in per_router.values()),
        "disagrees": sum(s.get("disagrees", 0) for s in per_router.values()),
        "known_disagreements": sum(s.get("known_disagreements", 0)
                                   for s in per_router.values()),
        "resolved_disagreements": sum(s.get("resolved_disagreements", 0)
                                      for s in per_router.values()),
        "unmeasurable": sum(s.get("unmeasurable", 0)
                            for s in per_router.values()),
    }
    totals["agreement_pct"] = _rate(
        totals["agrees"] + totals["resolved_disagreements"], totals["measured"])
    return {
        "classification": "CUI // SP-CTI",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(corpus_path or CORPUS_PATH),
        "corpus_cases": len(rows),
        "routers": per_router,
        "totals": totals,
        "report_only": True,
    }


# ---------------------------------------------------------------------------
# Human report
# ---------------------------------------------------------------------------


def human(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Routing regression corpus")
    lines.append(f"  corpus : {report.get('corpus')}")
    lines.append(f"  cases  : {report.get('corpus_cases')}")
    lines.append("")
    for name, sec in sorted(report.get("routers", {}).items()):
        pct = sec.get("agreement_pct")
        shown = "unmeasured" if pct is None else f"{pct:.1f}%"
        # The numerator is agrees + resolved, which is what agreement_pct
        # counts -- printing `agrees` beside a percentage that includes a
        # resolved case would not add up on screen.
        numerator = sec.get("agrees", 0) + sec.get("resolved_disagreements", 0)
        lines.append(f"  {name:15s} {sec.get('state', '?'):14s} "
                     f"agreement {shown:>10s}  "
                     f"({numerator}/{sec.get('measured', 0)} measured,"
                     f" {sec.get('total', 0)} declared)")
        lines.append(f"    {sec.get('entry_point', '')}")
        lines.append(f"    determinism: {sec.get('determinism', '')}")
        for bad in sec.get("disagreement_detail", []):
            lines.append(f"    DISAGREES {bad['id']}: expected "
                         f"{bad['expected']!r}, got {bad['actual']!r}")
            lines.append(f"      query: {str(bad['query'])[:90]}")
        for item in sec.get("known_detail", []):
            lines.append(f"    known    {item['id']}: expected "
                         f"{item['expected']!r}, got {item['actual']!r} -- "
                         f"{item.get('known_disagreement')}")
        for item in sec.get("resolved_detail", []):
            lines.append(f"    RESOLVED {item['id']}: now agrees; remove the "
                         "known_disagreement declaration")
        for item in sec.get("unmeasurable_detail", []):
            lines.append(f"    UNMEASURABLE {item['id']}: {item.get('reason')}")
        lines.append("")
    tot = report.get("totals", {})
    pct = tot.get("agreement_pct")
    lines.append("  TOTAL agreement: "
                 f"{'unmeasured' if pct is None else f'{pct:.1f}%'} "
                 f"({tot.get('agrees', 0) + tot.get('resolved_disagreements', 0)}"
                 f"/{tot.get('measured', 0)}), "
                 f"{tot.get('disagrees', 0)} disagreement(s), "
                 f"{tot.get('known_disagreements', 0)} known, "
                 f"{tot.get('unmeasurable', 0)} unmeasurable")
    lines.append("  report only -- no gate (kpr-fix-03)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay the routing regression corpus over the four routers.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p.add_argument("--router", action="append", metavar="NAME",
                   help="Replay one router only (repeatable)")
    p.add_argument("--corpus", metavar="PATH", help="Alternate corpus file")
    p.add_argument("--list-routers", action="store_true",
                   help="Print the declared routers and exit")
    p.add_argument("--validate", action="store_true",
                   help="Check the corpus DECLARATION; exit 1 on a problem")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    if args.list_routers:
        specs = [spec.describe() for _, spec in sorted(ROUTERS.items())]
        if args.json:
            print(json.dumps({"routers": specs, "count": len(specs)}, indent=2))
        else:
            for spec in specs:
                print(f"  {spec['name']:15s} {spec['entry_point']}")
                print(f"    {spec['question']}; default {spec['default']!r}")
        return 0

    corpus_path = Path(args.corpus) if args.corpus else None
    try:
        cases = load_corpus(corpus_path)
    except CorpusError as exc:
        payload = {"error": str(exc), "report_produced": False}
        print(json.dumps(payload, indent=2) if args.json else f"ERROR: {exc}")
        return 2

    if args.validate:
        problems = validate_corpus(cases)
        if args.json:
            print(json.dumps({"cases": len(cases), "problems": problems,
                              "valid": not problems}, indent=2))
        else:
            print(f"{len(cases)} case(s); "
                  f"{len(problems) or 'no'} declaration problem(s)")
            for prob in problems:
                print(f"  {prob}")
        return 1 if problems else 0

    report = replay(cases, routers=args.router, corpus_path=corpus_path)
    print(json.dumps(report, indent=2) if args.json else human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
