#!/usr/bin/env python3
# CUI // SP-CTI
"""Operator CLI for AGOV declarative detection (agov-det-07).

Four verbs, all with ``--json``:

``--list``
    Catalog the rules a directory yields — id, severity, kind, enforce, source
    path — plus any files that were skipped and why. Read-only.

``--check --rules-dir <dir>``
    Validate a rule directory. **Exits non-zero on any invalid rule.** This is
    the verb an operator runs before copying a rule into
    ``args/agent_rules_enforce/``, and the verb CI runs against the shipped
    pack. A rule that fails to compile is inert, not match-all, so the exit code
    is the only signal that a directory is not doing what its author thinks.

``--test``
    Evaluate the loaded rules against fixture events with declared expectations
    (``context/agent_detect/fixtures/*.yaml``). Exits non-zero when a rule stops
    firing on the case it was written for, or starts firing on a case declared
    negative. Fixtures are synthetic :class:`AgentEvent` records — they exercise
    the matcher, they do not observe a real session.

``--scan --session <id>``
    Evaluate the rules against the events already stored for one session, via
    the read-only normalizer in :mod:`tools.agent_detect.events`. Read-only by
    default; ``--record`` opts into appending findings to ``agent_findings``.

WHAT THIS DOES NOT ESTABLISH: a match is a RULE MATCH AND NOT PROOF OF
EXECUTION. ``--scan`` reads rows that say a tool call was requested; nothing in
the event stream records that the command ran or what it did. See
docs/features/agov-det-coverage-and-limits.md for the per-source fidelity table
and the known-missing list.

Exit codes:
    0  the verb completed and every check it makes passed
    1  a check failed (invalid rule under ``--check``, fixture mismatch under
       ``--test``)
    2  usage error, or the verb could not run at all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.agent_detect import events as events_mod  # noqa: E402
from tools.agent_detect import rules as rules_mod  # noqa: E402
from tools.agent_detect import sequence as sequence_mod  # noqa: E402

EXIT_OK = 0
EXIT_FAILED_CHECK = 1
EXIT_USAGE = 2

#: Default fixture location. Deliberately NOT under ``args/agent_rules/`` — the
#: loader globs every ``*.yaml`` under the rules directory, so a fixture file
#: parked there would be compiled as a rule and reported as invalid.
FIXTURES_DIRNAME = Path("context") / "agent_detect" / "fixtures"

#: Repeated verbatim in the rule-pack README, the manifest shard and the
#: coverage doc. One sentence, one meaning, everywhere it is surfaced.
NOT_PROOF = "A finding is a RULE MATCH AND NOT PROOF OF EXECUTION."


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _resolve_rules_dir(raw: Optional[str]) -> tuple[Optional[Path], Optional[str]]:
    """Resolve ``--rules-dir``. Returns ``(path, error)``; never raises.

    An explicitly named directory that does not exist is a usage error — the
    operator asked about a specific place and deserves to be told it is not
    there. An absent DEFAULT directory is not an error (an empty pack is the
    designed state), which is why the two cases are distinguished here.
    """
    if raw:
        path = Path(raw)
        if not path.is_dir():
            return None, f"not a directory: {path}"
        return path, None
    return rules_mod.default_rules_dir(), None


def _load(rules_dir: Optional[Path]) -> rules_mod.RuleSet:
    """Load a directory fresh. ``refresh=True``: an operator running --check
    twice around an edit must not be served the stat-signature cache."""
    return rules_mod.load_rules(rules_dir, refresh=True)


def _rule_mapping(rule: rules_mod.Rule) -> dict:
    """A compiled :class:`Rule` in the shape :func:`evaluate_sequence` wants.

    ``evaluate_sequence`` takes the raw YAML mapping (it re-validates the
    ``sequence`` block itself) while the loader hands back a compiled ``Rule``.
    This is the one adapter between them; it is not a second parser.
    """
    return {
        "id": rule.rule_id,
        "version": rule.version,
        "title": rule.title,
        "severity": rule.severity,
        "tags": list(rule.tags),
        "enabled": rule.enabled,
        "enforce": rule.enforce,
        "sequence": rule.sequence,
    }


def _evaluate(
    ruleset: rules_mod.RuleSet, event_list: Sequence[Any]
) -> tuple[list[dict], list[dict]]:
    """Run both evaluators over ``event_list``.

    Single-event rules run per event; chain rules run once over the whole
    ordered list, because a chain is by definition a property of the sequence
    and not of any one member. Returns ``(event_hits, chain_hits)`` as dicts.
    """
    event_hits: list[dict] = []
    for event in event_list:
        for match in rules_mod.evaluate_event(event, ruleset):
            event_hits.append(match.to_dict())

    chain_hits: list[dict] = []
    for rule in ruleset.sequence_rules:
        for finding in sequence_mod.evaluate_sequence(_rule_mapping(rule), event_list):
            chain_hits.append(finding.to_dict())
    return event_hits, chain_hits


def _fired_rule_ids(event_hits: Iterable[Mapping], chain_hits: Iterable[Mapping]) -> set:
    ids = {str(h.get("rule_id")) for h in event_hits}
    ids |= {str(h.get("rule_id")) for h in chain_hits}
    return ids


def _emit(payload: dict, as_json: bool, lines: Sequence[str]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return
    for line in lines:
        print(line)


# ---------------------------------------------------------------------------
# --list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    rules_dir, error = _resolve_rules_dir(args.rules_dir)
    if error:
        return _fail(error, args.json)

    ruleset = _load(rules_dir)
    catalog = [rule.to_dict() for rule in ruleset.rules]
    payload = {
        "directory": ruleset.directory,
        "count": len(catalog),
        "enabled": sum(1 for r in catalog if r["enabled"]),
        "enforce_declared": sum(1 for r in catalog if r["enforce"]),
        "sequence_rules": sum(1 for r in catalog if r["kind"] == "sequence"),
        "rules": catalog,
        "errors": [e.to_dict() for e in ruleset.errors],
        "note": NOT_PROOF,
    }

    lines = [f"{payload['count']} rule(s) in {ruleset.directory or '<none>'}"]
    for rule in catalog:
        flags = []
        if not rule["enabled"]:
            flags.append("disabled")
        if rule["enforce"]:
            flags.append("enforce-declared")
        suffix = ("  [" + ", ".join(flags) + "]") if flags else ""
        lines.append(
            f"  {rule['rule_id']:<44} {rule['severity']:<8} {rule['kind']:<8}"
            f" {rule['title'][:60]}{suffix}"
        )
    if ruleset.errors:
        lines.append(f"{len(ruleset.errors)} file(s) skipped:")
        lines.extend(f"  {e.path}: {e.message}" for e in ruleset.errors)
    # An `enforce: true` rule under the shipped pack is inert (the enforcement
    # directory is the authority, not the field), so listing it without saying
    # so would read as "this blocks", which it does not.
    if payload["enforce_declared"]:
        lines.append(
            "note: `enforce: true` only blocks from the enforcement directory "
            "(args/agent_rules_enforce/, ICDEV_AGENT_ENFORCE_RULES_DIR)."
        )
    _emit(payload, args.json, lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    rules_dir, error = _resolve_rules_dir(args.rules_dir)
    if error:
        return _fail(error, args.json)

    ruleset = _load(rules_dir)
    files_seen = 0
    if rules_dir is not None and rules_dir.is_dir():
        files_seen = len(rules_mod.discover_rule_files(rules_dir))

    # The loader validates the `sequence` block for SHAPE; SequenceSpec applies
    # the semantic constraints (2..8 steps, a bounded window, max_matches). A
    # rule that passes the first and fails the second loads fine and then
    # matches nothing forever, which is exactly the silent-inert failure
    # `--check` exists to surface.
    errors = [e.to_dict() for e in ruleset.errors]
    for rule in ruleset.rules:
        if rule.sequence is None:
            continue
        try:
            sequence_mod.SequenceSpec.from_dict(rule.sequence)
        except sequence_mod.SequenceSpecError as exc:
            errors.append(
                {
                    "path": rule.source_path,
                    "message": f"{rule.rule_id}: unusable sequence block: {exc}",
                }
            )

    ok = not errors
    payload = {
        "ok": ok,
        "directory": ruleset.directory,
        "files_seen": files_seen,
        "rules_loaded": len(ruleset.rules),
        "invalid": len(errors),
        "errors": errors,
    }
    lines = [
        f"{'OK' if ok else 'FAILED'}: {len(ruleset.rules)} of {files_seen} file(s) "
        f"compiled in {ruleset.directory or '<none>'}"
    ]
    lines.extend(f"  {e['path']}: {e['message']}" for e in errors)
    _emit(payload, args.json, lines)
    return EXIT_OK if ok else EXIT_FAILED_CHECK


# ---------------------------------------------------------------------------
# --test
# ---------------------------------------------------------------------------


def default_fixtures_dir() -> Optional[Path]:
    """Locate the fixture pack from ``__file__``, never ``os.getcwd()``.

    This module is imported from worktrees and from the ``icdev/`` package
    mirror, whose repo root is a different directory (CLAUDE.md, "Notes for
    agents working from worktrees").
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / FIXTURES_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


def load_fixture_cases(path: Optional[Path]) -> tuple[list[dict], list[str]]:
    """Read fixture cases from a file or a directory of files.

    Returns ``(cases, errors)``. A malformed fixture is an error, not a crash,
    and not a silent skip: a fixture that stops loading is a regression test
    that stops testing, which is the failure mode this whole card is about.
    """
    if path is None:
        return [], ["no fixture directory found"]
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a hard dep here
        return [], [f"PyYAML unavailable: {exc}"]

    if path.is_dir():
        files = sorted(
            set(list(path.rglob("*.yaml")) + list(path.rglob("*.yml"))),
            key=lambda p: p.as_posix(),
        )
    elif path.is_file():
        files = [path]
    else:
        return [], [f"not a file or directory: {path}"]

    cases: list[dict] = []
    errors: list[str] = []
    for fixture_file in files:
        display = fixture_file.as_posix()
        try:
            doc = yaml.safe_load(fixture_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the rest
            errors.append(f"{display}: unreadable: {exc}")
            continue
        if not isinstance(doc, Mapping) or not isinstance(doc.get("cases"), list):
            errors.append(f"{display}: expected a mapping with a 'cases' list")
            continue
        for index, case in enumerate(doc["cases"]):
            if not isinstance(case, Mapping):
                errors.append(f"{display}: cases[{index}] is not a mapping")
                continue
            entry = dict(case)
            entry["_source"] = display
            cases.append(entry)
    return cases, errors


def _build_events(raw_events: Any) -> tuple[list[Any], list[str]]:
    """Construct :class:`AgentEvent` records from fixture dicts.

    Built through the real constructor on purpose: ``__post_init__`` enforces
    the closed vocabulary and the operand invariant, so a fixture cannot assert
    a shape the normalizer would never produce.
    """
    built: list[Any] = []
    errors: list[str] = []
    if not isinstance(raw_events, list) or not raw_events:
        return [], ["'events' must be a non-empty list"]
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            errors.append(f"events[{index}] is not a mapping")
            continue
        payload = dict(raw)
        payload.setdefault("event_id", f"fx-{index}")
        payload.setdefault("session_id", "fixture-session")
        payload.setdefault("ts", f"2026-01-01 00:00:{index:02d}")
        payload.setdefault("source", events_mod.SOURCE_HOOK_EVENTS)
        payload.setdefault("confidence", events_mod.CONFIDENCE_DIRECT)
        try:
            built.append(events_mod.AgentEvent(**payload))
        except Exception as exc:  # noqa: BLE001 — report the case, keep going
            errors.append(f"events[{index}]: {exc}")
    return built, errors


def cmd_test(args: argparse.Namespace) -> int:
    rules_dir, error = _resolve_rules_dir(args.rules_dir)
    if error:
        return _fail(error, args.json)

    fixtures = Path(args.fixtures) if args.fixtures else default_fixtures_dir()
    cases, load_errors = load_fixture_cases(fixtures)
    ruleset = _load(rules_dir)

    results: list[dict] = []
    for case in cases:
        name = str(case.get("name") or "<unnamed>")
        expected = {str(r) for r in (case.get("expect_rules") or [])}
        built, build_errors = _build_events(case.get("events"))
        if build_errors:
            results.append(
                {
                    "name": name,
                    "source": case.get("_source"),
                    "ok": False,
                    "reason": "; ".join(build_errors),
                    "expected": sorted(expected),
                    "fired": [],
                }
            )
            continue

        event_hits, chain_hits = _evaluate(ruleset, built)
        fired = _fired_rule_ids(event_hits, chain_hits)
        # Only rules the case speaks about are judged. A fixture written for
        # secrets.env_file_read must not start failing because someone added an
        # unrelated rule that also matches — that would make the pack
        # un-extendable. `also_expect_none` is how a case pins a specific rule
        # NOT firing.
        scope = expected | {str(r) for r in (case.get("expect_not_rules") or [])}
        observed = fired & scope if scope else set()
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        ok = not missing and not unexpected
        results.append(
            {
                "name": name,
                "source": case.get("_source"),
                "ok": ok,
                "expected": sorted(expected),
                "fired": sorted(fired),
                "missing": missing,
                "unexpected": unexpected,
            }
        )

    failed = [r for r in results if not r["ok"]]
    # Zero fixtures is a FAILURE, not a pass. A green `--test` that evaluated
    # nothing is precisely the overstated-completion artifact this card's doc
    # is a counterweight to.
    no_cases = not cases
    ok = not failed and not load_errors and not no_cases

    payload = {
        "ok": ok,
        "rules_directory": ruleset.directory,
        "fixtures_directory": str(fixtures) if fixtures else None,
        "cases": len(cases),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
        "errors": load_errors + (["no fixture cases found"] if no_cases else []),
        "note": (
            "Fixtures are synthetic events. Passing means the matcher behaves as "
            "declared; it says nothing about coverage of real sessions."
        ),
    }
    lines = [f"{'OK' if ok else 'FAILED'}: {payload['passed']}/{len(cases)} case(s) passed"]
    for result in failed:
        detail = []
        if result.get("missing"):
            detail.append("did not fire: " + ", ".join(result["missing"]))
        if result.get("unexpected"):
            detail.append("fired unexpectedly: " + ", ".join(result["unexpected"]))
        if result.get("reason"):
            detail.append(result["reason"])
        lines.append(f"  FAIL {result['name']} — {'; '.join(detail)}")
    lines.extend(f"  ERROR {e}" for e in payload["errors"])
    _emit(payload, args.json, lines)
    return EXIT_OK if ok else EXIT_FAILED_CHECK


# ---------------------------------------------------------------------------
# --scan
# ---------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    if not args.session:
        return _fail("--scan requires --session <id>", args.json, EXIT_USAGE)
    rules_dir, error = _resolve_rules_dir(args.rules_dir)
    if error:
        return _fail(error, args.json)

    try:
        stored = events_mod.fetch_events(session_id=args.session, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 — a DB that is not there is an operator problem
        return _fail(f"could not read events: {exc}", args.json, EXIT_USAGE)

    ruleset = _load(rules_dir)
    event_hits, chain_hits = _evaluate(ruleset, stored)

    recorded: list[dict] = []
    if args.record:
        recorded = _record_findings(args.session, event_hits, chain_hits)

    payload = {
        "session_id": args.session,
        "rules_directory": ruleset.directory,
        "events_scanned": len(stored),
        "event_summary": events_mod.summarize(stored),
        "matches": len(event_hits) + len(chain_hits),
        "event_matches": event_hits,
        "chain_matches": chain_hits,
        "recorded": recorded,
        "note": NOT_PROOF,
    }
    lines = [
        f"session {args.session}: {len(stored)} event(s), "
        f"{payload['matches']} match(es)"
    ]
    for hit in event_hits:
        lines.append(
            f"  [{hit['severity']:<8}] {hit['rule_id']:<44} event={hit.get('event_id')}"
        )
    for hit in chain_hits:
        lines.append(
            f"  [{hit['severity']:<8}] {hit['rule_id']:<44} chain="
            f"{'->'.join(hit.get('event_ids') or [])}"
        )
    if payload["matches"]:
        lines.append(NOT_PROOF)
    _emit(payload, args.json, lines)
    return EXIT_OK


def _record_findings(session_id: str, event_hits: list, chain_hits: list) -> list:
    """Append findings for a scan. Opt-in (``--record``): a scan is read-only
    by default so an operator can re-run it while tuning rules without
    accumulating rows in an append-only table they cannot delete."""
    from tools.agent_detect import findings as findings_mod

    out: list[dict] = []
    for hit in event_hits:
        event_id = hit.get("event_id")
        out.append(
            _record_one(
                findings_mod, hit, session_id,
                [event_id] if event_id else [],
            )
        )
    for hit in chain_hits:
        out.append(
            _record_one(findings_mod, hit, session_id, hit.get("event_ids") or [])
        )
    return out


def _record_one(findings_mod, hit: Mapping, session_id: str, event_ids: Sequence) -> dict:
    """One record call.

    A scan is an OBSERVATION — ``decision`` stays ``observed`` and ``enforced``
    stays False whatever the rule declares, because nothing was blocked here:
    this CLI runs after the fact and has nothing left to deny. Recording a
    post-hoc match as ``denied`` would put a claim in an append-only table that
    is simply not true.
    """
    try:
        return findings_mod.record(
            rule_id=hit["rule_id"],
            rule_version=str(hit.get("rule_version") or "1"),
            severity=hit.get("severity") or "info",
            title=hit.get("title") or "",
            tags=hit.get("tags") or [],
            session_id=hit.get("session_id") or session_id,
            actor=hit.get("actor") or "",
            project_id=hit.get("project_id") or "",
            event_ids=[str(e) for e in event_ids],
            enforced=False,
            decision="observed",
        )
    except Exception as exc:  # noqa: BLE001 — a scan must still print its matches
        return {"rule_id": hit.get("rule_id"), "persisted": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _fail(message: str, as_json: bool, code: int = EXIT_USAGE) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_detect",
        description=(
            "Operator CLI for AGOV declarative detection. " + NOT_PROOF
        ),
    )
    verbs = parser.add_mutually_exclusive_group(required=True)
    verbs.add_argument("--list", action="store_true", help="catalog the loaded rules")
    verbs.add_argument(
        "--check", action="store_true",
        help="validate a rule directory; exits non-zero on any invalid rule",
    )
    verbs.add_argument(
        "--test", action="store_true",
        help="evaluate the rules against the fixture events",
    )
    verbs.add_argument(
        "--scan", action="store_true",
        help="evaluate the rules against stored events for one session",
    )
    parser.add_argument(
        "--rules-dir",
        help="rule directory (default: args/agent_rules, or ICDEV_AGENT_RULES_DIR)",
    )
    parser.add_argument("--session", help="session id, required by --scan")
    parser.add_argument(
        "--fixtures",
        help=f"fixture file or directory (default: {FIXTURES_DIRNAME.as_posix()})",
    )
    parser.add_argument(
        "--limit", type=int, default=events_mod.DEFAULT_LIMIT,
        help="per-source row cap for --scan",
    )
    parser.add_argument(
        "--record", action="store_true",
        help="--scan only: append matches to agent_findings (off by default)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        return cmd_list(args)
    if args.check:
        return cmd_check(args)
    if args.test:
        return cmd_test(args)
    return cmd_scan(args)


if __name__ == "__main__":
    sys.exit(main())
