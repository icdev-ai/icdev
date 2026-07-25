# CUI // SP-CTI
"""Divergence critic — the Focus half of divergent ideation (dvg-critic-01).

invoke_divergence() (the Generate half) produces a raw pool of candidate ideas
under a strict no-evaluation contract. This module is the SEPARATE, opposing
invocation that scores that pool. The generator/critic split is deliberately
mechanical: the critic runs its own LLM call with a harsh-evaluator system
prompt, never a continuation of the generator's context.

Deterministic-picker discipline (agx-pick-02): the LLM emits only CATEGORICAL
judgments — one 3-value enum per dimension (novelty / viability / fit) plus a
written rationale. Python composes the numeric composite AND the final ordering
(tools.quality.categorical_scoring.compose_divergence). We never ask the model
for a 0-1 number or a ranked list; that arithmetic is identical across model
families and unit-tested off a truth table.

Why this is genuinely new: tools/creative/gap_scorer.py scores its dimensions
against a DB of already-known items (_score_gap_uniqueness queries existing
rows), so it structurally cannot score an idea that has no DB row yet. Every
divergence idea is, by construction, not in the database.

Scores persist to divergence_idea_scores keyed by the run trace_id, so a run is
auditable after the fact. Degrades cleanly: if the critic LLM is unavailable or
returns unparseable output, returns an unscored result with a reason rather than
raising — callers treat it like any other unavailable-LLM case.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
from tools.quality.categorical_scoring import (
    DIVERGENCE_DIMENSIONS,
    DIVERGENCE_VOCAB,
    TRAP_VOCAB,
    compose_divergence,
    compose_trap,
)

logger = get_logger("icdev.quality.divergence_critic")

_CUI_BANNER = (
    "// CLASSIFICATION: CUI // SP-CTI // DISTRIBUTION: ICDEV INTERNAL USE ONLY\n"
    "// Unclassified when removed from ICDEV™ system context\n"
)


@dataclass
class IdeaScore:
    """One scored idea: the categorical enums, composed floats, and rationale."""

    index: int
    idea: str
    frame: str
    novelty: str
    viability: str
    fit: str
    composite: float
    dimension_floats: dict[str, float]
    rationale: str
    vocabulary_version: str
    # Trap detection (dvg-critic-02): seductive-but-broken flag + mandatory why.
    trap_flag: str = "clear"
    trap_level: float = 0.0
    is_trap: bool = False
    trap_rationale: str = ""
    # Clustering (dvg-critic-03): short label naming the underlying approach, so
    # restatements of one idea collapse into one cluster instead of N ideas.
    cluster_label: str = ""


@dataclass
class ScoredPool:
    """Result of scoring an idea pool. ``ordered`` is composite-descending —
    the ordering is composed in Python, never requested from the model."""

    trace_id: str
    function: str
    ordered: list[IdeaScore] = field(default_factory=list)
    stop_reason: str = "completed"
    model_id: str = ""
    total_cost_usd: float = 0.0
    persisted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "function": self.function,
            "stop_reason": self.stop_reason,
            "model_id": self.model_id,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "persisted": self.persisted,
            "ideas": [
                {
                    "index": s.index,
                    "frame": s.frame,
                    "idea": s.idea,
                    "novelty": s.novelty,
                    "viability": s.viability,
                    "fit": s.fit,
                    "composite": s.composite,
                    "rationale": s.rationale,
                    "trap_flag": s.trap_flag,
                    "is_trap": s.is_trap,
                    "trap_rationale": s.trap_rationale,
                    "cluster_label": s.cluster_label,
                }
                for s in self.ordered
            ],
        }

    def trap_warnings(self) -> list[dict[str, Any]]:
        """Advisory trap warnings for the creative/innovation triage decision
        point — ADVISORY input to existing gates, NOT an autonomous blocker
        (until dvg-bench-01 has measured trap detection on our stack).

        Only ACTIONABLE traps appear: a trap flag with a written rationale. An
        unexplained flag is deliberately excluded (it cannot be reviewed). The
        shape mirrors a triage warning so a gate can fold these into its existing
        warnings list without blocking.
        """
        return [
            {
                "kind": "divergence_trap",
                "severity": "warning",  # advisory only — never 'block'
                "idea_index": s.index,
                "frame": s.frame,
                "idea": s.idea,
                "trap_flag": s.trap_flag,
                "rationale": s.trap_rationale,
                "composite": s.composite,
                "trace_id": self.trace_id,
            }
            for s in self.ordered
            if s.is_trap
        ]


# ---------------------------------------------------------------------------
# Pool parsing
# ---------------------------------------------------------------------------
_FRAME_HEADER = re.compile(r"^##\s*Frame:\s*(.+?)\s*$", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")


def parse_idea_pool(pool_text: str) -> list[dict[str, str]]:
    """Parse the labeled idea-pool text from invoke_divergence into a flat list
    of ``{frame, idea}`` dicts, one per candidate.

    The pool is organized as ``## Frame: <name>`` sections, each containing
    numbered candidate ideas. Robust to blank lines and un-numbered prose: if a
    frame section has no numbered items, the whole section body is taken as a
    single idea so nothing is silently dropped.
    """
    ideas: list[dict[str, str]] = []
    if not pool_text:
        return ideas

    # Split on frame headers, keeping the frame name with its body.
    matches = list(_FRAME_HEADER.finditer(pool_text))
    if not matches:
        # No frame headers — treat numbered lines (or the whole text) as ideas.
        sections = [("(unframed)", pool_text)]
    else:
        sections = []
        for i, m in enumerate(matches):
            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(pool_text)
            sections.append((m.group(1).strip(), pool_text[body_start:body_end]))

    for frame, body in sections:
        numbered = []
        for line in body.splitlines():
            nm = _NUMBERED.match(line)
            if nm and nm.group(2).strip():
                numbered.append(nm.group(2).strip())
        if numbered:
            for idea in numbered:
                ideas.append({"frame": frame, "idea": idea})
        else:
            cleaned = " ".join(
                ln.strip() for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("[")
            ).strip()
            if cleaned:
                ideas.append({"frame": frame, "idea": cleaned})
    return ideas


# ---------------------------------------------------------------------------
# Critic prompt (opposing the generator's no-evaluation system prompt)
# ---------------------------------------------------------------------------
def build_critic_prompt(ideas: list[dict[str, str]]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the critic call.

    The system prompt is the mechanical OPPOSITE of the generator's: where the
    branch was told to widen and never evaluate, the critic is told to evaluate
    hard and emit ONLY the fixed enums. Emitting closed enums (not free-form
    numbers) is what makes the score portable across model families.
    """
    novelty_opts = "/".join(DIVERGENCE_VOCAB["novelty"])
    viability_opts = "/".join(DIVERGENCE_VOCAB["viability"])
    fit_opts = "/".join(DIVERGENCE_VOCAB["fit"])
    trap_opts = "/".join(TRAP_VOCAB)

    sys = _CUI_BANNER + (
        "You are a hard, fair critic scoring a pool of candidate ideas produced by "
        "a separate generative pass. Judge each idea on its merits. Do NOT generate "
        "new ideas, do NOT soften, do NOT rank — you assign categorical labels only; "
        "a downstream deterministic composer does the ranking.\n\n"
        "Score EACH idea on exactly three dimensions, choosing ONE token per "
        "dimension from the fixed vocabularies:\n"
        f"  novelty:   {novelty_opts}\n"
        f"  viability: {viability_opts}\n"
        f"  fit:       {fit_opts}\n\n"
        "Then judge whether the idea is a TRAP — seductive-but-broken: it looks "
        "attractive but fails for a reason that is NOT obvious up front. Choose one "
        f"trap token: {trap_opts}. If you flag 'trap' or 'suspected_trap' you MUST "
        "give a specific trap_rationale naming the non-obvious failure. An "
        "unexplained trap flag is worthless and will be discarded.\n\n"
        "For every idea also give a one-sentence rationale grounding the three "
        "score labels, and a short 'cluster' label (2-4 words) naming the "
        "UNDERLYING APPROACH — ideas that are restatements of the same approach "
        "MUST share an identical cluster label so they collapse into one group. "
        "Return STRICT JSON only."
    )

    listing = "\n".join(
        f'{i}. [frame: {d["frame"]}] {d["idea"]}' for i, d in enumerate(ideas)
    )
    usr = (
        "[IDEA POOL]\n" + listing + "\n\n"
        "[OUTPUT] Return a JSON object of the form:\n"
        '{"scores": [{"index": <int>, "novelty": "<token>", "viability": "<token>", '
        '"fit": "<token>", "rationale": "<one sentence>", "trap": "<token>", '
        '"trap_rationale": "<why it is a trap, or empty if clear>", '
        '"cluster": "<2-4 word approach label>"}, ...]}\n'
        "One entry per idea, index matching the list above. JSON only — no prose."
    )
    return sys, usr


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_critic_json(content: str) -> list[dict[str, Any]]:
    """Extract the scores array from the critic's response, tolerant of code
    fences and surrounding prose. Returns [] if nothing parseable is found."""
    if not content:
        return []
    text = content.strip()
    # Strip common code-fence wrappers.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    candidates = [text]
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("scores"), list):
            return obj["scores"]
        if isinstance(obj, list):
            return obj
    return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_idea_pool(
    pool_text: str,
    *,
    function: str,
    trace_id: Optional[str] = None,
    router: Any = None,
    persist: bool = True,
    tenant_id: str = "default",
    classification: str = "CUI",
) -> ScoredPool:
    """Score a raw divergence idea pool. Separate LLM call, categorical output,
    Python-composed ordering, persisted by trace_id.

    Args:
        pool_text: the ``content`` from invoke_divergence (labeled idea pool).
        function: ICDEV function name (routing + audit key).
        trace_id: the divergence run's trace id; a fresh uuid if omitted.
        router: an LLMRouter-like object with ``invoke(function, request)``.
            Injected for tests; constructed lazily otherwise.
        persist: write scores to divergence_idea_scores (best-effort).

    Returns a ScoredPool; never raises on LLM/parse/DB failure.
    """
    trace_id = trace_id or str(uuid.uuid4())
    ideas = parse_idea_pool(pool_text)
    result = ScoredPool(trace_id=trace_id, function=function)

    if not ideas:
        result.stop_reason = "empty_pool"
        return result

    try:
        from tools.llm.provider import LLMRequest

        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()

        sys_prompt, usr_prompt = build_critic_prompt(ideas)
        request = LLMRequest(
            messages=[{"role": "user", "content": usr_prompt}],
            system_prompt=sys_prompt,
        )
        response = router.invoke(function, request)
        content = getattr(response, "content", "") or ""
        result.model_id = getattr(response, "model_id", "") or ""
    except Exception as exc:  # noqa: BLE001 — degrade cleanly, never raise to caller
        logger.warning("Divergence critic LLM call failed for '%s': %s", function, exc)
        result.stop_reason = "critic_unavailable"
        return result

    raw_scores = _parse_critic_json(content)
    if not raw_scores:
        result.stop_reason = "unparseable_critic_output"
        return result

    by_index = {}
    for entry in raw_scores:
        if isinstance(entry, dict) and isinstance(entry.get("index"), int):
            by_index[entry["index"]] = entry

    scored: list[IdeaScore] = []
    for i, d in enumerate(ideas):
        entry = by_index.get(i, {})
        novelty = str(entry.get("novelty", "")).strip().lower()
        viability = str(entry.get("viability", "")).strip().lower()
        fit = str(entry.get("fit", "")).strip().lower()
        composed = compose_divergence(novelty, viability, fit)
        trap = compose_trap(
            str(entry.get("trap", "")).strip().lower(),
            str(entry.get("trap_rationale", "")).strip(),
        )
        scored.append(
            IdeaScore(
                index=i,
                idea=d["idea"],
                frame=d["frame"],
                novelty=novelty or "unknown",
                viability=viability or "unknown",
                fit=fit or "unknown",
                composite=composed["composite"],
                dimension_floats={k: composed[k] for k in DIVERGENCE_DIMENSIONS},
                rationale=str(entry.get("rationale", "")).strip(),
                vocabulary_version=composed["vocabulary_version"],
                trap_flag=trap["trap_flag"],
                trap_level=trap["trap_level"],
                is_trap=trap["is_trap"],
                trap_rationale=trap["rationale"],
                cluster_label=str(entry.get("cluster", "")).strip(),
            )
        )

    # Ordering is composed HERE, in Python — never requested from the model.
    scored.sort(key=lambda s: s.composite, reverse=True)
    result.ordered = scored

    if persist:
        result.persisted = _persist_scores(result, tenant_id=tenant_id, classification=classification)
    return result


def _persist_scores(pool: ScoredPool, *, tenant_id: str, classification: str) -> bool:
    """Best-effort append of scores to divergence_idea_scores. Returns True on
    success. Never raises — a missing table (un-migrated checkout) is tolerated."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()
        for s in pool.ordered:
            conn.execute(
                """
                INSERT INTO divergence_idea_scores
                    (id, trace_id, function, idea_index, frame, idea_text,
                     novelty, viability, fit, composite, rationale,
                     trap_flag, trap_level, is_trap, trap_rationale,
                     vocabulary_version, tenant_id, classification, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"dis-{uuid.uuid4().hex[:12]}",
                    pool.trace_id,
                    pool.function,
                    s.index,
                    s.frame,
                    s.idea[:4000],
                    s.novelty,
                    s.viability,
                    s.fit,
                    s.composite,
                    s.rationale[:2000],
                    s.trap_flag,
                    s.trap_level,
                    1 if s.is_trap else 0,
                    s.trap_rationale[:2000],
                    s.vocabulary_version,
                    tenant_id,
                    classification,
                    now,
                ),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.debug("Divergence score persistence failed (non-blocking): %s", exc)
        return False


# ---------------------------------------------------------------------------
# Clustering + deepening (dvg-critic-03) — completes the Focus phase
# ---------------------------------------------------------------------------
DEFAULT_DEEPEN_TOP_K = 3  # deepening is the 2nd-most expensive step after fan-out


@dataclass
class DeepenedCluster:
    """A group of ideas sharing an underlying approach. The top-K clusters are
    expanded into an actionable sketch with risks + next steps; the rest carry
    only their membership + scores. Structured so dvg-wire-* callers can act on
    it without re-parsing free text."""

    label: str
    member_indices: list[int]
    members: list[dict[str, Any]]
    best_composite: float
    has_trap: bool
    deepened: bool = False
    sketch: str = ""
    risks: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "member_indices": self.member_indices,
            "members": self.members,
            "best_composite": self.best_composite,
            "has_trap": self.has_trap,
            "deepened": self.deepened,
            "sketch": self.sketch,
            "risks": self.risks,
            "next_steps": self.next_steps,
        }


def cluster_pool(pool: ScoredPool) -> list[DeepenedCluster]:
    """Group a scored pool by underlying approach, most-promising cluster first.

    Grouping key is the critic-assigned ``cluster_label`` (normalized), falling
    back to the generative frame when no label was returned — so ten restatements
    of one approach collapse into one cluster instead of reading as ten ideas.
    Pure function, no LLM/IO. A cluster's rank is its best member's composite.
    """
    groups: dict[str, list[IdeaScore]] = {}
    for s in pool.ordered:
        key = (s.cluster_label or s.frame or "unlabeled").strip().lower() or "unlabeled"
        groups.setdefault(key, []).append(s)

    clusters: list[DeepenedCluster] = []
    for _key, members in groups.items():
        members_sorted = sorted(members, key=lambda m: m.composite, reverse=True)
        label = members_sorted[0].cluster_label or members_sorted[0].frame or "unlabeled"
        clusters.append(
            DeepenedCluster(
                label=label,
                member_indices=[m.index for m in members_sorted],
                members=[
                    {
                        "index": m.index,
                        "idea": m.idea,
                        "frame": m.frame,
                        "composite": m.composite,
                        "trap_flag": m.trap_flag,
                        "is_trap": m.is_trap,
                    }
                    for m in members_sorted
                ],
                best_composite=members_sorted[0].composite,
                has_trap=any(m.is_trap for m in members_sorted),
            )
        )
    clusters.sort(key=lambda c: c.best_composite, reverse=True)
    return clusters


def _resolve_deepen_k(router: Any, k: Optional[int]) -> int:
    if isinstance(k, int) and k > 0:
        return k
    try:
        cfg = getattr(router, "_config", {}) or {}
        div = cfg.get("chain_orchestration", {}).get("divergence", {})
        val = int(div.get("deepen_top_k", DEFAULT_DEEPEN_TOP_K))
        return val if val > 0 else DEFAULT_DEEPEN_TOP_K
    except Exception:  # noqa: BLE001
        return DEFAULT_DEEPEN_TOP_K


def build_deepen_prompt(clusters: list[DeepenedCluster]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) to expand the top clusters into
    actionable sketches. Separate call from scoring — deepening only the
    survivors keeps the expensive step small."""
    sys = _CUI_BANNER + (
        "You are turning a small set of surviving candidate approaches into "
        "ACTIONABLE sketches. For each cluster give: a concrete sketch of how to "
        "pursue it, the top risks, and the first concrete next steps. Be specific "
        "and terse. Return STRICT JSON only — no prose."
    )
    listing = []
    for i, c in enumerate(clusters):
        best_idea = c.members[0]["idea"] if c.members else ""
        trap = " [TRAP-FLAGGED]" if c.has_trap else ""
        listing.append(f'{i}. cluster "{c.label}"{trap} — best idea: {best_idea}')
    usr = (
        "[SURVIVING CLUSTERS]\n" + "\n".join(listing) + "\n\n"
        "[OUTPUT] Return JSON:\n"
        '{"clusters": [{"index": <int>, "sketch": "<how to pursue it>", '
        '"risks": ["<risk>", ...], "next_steps": ["<step>", ...]}, ...]}\n'
        "One entry per cluster, index matching the list. JSON only."
    )
    return sys, usr


def cluster_and_deepen(
    pool: ScoredPool,
    *,
    function: str,
    router: Any = None,
    k: Optional[int] = None,
) -> dict[str, Any]:
    """Complete the Focus phase: cluster the scored pool, then expand the top-K
    survivors into actionable sketches (risks + next steps).

    A SEPARATE LLM call deepens only the top-K clusters (K config-driven, small).
    Degrades cleanly: with no router / an LLM failure / unparseable output, the
    clusters are still returned, top-K simply left un-deepened (sketch empty).
    Never raises.

    Returns ``{trace_id, function, k, cluster_count, clusters: [DeepenedCluster
    dicts...]}`` — top-K carry ``deepened=True`` with sketch/risks/next_steps;
    the rest are membership + scores only. Structured for dvg-wire-* consumers.
    """
    clusters = cluster_pool(pool)
    result_k = _resolve_deepen_k(router, k)
    top = clusters[:result_k]

    if top:
        try:
            from tools.llm.provider import LLMRequest

            if router is None:
                from tools.llm.router import LLMRouter

                router = LLMRouter()
            sys_prompt, usr_prompt = build_deepen_prompt(top)
            request = LLMRequest(
                messages=[{"role": "user", "content": usr_prompt}],
                system_prompt=sys_prompt,
            )
            response = router.invoke(function, request)
            deep = _parse_deepen_json(getattr(response, "content", "") or "")
            by_index = {e["index"]: e for e in deep if isinstance(e.get("index"), int)}
            for i, c in enumerate(top):
                entry = by_index.get(i, {})
                c.sketch = str(entry.get("sketch", "")).strip()
                c.risks = [str(r).strip() for r in (entry.get("risks") or []) if str(r).strip()]
                c.next_steps = [str(s).strip() for s in (entry.get("next_steps") or []) if str(s).strip()]
                c.deepened = bool(c.sketch or c.risks or c.next_steps)
        except Exception as exc:  # noqa: BLE001 — deepening is best-effort
            logger.warning("Divergence cluster deepening failed for '%s': %s", function, exc)

    return {
        "trace_id": pool.trace_id,
        "function": function,
        "k": result_k,
        "cluster_count": len(clusters),
        "clusters": [c.as_dict() for c in clusters],
    }


def _parse_deepen_json(content: str) -> list[dict[str, Any]]:
    raw = _parse_critic_json(content)  # tolerant of code fences / prose
    if raw:
        return raw
    # _parse_critic_json returns [] for a bare {"clusters": [...]} without a
    # "scores" key; retry explicitly for the clusters key.
    try:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
        m = _JSON_BLOCK.search(text)
        obj = json.loads(m.group(0) if m else text)
        if isinstance(obj, dict) and isinstance(obj.get("clusters"), list):
            return obj["clusters"]
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass
    return []


def _parse_args():
    import argparse

    p = argparse.ArgumentParser(description="Score a divergence idea pool")
    p.add_argument("--function", required=True)
    p.add_argument("--pool-file", required=True, help="File containing the idea-pool text")
    p.add_argument("--trace-id", default=None)
    p.add_argument("--no-persist", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def main() -> None:  # pragma: no cover - CLI entrypoint
    args = _parse_args()
    pool_text = Path(args.pool_file).read_text(encoding="utf-8")
    result = score_idea_pool(
        pool_text, function=args.function, trace_id=args.trace_id, persist=not args.no_persist
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(f"trace {result.trace_id} — {result.stop_reason} — {len(result.ordered)} ideas")
        for s in result.ordered:
            print(f"  [{s.composite:.3f}] ({s.frame}) {s.idea[:80]}  <n={s.novelty},v={s.viability},f={s.fit}>")


if __name__ == "__main__":  # pragma: no cover
    main()
