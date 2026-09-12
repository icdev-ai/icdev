#!/usr/bin/env python3
# CUI // SP-CTI
"""NOVA Skill Generator — auto-generate skill specs from session patterns (adapt-hermes-04).

Reads repeated command/tool patterns from session history (via session_indexer FTS5),
identifies high-frequency patterns that suggest a reusable skill is missing, and
generates an ICDEV™ skill specification for review.  Generated specs are queued in
agent_improvement_artifacts for Continuous Harness evaluation (SELA pipeline).

Every surfaced pattern carries a ``confidence`` in [0.0, 1.0] (xrv-shield-02) —
a declared function of how often it was seen, across how many DISTINCT sessions,
how recently, and whether a prompt-injection scan came back clean. The formula
lives in exactly one function, :func:`pattern_confidence`, with its weights in
``args/nova_config.yaml``; ``confidence`` is ``None`` (never 0.0) when the
evidence for a pattern could not be measured at all.

Usage:
    python tools/nova/skill_generator.py --analyze --json
    python tools/nova/skill_generator.py --generate "database migration" --json
    python tools/nova/skill_generator.py --list-queued --json
    python tools/nova/skill_generator.py --generate "pytest tests/" --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Patterns to detect in session turn content
_COMMAND_PATTERNS = [
    (r"python tools/\w+/\w+\.py(?:\s+--\S+)*", "tool_invocation"),
    (r"pytest tests/test_\w+\.py", "test_run"),
    (r"icdev \w[\w-]*", "cli_command"),
    (r"from (?:tools|icdev\.tools)\.\w+\.\w+ import \w+", "import_pattern"),
    (r"python -m tools\.\w+(?:\.\w+)?", "module_run"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gen_id(prefix: str = "sg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ph(conn) -> str:
    """Return the correct SQL placeholder for the active connection."""
    try:
        conn.execute("SELECT sqlite_version()")
        return "?"
    except Exception:
        return "%s"


# ---------------------------------------------------------------------------
# Confidence (xrv-shield-02)
#
# `analyze_patterns` used to return a raw frequency behind a `min_count=2` floor
# and nothing else, and `skills_lifecycle.maybe_propose_from_session` queued a
# NOVA proposal with nothing to refuse on. The precedent already in the tree is
# `tools/registry/learning_collector.py`: `child_learned_behaviors.confidence`
# is `REAL CHECK(0..1)`, clamped, with an injection scan that BLOCKS at 0.7 and
# DEMOTES at 0.5. That is the shape reproduced here.
# ---------------------------------------------------------------------------

#: `confidence` is None — NEVER 0.0, which is a real verdict meaning "measured,
#: and worthless". Nothing was measured is its own answer.
CONFIDENCE_UNMEASURABLE = "unmeasurable"

try:  # the detector is a layer, not a dependency (learning_collector's idiom)
    from tools.security.prompt_injection_detector import PromptInjectionDetector

    _pid: Any = PromptInjectionDetector()
except Exception:  # noqa: BLE001
    _pid = None


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_confidence_config() -> dict:
    """Return the declared scoring config from ``args/nova_config.yaml``.

    Read on every call rather than cached at import: a weight edit must not need
    a process restart to take effect, and the file is a few hundred bytes. Every
    default below is the value that file ships, so a deployment that lost the
    file scores identically instead of silently scoring nothing.
    """
    cfg = _load_yaml(BASE_DIR / "args" / "nova_config.yaml")
    pc = dict(cfg.get("pattern_confidence") or {})
    pc.setdefault("weights", {"frequency": 0.40, "distinct_sessions": 0.35, "recency": 0.25})
    pc.setdefault("frequency_saturation_count", 8)
    pc.setdefault("distinct_sessions_saturation", 4)
    pc.setdefault("single_session_ceiling", 0.65)
    pc.setdefault("unmeasured_session_credit", 0.0)
    pc.setdefault("recency", {"half_life_key": "session", "min_decay_factor": 0.01})
    pc.setdefault(
        "injection_scan",
        {"block_confidence": 0.7, "demote_confidence": 0.5, "demote_ceiling": 0.5},
    )
    pc["propose_min_confidence"] = float(
        (cfg.get("propose") or {}).get("min_confidence", 0.7)
    )
    return pc


def propose_min_confidence() -> float:
    """The bar `maybe_propose_from_session` refuses below (default 0.7)."""
    return load_confidence_config()["propose_min_confidence"]


def _half_life_days(key: str, default: float = 30.0) -> float:
    """Half-life in days for `key`, from ``args/memory_config.yaml``.

    The half-lives are declared ONCE, in the memory system's own config; this
    reads that table rather than restating a number beside it. A key the table
    does not carry falls through to that file's declared `default_half_life` —
    `session` is deliberately such a key (see args/nova_config.yaml).
    """
    decay = _load_yaml(BASE_DIR / "args" / "memory_config.yaml").get("time_decay") or {}
    half_lives = decay.get("half_lives") or {}
    try:
        return float(half_lives.get(key, decay.get("default_half_life", default)))
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> "datetime | None":
    """Parse a memory_entries timestamp; None when it cannot be read.

    `memory_entries.created_at` is TEXT and carries BOTH spellings on this
    platform — `datetime('now')`'s space form and an ISO `T` form — so both are
    accepted. A value we cannot parse is None, never "now": defaulting to now
    would score an undateable row as maximally fresh.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def scan_for_injection(text: str) -> dict:
    """Scan `text` for prompt injection; the detector's own confidence, carried.

    `scanned: False` means the detector was UNAVAILABLE — never "came back
    clean". The two justify opposite readings and are never merged, so
    `detected` is None rather than False in that case.
    """
    if _pid is None or not text:
        return {"scanned": False, "detected": None, "confidence": None}
    try:
        result = _pid.scan_text(text, source="nova_pattern")
        return {
            "scanned": True,
            "detected": bool(result.get("detected")),
            "confidence": result.get("confidence"),
        }
    except Exception:  # noqa: BLE001
        return {"scanned": False, "detected": None, "confidence": None}


def pattern_confidence(
    *,
    count,
    distinct_sessions,
    last_seen=None,
    injection: "dict | None" = None,
    now: "datetime | None" = None,
    config: "dict | None" = None,
) -> dict:
    """THE formula. Score a learned pattern in [0.0, 1.0].

    This is the single place the score is computed — `analyze_patterns`, the
    `nova_analyze_patterns` MCP tool and
    `skills_lifecycle.maybe_propose_from_session` all reach it, and a second
    copy anywhere is a defect. Weights are declared in
    ``args/nova_config.yaml``.

    Three terms, each measuring something the others cannot:

    * **frequency** — occurrences, saturating at `frequency_saturation_count`.
      Saturating rather than unbounded because 400 hits in ONE session are not a
      hundred times the evidence of four hits across four sessions.
    * **distinct_sessions** — how many DIFFERENT sessions produced it.
      REPETITION IS NOT CORROBORATION; this term is the one that says so.
      `None` (attribution not measurable) scores `unmeasured_session_credit`,
      which ships at 0.0, and the result's `basis` says the term was not
      measured — so a measured 0 and an unmeasured one never read alike. A
      pattern MEASURED in exactly one session is additionally capped at
      `single_session_ceiling`, below the propose bar: forty hits in one session
      are one observation, not forty.
    * **recency** — ``2 ** -(age_days / half_life)``, the memory system's own
      decay curve read from ``args/memory_config.yaml``, floored at
      `min_decay_factor`. An undateable occurrence takes the FLOOR, not 1.0: an
      unknown age must never read as fresh.

    Then the injection rule, `learning_collector`'s verbatim: a hit at or above
    `block_confidence` REJECTS (0.0); a hit at or above `demote_confidence` CAPS
    at `demote_ceiling` (0.5). A detector that did not RUN never demotes — an
    unavailable scanner is not a clean bill of health, and says so on
    `injection_scanned` instead.

    Returns:
        ``{confidence, basis, terms, injection_scanned, injection_detected}``.
        ``confidence`` is **None**, never 0.0, when `count` is missing or
        non-positive: nothing was measured, and 0.0 there would read as a
        pattern that WAS measured and found worthless.
    """
    cfg = config or load_confidence_config()
    weights = cfg["weights"]
    w_f = float(weights.get("frequency", 0.0))
    w_s = float(weights.get("distinct_sessions", 0.0))
    w_r = float(weights.get("recency", 0.0))
    total_w = w_f + w_s + w_r
    if abs(total_w - 1.0) > 1e-6:
        # A silently renormalised weight set is a formula nobody declared.
        raise ValueError(
            "nova_config pattern_confidence.weights must sum to 1.0, got %s" % total_w
        )

    try:
        n = int(count) if count is not None else 0
    except (TypeError, ValueError):
        n = 0
    inj = injection or {"scanned": False, "detected": None, "confidence": None}
    if n <= 0:
        return {
            "confidence": None,
            "basis": CONFIDENCE_UNMEASURABLE,
            "terms": {"frequency": None, "distinct_sessions": None, "recency": None},
            "injection_scanned": bool(inj.get("scanned")),
            "injection_detected": inj.get("detected"),
        }

    freq_sat = max(1, int(cfg.get("frequency_saturation_count", 8)))
    freq_term = min(1.0, n / freq_sat)

    sess_sat = max(1, int(cfg.get("distinct_sessions_saturation", 4)))
    sessions_measured = distinct_sessions is not None
    if sessions_measured:
        sess_term = min(1.0, max(0, int(distinct_sessions)) / sess_sat)
    else:
        sess_term = float(cfg.get("unmeasured_session_credit", 0.0))

    rec_cfg = cfg.get("recency") or {}
    floor = float(rec_cfg.get("min_decay_factor", 0.01))
    seen = _parse_ts(last_seen)
    recency_measured = seen is not None
    if recency_measured:
        half_life = _half_life_days(str(rec_cfg.get("half_life_key", "session")))
        age_days = max(
            0.0,
            ((now or datetime.now(timezone.utc)) - seen).total_seconds() / 86400.0,
        )
        rec_term = max(
            floor, math.pow(2.0, -(age_days / half_life)) if half_life > 0 else floor
        )
    else:
        rec_term = floor

    score = max(0.0, min(1.0, w_f * freq_term + w_s * sess_term + w_r * rec_term))

    # REPETITION IS NOT CORROBORATION. One session that ran a command forty
    # times is one observation, not forty, so a MEASURED single-session pattern
    # is capped below the propose bar however often it recurred. Not applied to
    # an UNMEASURED session count — that is not known to be one session.
    single_session = sessions_measured and int(distinct_sessions) <= 1
    if single_session:
        score = min(score, float(cfg.get("single_session_ceiling", 0.65)))

    unmeasured = []
    if not sessions_measured:
        unmeasured.append("sessions")
    if not recency_measured:
        unmeasured.append("recency")
    basis = "measured" if not unmeasured else "measured_without_" + "_".join(unmeasured)
    if single_session:
        basis = "single_session_capped" if score >= float(
            cfg.get("single_session_ceiling", 0.65)
        ) else basis

    inj_cfg = cfg.get("injection_scan") or {}
    if inj.get("scanned") and inj.get("detected"):
        hit = float(inj.get("confidence") or 0.0)
        if hit >= float(inj_cfg.get("block_confidence", 0.7)):
            score = 0.0
            basis = "injection_blocked"
        elif hit >= float(inj_cfg.get("demote_confidence", 0.5)):
            score = min(score, float(inj_cfg.get("demote_ceiling", 0.5)))
            basis = "injection_demoted"

    return {
        "confidence": round(score, 4),
        "basis": basis,
        "terms": {
            "frequency": round(freq_term, 4),
            "distinct_sessions": round(sess_term, 4) if sessions_measured else None,
            "recency": round(rec_term, 4) if recency_measured else None,
        },
        "injection_scanned": bool(inj.get("scanned")),
        "injection_detected": inj.get("detected"),
    }


# ---------------------------------------------------------------------------
# Pattern analysis
# ---------------------------------------------------------------------------

def _session_ref_available(conn) -> bool:
    """Does the live memory_entries table carry `session_ref`?

    Asked of the table, never assumed. `session_ref` is migration 226's column
    and the SQLite init path does not declare it, so a SELECT naming it on an
    un-migrated database raises — and this module's broad `except` would turn
    that into an EMPTY pattern list, i.e. "no patterns" for a database full of
    them. Probed instead, so an absent column degrades to
    `distinct_sessions: None` (unmeasurable) rather than to silence.
    """
    try:
        conn.execute("SELECT session_ref FROM memory_entries LIMIT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()  # PG aborts the transaction on a failed probe
        except Exception:  # noqa: BLE001
            pass
        return False


def analyze_patterns(
    limit: int = 50,
    min_count: int = 2,
    *,
    scan_injection: bool = True,
) -> list[dict]:
    """Scan session history for repeated command patterns, each with a confidence.

    Reads memory_entries rows with type LIKE 'session_%' and applies regex
    matching to surface high-frequency command patterns that suggest a
    reusable ICDEV™ skill might be worth generating.

    Each result carries a ``confidence`` in [0.0, 1.0] from
    :func:`pattern_confidence` — a raw frequency is not evidence that a pattern
    is worth a skill, because one chatty session can produce any count it likes.
    The distinguishing evidence is gathered here and NOWHERE ELSE:
    ``sessions`` (distinct ``memory_entries.session_ref`` values, or **None**
    when that column is absent or unpopulated — never 0, which would read as a
    measured absence) and ``last_seen`` (the newest occurrence).

    Args:
        limit: Max patterns to return.
        min_count: Minimum occurrences to surface.
        scan_injection: Run the prompt-injection detector over each pattern.
            The scan is the one part of scoring that costs per pattern; off, the
            result reports ``injection_scanned: false`` rather than a clean scan.

    Returns:
        List of ``{pattern, count, category, example, sessions, last_seen,
        confidence, confidence_basis, confidence_terms, injection_scanned,
        injection_detected}`` dicts, sorted by confidence then count, both desc.
        ``confidence`` is None (never 0.0) for a pattern nothing could be
        measured about.
    """
    conn = _get_conn()
    pattern_counts: dict[str, dict] = {}
    ph = _ph(conn)

    try:
        has_session_ref = _session_ref_available(conn)
        session_col = "session_ref" if has_session_ref else "NULL AS session_ref"
        rows = conn.execute(
            f"""SELECT content, created_at, {session_col} FROM memory_entries
                WHERE type LIKE {ph}
                ORDER BY created_at DESC
                LIMIT {ph}""",
            ("session_%", limit * 10),
        ).fetchall()

        for row in rows:
            if isinstance(row, (list, tuple)):
                content, created_at, session_ref = row[0], row[1], row[2]
            else:
                content = row["content"]
                created_at = row["created_at"]
                session_ref = row["session_ref"]
            if not content:
                continue
            for regex, category in _COMMAND_PATTERNS:
                for match in re.finditer(regex, content):
                    key = match.group(0)[:80]
                    entry = pattern_counts.get(key)
                    if entry is None:
                        entry = {
                            "pattern": key,
                            "count": 0,
                            "category": category,
                            "example": content[:120],
                            "_sessions": set(),
                            "_last_seen": None,
                        }
                        pattern_counts[key] = entry
                    entry["count"] += 1
                    if session_ref:
                        entry["_sessions"].add(str(session_ref))
                    seen = _parse_ts(created_at)
                    if seen is not None and (
                        entry["_last_seen"] is None or seen > entry["_last_seen"]
                    ):
                        entry["_last_seen"] = seen

    except Exception:
        pass
    finally:
        conn.close()

    cfg = load_confidence_config()
    now = datetime.now(timezone.utc)
    results: list[dict] = []
    for entry in pattern_counts.values():
        if entry["count"] < min_count:
            continue
        sessions = entry.pop("_sessions")
        last_seen = entry.pop("_last_seen")
        # None, never 0: a pattern with no attributable session is unmeasured,
        # not measured-and-attributed-to-nothing.
        distinct_sessions = len(sessions) if sessions else None
        injection = scan_for_injection(entry["pattern"]) if scan_injection else {
            "scanned": False,
            "detected": None,
            "confidence": None,
        }
        scored = pattern_confidence(
            count=entry["count"],
            distinct_sessions=distinct_sessions,
            last_seen=last_seen,
            injection=injection,
            now=now,
            config=cfg,
        )
        entry.update(
            {
                "sessions": distinct_sessions,
                "last_seen": last_seen.isoformat(timespec="seconds") if last_seen else None,
                "confidence": scored["confidence"],
                "confidence_basis": scored["basis"],
                "confidence_terms": scored["terms"],
                "injection_scanned": scored["injection_scanned"],
                "injection_detected": scored["injection_detected"],
            }
        )
        results.append(entry)

    # Confidence first, count as the tie-break: the whole point of the score is
    # that it outranks a raw frequency.
    results.sort(key=lambda x: (x["confidence"] or 0.0, x["count"]), reverse=True)
    return results[:limit]


def score_candidate(pattern: str, *, limit: int = 200) -> dict:
    """Score ONE candidate pattern against the evidence history holds for it.

    The screening seam `skills_lifecycle.maybe_propose_from_session` calls. It
    reuses :func:`analyze_patterns` rather than issuing its own query, so the
    evidence behind a refusal is the same evidence `--analyze` prints — a second
    gatherer is how two surfaces come to disagree about one pattern.

    A candidate history holds NO evidence for scores ``confidence: None``
    (``basis: unmeasurable``). That is a refusal, not an approval: nothing
    corroborates it.
    """
    candidate = (pattern or "").strip()
    if not candidate:
        return {
            "pattern": "",
            "count": 0,
            "sessions": None,
            "confidence": None,
            "basis": CONFIDENCE_UNMEASURABLE,
        }
    # min_count=1: the screen must be able to SEE a once-seen pattern in order
    # to refuse it. A floor here would report it as unmeasurable instead.
    #
    # MATCHING IS IN BOTH DIRECTIONS, and it has to be: the candidate is a
    # session TITLE while a scored pattern is what `_COMMAND_PATTERNS` captured
    # out of a turn, so `python tools/x/y.py --flag arg` as a title carries the
    # pattern `python tools/x/y.py --flag` INSIDE it. An exact match wins
    # outright; otherwise the LONGEST containing/contained pattern is taken —
    # the most specific evidence, and a deterministic pick rather than
    # whichever happened to sort first.
    best = None
    for scored in analyze_patterns(limit=limit, min_count=1):
        found = scored["pattern"]
        if found == candidate:
            best = scored
            break
        if found in candidate or candidate in found:
            if best is None or len(found) > len(best["pattern"]):
                best = scored
    if best is not None:
        return {
            "pattern": best["pattern"],
            "count": best["count"],
            "sessions": best["sessions"],
            "last_seen": best.get("last_seen"),
            "confidence": best["confidence"],
            "basis": best["confidence_basis"],
            "terms": best.get("confidence_terms"),
        }
    scored = pattern_confidence(
        count=0,
        distinct_sessions=None,
        injection=scan_for_injection(candidate),
    )
    return {
        "pattern": candidate,
        "count": 0,
        "sessions": None,
        "last_seen": None,
        "confidence": scored["confidence"],
        "basis": scored["basis"],
        "terms": scored["terms"],
    }


# ---------------------------------------------------------------------------
# Skill spec generation
# ---------------------------------------------------------------------------

def generate_skill_spec(
    pattern: str,
    category: str = "general",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate an ICDEV™ skill spec markdown for a given pattern.

    Uses scanner-tier LLM when available; falls back to a structured template.
    Queues the result in agent_improvement_artifacts unless dry_run=True.

    Returns:
        {skill_id, skill_name, pattern, category, spec_preview, queued, dry_run}
    """
    skill_id = _gen_id("sg")
    slug = re.sub(r"[^a-z0-9]+", "-", pattern.lower())[:40].strip("-")
    skill_name = f"icdev-{slug}"

    spec_text = _llm_generate_spec(pattern, skill_name)

    result: dict[str, Any] = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "pattern": pattern,
        "category": category,
        "spec_preview": spec_text[:300],
        "queued": False,
        "dry_run": dry_run,
    }

    if not dry_run:
        result["queued"] = _queue_for_harness(skill_id, skill_name, spec_text, pattern)

    return result


# exa-refine-02: this was an f-string literal inside _llm_generate_spec. The text
# is unchanged; the two interpolations became named placeholders so the template
# can be versioned in the prompt registry under SPEC_PROMPT_NAME. Only the LLM
# PROMPT is registered — the deterministic fallback spec below it stays a module
# literal, because it is the output used when the LLM is unreachable and must not
# depend on the database being reachable either.
SPEC_PROMPT_NAME = "call_site/nova_skill_spec"
SPEC_PROMPT_TEMPLATE = (
    "Generate a concise ICDEV™ skill specification in markdown for automating:\n\n"
    "Pattern: {pattern}\n\n"
    "Format:\n"
    "# {skill_name}\n"
    "## When to use\n[1-2 sentences]\n\n"
    "## Steps\n1. ...\n\n"
    "## Acceptance criteria\n- [ ] ...\n"
)


def _build_spec_prompt(pattern: str, skill_name: str) -> str:
    """Render the skill-spec prompt: active registry version, else the module default."""
    from tools.llm.prompt_registry import render_prompt

    return render_prompt(
        SPEC_PROMPT_NAME,
        SPEC_PROMPT_TEMPLATE,
        pattern=pattern,
        skill_name=skill_name,
    )


def _llm_generate_spec(pattern: str, skill_name: str) -> str:
    """Generate skill spec via scanner-tier LLM; falls back to template."""
    prompt = _build_spec_prompt(pattern, skill_name)
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        response = router.invoke("memory_consolidation", request)
        if response and response.content and response.content.strip():
            return response.content.strip()
    except Exception:
        pass

    # Fallback template
    return (
        f"# {skill_name}\n\n"
        f"## When to use\n"
        f"When you need to automate: `{pattern}`\n\n"
        f"## Steps\n"
        f"1. Identify input parameters\n"
        f"2. Execute the pattern\n"
        f"3. Verify output matches expected format\n\n"
        f"## Acceptance criteria\n"
        f"- [ ] Pattern executes without error\n"
        f"- [ ] Output is valid JSON when `--json` flag supplied\n"
        f"- [ ] Audit trail entry written on completion\n"
    )


def _queue_for_harness(
    skill_id: str, skill_name: str, spec_text: str, source_pattern: str
) -> bool:
    """Insert generated spec into agent_improvement_artifacts for SELA evaluation."""
    conn = _get_conn()
    ph = _ph(conn)
    try:
        evidence = json.dumps({"source_pattern": source_pattern, "generator": "hermes_skill_generator"})
        conn.execute(
            f"""INSERT INTO agent_improvement_artifacts
                (artifact_id, task_type, skill_used, generation_n,
                 improvement_text, composite_score, baseline_score,
                 evidence_traces, applied_count, status, created_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
            (
                skill_id,
                "skill_generation",
                skill_name,
                1,
                spec_text,
                0.0,
                0.0,
                evidence,
                0,
                "pending",
                _now_iso(),
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# List queued specs
# ---------------------------------------------------------------------------

def list_queued(limit: int = 20) -> list[dict]:
    """Return queued skill specs from agent_improvement_artifacts."""
    conn = _get_conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"""SELECT artifact_id, skill_used, composite_score, status, created_at
                FROM agent_improvement_artifacts
                WHERE task_type = {ph}
                ORDER BY created_at DESC
                LIMIT {ph}""",
            ("skill_generation", limit),
        ).fetchall()

        results = []
        for row in rows:
            if hasattr(row, "keys"):
                results.append(dict(row))
            else:
                results.append({
                    "artifact_id": row[0],
                    "skill_used": row[1],
                    "composite_score": row[2],
                    "status": row[3],
                    "created_at": str(row[4]) if row[4] else None,
                })
        return results
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="NOVA Skill Generator — Hermes adapt-hermes-04"
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Analyze session history for recurring command patterns",
    )
    parser.add_argument(
        "--generate", metavar="PATTERN",
        help="Generate a skill spec for the given pattern string",
    )
    parser.add_argument(
        "--list-queued", action="store_true",
        help="List queued skill specs awaiting Continuous Harness evaluation",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument(
        "--min-count", type=int, default=2,
        help="Minimum pattern occurrences to surface (default: 2)",
    )
    parser.add_argument("--category", default="general", help="Category for --generate")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate spec without queuing to DB",
    )
    parser.add_argument(
        "--no-injection-scan", action="store_true",
        help="Skip the prompt-injection scan; patterns report injection_scanned=false "
             "(NOT a clean scan)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.analyze:
        patterns = analyze_patterns(
            limit=args.limit,
            min_count=args.min_count,
            scan_injection=not args.no_injection_scan,
        )
        scored = [p for p in patterns if p["confidence"] is not None]
        threshold = propose_min_confidence()
        if args.as_json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "count": len(patterns),
                # None, never 0.0, when nothing was scored — a mean over an
                # empty denominator is not a measurement (args/perfect_score_gate.yaml).
                "mean_confidence": (
                    round(sum(p["confidence"] for p in scored) / len(scored), 4)
                    if scored else None
                ),
                "unmeasurable": len(patterns) - len(scored),
                "propose_min_confidence": threshold,
                "above_propose_threshold": sum(
                    1 for p in scored if p["confidence"] >= threshold
                ) if scored else None,
                "patterns": patterns,
            }))
        else:
            if not patterns:
                print("No patterns surfaced. UNMEASURABLE, not clean — session "
                      "history holds no rows matching type LIKE 'session_%'.")
            for p in patterns:
                conf = "unmeasurable" if p["confidence"] is None else f"{p['confidence']:.2f}"
                sess = "?" if p["sessions"] is None else p["sessions"]
                print(f"[conf {conf}] [{p['count']}x / {sess} sessions] "
                      f"[{p['category']}] {p['pattern']}  ({p['confidence_basis']})")

    elif args.generate:
        result = generate_skill_spec(
            args.generate, category=args.category, dry_run=args.dry_run
        )
        if args.as_json:
            print(json.dumps({"classification": "CUI // SP-CTI", **result}))
        else:
            print(f"Skill   : {result['skill_name']} (id: {result['skill_id']})")
            print(f"Queued  : {result['queued']}")
            print(f"Preview : {result['spec_preview'][:120]}")

    elif args.list_queued:
        queued = list_queued(limit=args.limit)
        if args.as_json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "count": len(queued),
                "queued": queued,
            }))
        else:
            for q in queued:
                name = q.get("skill_used", "unknown")
                aid = q.get("artifact_id", "?")
                status = q.get("status", "?")
                ts = q.get("created_at", "?")
                print(f"[{status}] {name} ({aid}) @ {ts}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
