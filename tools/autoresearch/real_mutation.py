#!/usr/bin/env python3
# CUI // SP-CTI
"""Real mutation for ONE domain, in a worktree, with evidence lanes (xrv-lab-02).

WHY. After xrv-lab-01 the autoresearch loop says honestly that it measures
NOTHING: ``experiment_engine.run_loop`` evaluates a domain, creates a
candidate, runs it, evaluates AGAIN **with nothing changed**, and decides
keep/discard on that delta. Every payload therefore carries
``placeholder_metrics: True`` and the ``experiment`` reflex reports the whole
run ``unmeasurable``. An identity baseline is not an experiment.

This module makes ONE domain measure something real, bounded and reversible.
The shape is adapted from PRAXIST (candidates -> task-owned evaluator ->
evidence lanes); PRAXIST is Fair Source 1.0, so the SHAPE is adapted and NONE
of its code is used or vendored here.

ONE DOMAIN, AND THE LIST IS IN PYTHON, NOT IN CONFIG.
``REAL_MUTATION_DOMAINS`` is a frozen tuple holding exactly ``code_quality``.
``real_mutation.domains`` in args/autoresearch_config.yaml can only NARROW it
-- the intersection is taken, never the union -- because widening autonomous
code mutation to a domain nobody vetted must not be a YAML edit. The other
seven domains are untouched and keep reporting ``unmeasurable``.

TWO GATES, AND BOTH MUST BE OPEN. The master switch
(``experiment_engine.autoresearch_enabled``, unchanged, still ships off) plus
``real_mutation.enabled`` (DEFAULT FALSE, env override
``ICDEV_AUTORESEARCH_REAL_MUTATION``). Off means the real path is NEVER taken
and the domain falls back to today's placeholder path -- so the rollback is a
flag flip and not a merge revert. Off is REPORTED (``basis:
disabled_by_config`` / ``disabled_by_env``), never silent, and it is never the
same verdict as ``domain_not_enabled``: one is a switch, the other is a list.

THE MEASUREMENT IS THE POINT, AND A FAILED MEASUREMENT IS NEVER 0.0.
``fitness_evaluator.evaluate`` returns ``metric_value: 0.0`` with
``success: False`` when the underlying tool fails -- a real 0.0 and an
unmeasurable run are the same number there. ``measure()`` reads ``success``
FIRST and returns ``metric: None`` for a failure, so a broken analyzer can
never be read as "this tree scored zero" and can never move a posterior.
BEFORE is measured on the BASE (the fresh worktree, before any patch) and
AFTER on the PATCHED tree, by the SAME evaluator against the SAME
``project_dir`` -- two trees, one analyzer. Both are persisted on
``experiment_results``; ``placeholder_metrics`` is False ONLY on this path.

AND A REAL MEASUREMENT IS NOT AUTOMATICALLY A DETECTION. Measured here
2026-09-12: the tree-wide `code_quality` average over ~1,700 files did not move
at all (0.9312 -> 0.9312) for a patch that moved its own directory 0.988 ->
0.824, because the evolve declaration this loop obeys mandates ONE file per
mutation. A loop fed that number discards every hypothesis forever at delta
0.0, which is the identity baseline again with better manners. So the decision
is fed the SCOPED pair (`scoped_measurement`) and falls back to the tree pair
only with the dilution NAMED. Both pairs ride on every result.

THE WORKTREE IS REMOVED THROUGH GIT'S OWN DOOR, never ``shutil.rmtree``
(orphan_requeue's rule). The plain remove is always tried first and a refusal
is respected. It is NOT enough on its own, and that was measured rather than
reasoned: EVERY discarded experiment is dirty by construction -- a patch was
written, measured and rejected -- so the plain remove refused on every discard
and left a full checkout behind for each rejected hypothesis. So the discard
path PROVES the tree holds no commit beyond the base ref it was created from
(``_holds_no_commits``; None, cannot tell, refuses) and only then forces. The
one thing destroyed is the uncommitted patch this loop just decided against,
and nothing that was ever committed can be lost. The throwaway branch goes with
it through ``git branch -d`` -- never ``-D``, because git's refusal to delete a
branch carrying unmerged commits IS the proof that there were none.

IT NEVER MERGES. A kept candidate is committed, pushed and opened as a PR
titled ``autoresearch(<domain>): <hypothesis>``; a human merges it or nobody
does. There is deliberately no merge call anywhere in this module or in
``experiment_engine`` and ``tests/autoresearch/test_real_mutation.py`` reads
both ASTs to keep it that way -- a behavioural test over today's callers would
still pass the day somebody threads an auto-merge through.

THREE LANES (``LANES``), and the migration's CHECK is DERIVED from this tuple:
    incubator  a new candidate, and where one whose measurement could not be
               made STAYS. Not a failure -- an unmeasured experiment.
    frontier   kept: measured better than its base, PR open, awaiting a human.
    archive    discarded, or superseded.

Usage:
    python -m tools.autoresearch.real_mutation --gate --json
    python -m tools.autoresearch.real_mutation --lanes --json
    python -m tools.autoresearch.real_mutation --plan --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# The sys.path BOOTSTRAP only: run by path, sys.path[0] is this file's own
# directory, so the import root must be put back before the first first-party
# import below. Deliberately NOT reused as the repo root -- that is
# `icdev.core.paths.repo_root`, the ONE resolver (xit-decl-03), resolved just
# below. The two are the same directory in a source checkout and are NOT the
# same question: one is where `import tools` works from, the other is where
# `args/` lives.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]  # noqa: PTH100 - import root
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.common.helpers import now_iso  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

_REPO_ROOT = repo_root(__file__)

logger = get_logger("icdev.autoresearch.real_mutation")

# ── Declarations ─────────────────────────────────────────────────────────────

#: The evidence lanes. The migration derives ``experiment_candidates.lane``'s
#: CHECK from this tuple -- never a respelled list in SQL.
LANES: Tuple[str, ...] = ("incubator", "frontier", "archive")
LANE_INCUBATOR = "incubator"
LANE_FRONTIER = "frontier"
LANE_ARCHIVE = "archive"

#: The ONLY domains a real mutation may run for. Config may narrow, never widen.
REAL_MUTATION_DOMAINS: Tuple[str, ...] = ("code_quality",)

DEFAULT_ENV_OVERRIDE = "ICDEV_AUTORESEARCH_REAL_MUTATION"
_TRUTHY = ("1", "true", "yes", "on", "enabled")

#: What a real run can end as. ``unmeasurable`` is its own member and is NEVER
#: folded into ``discarded`` -- a candidate nothing could measure has not been
#: judged, and counting it as a rejection is how an acceptance rate becomes a
#: statement about the evaluator rather than about the hypotheses.
OUTCOME_KEPT = "kept"
OUTCOME_DISCARDED = "discarded"
OUTCOME_UNMEASURABLE = "unmeasurable"
OUTCOME_REFUSED = "refused"
OUTCOMES: Tuple[str, ...] = (
    OUTCOME_KEPT, OUTCOME_DISCARDED, OUTCOME_UNMEASURABLE, OUTCOME_REFUSED,
)

#: EVERY exit carries a NAMED reason, and the mapping is CLOSED -- a test reads
#: this module's AST and refuses a reason string outside it, so a later edit
#: cannot invent a silent exit.
REFUSALS: Dict[str, str] = {
    "domain_not_enabled": (
        "the domain is not in REAL_MUTATION_DOMAINS; only code_quality is "
        "vetted for autonomous mutation"
    ),
    "disabled_by_config": "real_mutation.enabled is false in args/autoresearch_config.yaml",
    "disabled_by_env": DEFAULT_ENV_OVERRIDE + " is set to a falsy value",
    "config_unreadable": "the switch or the bounds could not be read, and that is not consent",
    "adapter_unavailable": "the claude CLI is not installed on this host",
    "worktree_add_failed": "git worktree add refused; nothing was measured",
    "wall_clock_spent": "the per-run wall-clock budget was spent before this candidate started",
    "patch_timed_out": "the adapter did not finish inside patch_timeout_seconds",
    "patch_failed": "the adapter exited without a readable tree",
    "no_patch_produced": "the adapter changed no file in the worktree",
    "patch_out_of_bounds": "the adapter wrote outside allowed_directories or touched a forbidden file",
    "base_unmeasurable": "the BEFORE measurement could not be made",
    "post_unmeasurable": "the AFTER measurement could not be made",
    "commit_failed": "the kept patch could not be committed",
    "push_failed": "the kept patch could not be pushed; the worktree is KEPT so the work is not lost",
    "decision_failed": "the engine could not record a decision for this candidate",
}

#: What a run reports when only the TREE-WIDE pair was available. Its own
#: sentence, never REAL_METRICS_NOTE, because a 0.0 delta from a diluted average
#: and a 0.0 delta from a patch that genuinely changed nothing are different
#: facts and a reader must not have to guess which one a run is reporting.
REAL_METRICS_DILUTED_NOTE = (
    "Measured on two trees, but the SCOPED reading could not be made, so the "
    "delta is a tree-wide average over every file the evaluator walks. A "
    "one-file patch is below its resolution: a 0.0 delta here does NOT mean "
    "the patch changed nothing."
)

#: Reasons that describe THE HOST, not the hypothesis. They cannot change
#: between candidates in one run, so a loop that hits one stops rather than
#: paying for the same discovery again -- the BEFORE measurement is a full
#: `code_analyzer` pass and runs BEFORE the adapter is reached, so retrying an
#: absent CLI five times costs five analyzer runs and learns nothing new.
#: Deliberately NOT the circuit breaker: that counts REJECTED HYPOTHESES, and
#: feeding an infrastructure outage into it would trip a breaker whose whole
#: meaning is "this domain's ideas keep failing".
FATAL_REASONS: Tuple[str, ...] = (
    "adapter_unavailable",
    "config_unreadable",
    "worktree_add_failed",
    "wall_clock_spent",
    "domain_not_enabled",
    "disabled_by_config",
    "disabled_by_env",
)


def is_fatal(reason: Optional[str]) -> bool:
    """Would the next candidate in this run hit the same wall?"""
    return bool(reason) and reason in FATAL_REASONS


#: What a real run reports in place of the engine's identity-baseline note.
REAL_METRICS_NOTE = (
    "Measured: BEFORE on the unpatched worktree and AFTER on the patched "
    "worktree, by the same evaluator against the same project_dir."
)


# ── Config / gate ────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> Optional[dict]:
    """``None`` on any failure -- an unreadable declaration is never ``{}``."""
    try:
        import yaml  # noqa: PLC0415

        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - unreadable is a verdict, not a default
        return None


def _config(root: Optional[Path] = None) -> Optional[dict]:
    base = Path(root) if root else _REPO_ROOT
    return _load_yaml(base / "args" / "autoresearch_config.yaml")


def mutation_domains(config: Optional[dict] = None) -> Tuple[str, ...]:
    """The domains a real mutation may run for.

    The INTERSECTION of ``REAL_MUTATION_DOMAINS`` and the config's list, never
    the union: config narrows, code vets.
    """
    declared = tuple(REAL_MUTATION_DOMAINS)
    if not isinstance(config, dict):
        return declared
    block = config.get("real_mutation")
    if not isinstance(block, dict):
        return declared
    listed = block.get("domains")
    if not isinstance(listed, (list, tuple)):
        return declared
    return tuple(d for d in declared if str(d) in {str(x) for x in listed})


def real_mutation_gate(
    domain: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    config: Optional[dict] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Is the real-mutation path open FOR THIS DOMAIN?

    FAIL-CLOSED on an unreadable config -- this guards a loop whose declared
    purpose is autonomous code mutation, and "we could not read the switch" is
    not consent. Four distinct closed verdicts, never merged, because each
    sends a reader somewhere different: a list, a config value, an env var, and
    a broken file.
    """
    environ = os.environ if env is None else env
    cfg = _config(root) if config is None else config

    if cfg is None:
        return {
            "enabled": False, "basis": "config_unreadable", "domain": domain,
            "env_var": DEFAULT_ENV_OVERRIDE, "config_enabled": None,
        }

    raw_block = cfg.get("real_mutation") if isinstance(cfg, dict) else None
    block = raw_block if isinstance(raw_block, dict) else {}
    env_var = str(block.get("env_override") or DEFAULT_ENV_OVERRIDE)
    config_enabled = bool(block.get("enabled", False))

    if domain not in mutation_domains(cfg):
        return {
            "enabled": False, "basis": "domain_not_enabled", "domain": domain,
            "env_var": env_var, "config_enabled": config_enabled,
            "domains": list(mutation_domains(cfg)),
        }

    raw = environ.get(env_var)
    if raw is not None and str(raw).strip() != "":
        on = str(raw).strip().lower() in _TRUTHY
        return {
            "enabled": on,
            "basis": ("env:" + env_var) if on else "disabled_by_env",
            "domain": domain, "env_var": env_var, "env_value": str(raw),
            "config_enabled": config_enabled,
        }

    return {
        "enabled": config_enabled,
        "basis": "config:args/autoresearch_config.yaml" if config_enabled else "disabled_by_config",
        "domain": domain, "env_var": env_var, "config_enabled": config_enabled,
    }


def evolve_bounds(root: Optional[Path] = None) -> Dict[str, Any]:
    """``allowed_directories`` / ``forbidden_files``, READ from the ONE place
    that declares them: ``args/genesis_config.yaml`` -> ``reflexes.evolve``.

    Never a second copy here. The evolve reflex and this module mutate the same
    tree under the same human's declaration, and two spellings of "what may an
    autonomous writer touch" is how one of them comes to permit what the other
    refuses. ``readable: False`` means the declaration could not be read, and a
    run REFUSES rather than falling back to a permissive default.
    """
    base = Path(root) if root else _REPO_ROOT
    cfg = _load_yaml(base / "args" / "genesis_config.yaml")
    if not isinstance(cfg, dict):
        return {"readable": False, "allowed_directories": [], "forbidden_files": [],
                "max_files_per_cycle": None}

    node: Any = cfg
    for key in ("reflexes", "evolve"):
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        # Some layouts declare `evolve:` at the top level.
        top = cfg.get("evolve")
        node = top if isinstance(top, dict) else None
    if not isinstance(node, dict):
        return {"readable": False, "allowed_directories": [], "forbidden_files": [],
                "max_files_per_cycle": None}

    allowed = [str(x) for x in (node.get("allowed_directories") or [])]
    forbidden = [str(x) for x in (node.get("forbidden_files") or [])]
    if not allowed or not forbidden:
        # A half-read declaration is not a declaration: an empty forbidden list
        # would permit exactly the files the human wrote down to protect.
        return {"readable": False, "allowed_directories": allowed,
                "forbidden_files": forbidden, "max_files_per_cycle": None}
    return {
        "readable": True,
        "allowed_directories": allowed,
        "forbidden_files": forbidden,
        "max_files_per_cycle": node.get("max_files_per_cycle"),
    }


def _norm(rel: str) -> str:
    text = str(rel).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def classify_paths(paths: Sequence[str], bounds: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Split repo-relative *paths* into in/out of the declared bounds.

    A path is in bounds when it sits under an ``allowed_directories`` entry AND
    is not a ``forbidden_files`` entry. Forbidden wins -- ``CLAUDE.md`` is not
    under any allowed directory anyway, but ``tools/db/storage.py`` IS under
    ``tools/`` and must still be refused.
    """
    allowed = [_norm(d).rstrip("/") + "/" for d in (bounds.get("allowed_directories") or [])]
    forbidden = {_norm(f) for f in (bounds.get("forbidden_files") or [])}
    inside: List[str] = []
    outside: List[str] = []
    for raw in paths:
        rel = _norm(raw)
        if rel in forbidden or not any(rel.startswith(a) for a in allowed):
            outside.append(rel)
        else:
            inside.append(rel)
    return {"in_bounds": inside, "out_of_bounds": outside}


# ── Measurement ──────────────────────────────────────────────────────────────


def measure(
    domain: str,
    project_dir: Path,
    *,
    evaluate_fn: Optional[Callable[..., dict]] = None,
) -> Dict[str, Any]:
    """One fitness reading. ``metric`` is a float or **None**, never 0.0 for a
    failure.

    ``fitness_evaluator.evaluate`` reports a failed tool run as
    ``metric_value: 0.0, success: False`` -- the same number a genuinely
    zero-scoring tree would produce. Reading ``success`` first is the whole
    reason this wrapper exists: a maintainability score of 0.0 and "the
    analyzer did not run" justify opposite decisions.
    """
    path = Path(project_dir)
    text = str(path)
    if any(ch.isspace() for ch in text):
        # fitness_evaluator._run_tool builds its argv with `cmd.split()`, so a
        # path with a space does not error -- it measures a DIFFERENT tree.
        return {"metric": None, "basis": "project_dir_unsplittable",
                "project_dir": text, "measured_at": now_iso()}
    if not path.exists():
        return {"metric": None, "basis": "project_dir_absent",
                "project_dir": text, "measured_at": now_iso()}

    evaluator = evaluate_fn
    if evaluator is None:
        from tools.autoresearch.fitness_evaluator import evaluate as evaluator  # noqa: PLC0415

    try:
        result = evaluator(domain, project_dir=text)
    except Exception as exc:  # noqa: BLE001 - an evaluator that raised measured nothing
        return {"metric": None, "basis": "evaluator_raised", "error": str(exc)[:300],
                "project_dir": text, "measured_at": now_iso()}

    if not isinstance(result, dict) or not result.get("success"):
        err = result.get("error") if isinstance(result, dict) else None
        return {"metric": None, "basis": "evaluator_failed", "error": str(err or "")[:300],
                "project_dir": text, "measured_at": now_iso()}

    raw = result.get("metric_value")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return {"metric": None, "basis": "metric_absent", "project_dir": text,
                "measured_at": now_iso()}
    return {
        "metric": float(raw),
        "basis": "measured",
        "metric_name": result.get("metric_name"),
        "project_dir": text,
        "measured_at": now_iso(),
    }


#: A scope this wide is the tree-wide average again, so scoping buys nothing
#: and is reported as such rather than pretending to be a narrower reading.
_UNSCOPEABLE = ("", ".", "tools")


def patch_scope(paths: Sequence[str]) -> Optional[str]:
    """The ONE directory a patch is contained in, or **None**.

    None for an empty patch, for a patch spanning two directories (there is no
    single scope to measure), and for a patch whose directory IS the evaluator's
    own root -- where a scoped reading is the tree-wide reading and claiming
    otherwise would be a second name for the same number.
    """
    dirs = {_norm(p).rsplit("/", 1)[0] if "/" in _norm(p) else "" for p in paths}
    if len(dirs) != 1:
        return None
    only = dirs.pop()
    return None if only in _UNSCOPEABLE else only


def _materialise_base_scope(worktree: Path, scope: str, dest: Path) -> Optional[int]:
    """Rebuild *scope* AS IT WAS AT HEAD under *dest*. Returns the file count.

    Every file is read with ``git show HEAD:<path>`` -- a read-only question
    about the commit the worktree was created at. A file the patch ADDED does
    not exist at HEAD and is therefore absent here, which is correct: it was not
    in the BEFORE. **None** when git could not answer, so a partial
    reconstruction can never be measured and reported as a baseline.
    """
    rc, out, _err = _git(["ls-tree", "-r", "--name-only", "HEAD", "--", scope],
                         Path(worktree))
    if rc != 0:
        return None
    names = [line.strip() for line in out.splitlines() if line.strip()]
    written = 0
    for rel in names:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=str(worktree), capture_output=True,
            timeout=60, stdin=subprocess.DEVNULL,
        )
        if blob.returncode != 0:
            return None
        target = dest / Path(rel).name
        try:
            target.write_bytes(blob.stdout)
        except OSError:
            return None
        written += 1
    return written


def scoped_measurement(
    domain: str,
    worktree: Path,
    changed: Sequence[str],
    *,
    evaluate_fn: Optional[Callable[..., dict]] = None,
) -> Dict[str, Any]:
    """Measure the DIRECTORY THE PATCH TOUCHED, before and after.

    WHY THIS EXISTS, and it was measured rather than anticipated. The
    ``code_quality`` evaluator averages maintainability over EVERY file under
    ``tools/`` -- about 1,700 of them -- while the evolve declaration this loop
    obeys sets ``max_files_per_cycle: 1``. Proven on this checkout 2026-09-12: a
    deliberately awful 29-line module moved a two-file directory from 0.988 to
    0.824, and moved the tree-wide average from 0.9312 to **0.9312**, identical
    at the reported precision and four orders of magnitude below the 0.005 keep
    threshold. So the tree-wide reading is a REAL measurement that structurally
    CANNOT DETECT the change it is measuring, and a loop fed that number would
    discard every hypothesis forever at delta 0.0 -- functionally the identity
    baseline xrv-lab-01 exposed, wearing a real measurement's clothes.

    The BEFORE is reconstructed from ``HEAD`` rather than measured earlier,
    because the scope is not knowable until the patch exists. Same evaluator,
    same metric, two trees -- only the extent narrows.

    ``basis`` is one of: ``measured`` | ``scope_not_single_directory`` |
    ``scope_is_evaluator_root`` | ``base_unreadable`` | ``evaluator_failed``.
    Every non-measured basis leaves both metrics None, so a caller can never
    read a missing scoped reading as a zero delta.
    """
    import tempfile  # noqa: PLC0415

    scope = patch_scope(changed)
    if scope is None:
        dirs = {_norm(p).rsplit("/", 1)[0] if "/" in _norm(p) else "" for p in changed}
        basis = ("scope_is_evaluator_root"
                 if len(dirs) == 1 else "scope_not_single_directory")
        return {"basis": basis, "scope": None, "before": None, "after": None,
                "delta": None}

    # TemporaryDirectory, never mkdtemp + rmtree: this module must not contain
    # a recursive delete AT ALL, so the AST rule that keeps `git worktree
    # remove` the only way a tree is destroyed stays absolute rather than
    # needing a reader to check which path each rmtree points at.
    with tempfile.TemporaryDirectory(prefix="xrv-lab-02-base-") as staging_name:
        base_dir = Path(staging_name) / "base"
        base_dir.mkdir()
        count = _materialise_base_scope(worktree, scope, base_dir)
        if count is None:
            return {"basis": "base_unreadable", "scope": scope,
                    "before": None, "after": None, "delta": None}

        before = measure(domain, base_dir, evaluate_fn=evaluate_fn)
        after = measure(domain, Path(worktree) / scope, evaluate_fn=evaluate_fn)
        if before["metric"] is None or after["metric"] is None:
            return {"basis": "evaluator_failed", "scope": scope,
                    "before": before["metric"], "after": after["metric"],
                    "delta": None,
                    "before_basis": before["basis"], "after_basis": after["basis"]}
        return {
            "basis": "measured", "scope": scope,
            "before": before["metric"], "after": after["metric"],
            "delta": round(after["metric"] - before["metric"], 6),
            "base_files": count,
        }


# ── Git ──────────────────────────────────────────────────────────────────────


def _git(args: Sequence[str], cwd: Path, timeout: int = 300) -> Tuple[Optional[int], str, str]:
    """``rc`` is **None** when git could not be asked -- never 0, never 1."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return None, "", str(exc)[:300]


def worktree_base(root: Path, config: Optional[dict] = None) -> Path:
    """The ALREADY-DECLARED ``git.worktree_base`` -- never a path invented here."""
    cfg = config if isinstance(config, dict) else (_config(root) or {})
    git_block = cfg.get("git") if isinstance(cfg.get("git"), dict) else {}
    declared = git_block.get("worktree_base") or ".tmp/autoresearch/worktrees"
    return Path(root) / str(declared)


def branch_name(candidate_id: str, config: Optional[dict] = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    git_block = cfg.get("git") if isinstance(cfg.get("git"), dict) else {}
    prefix = git_block.get("branch_prefix") or "autoresearch/"
    return f"{prefix}{candidate_id}"


def create_worktree(root: Path, candidate_id: str, *, base_ref: str = "origin/main",
                    config: Optional[dict] = None) -> Dict[str, Any]:
    """One worktree, on ``autoresearch/<candidate>``, under the declared base."""
    cfg = config if config is not None else (_config(root) or {})
    base = worktree_base(root, cfg)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"created": False, "path": str(base / candidate_id),
                "branch": branch_name(candidate_id, cfg), "error": str(exc)[:300]}
    path = base / candidate_id
    branch = branch_name(candidate_id, cfg)
    rc, _out, err = _git(["worktree", "add", "-b", branch, str(path), base_ref], Path(root))
    if rc != 0:
        return {"created": False, "path": str(path), "branch": branch,
                "error": err.strip()[:300]}
    return {"created": True, "path": str(path), "branch": branch, "base_ref": base_ref}


def _holds_no_commits(worktree: Path, base_ref: str) -> Optional[bool]:
    """Does this worktree's branch hold NO commit beyond *base_ref*?

    **None** when git could not be asked. The caller treats None as "do not
    force", so an unreadable history can never authorise a destructive removal.
    """
    rc, out, _err = _git(["rev-list", "--count", f"{base_ref}..HEAD"], Path(worktree))
    if rc != 0:
        return None
    try:
        return int(out.strip()) == 0
    except ValueError:
        return None


def remove_worktree(root: Path, path: Path, *, base_ref: Optional[str] = None,
                    force_if_dirty: bool = False) -> Dict[str, Any]:
    """git's OWN door, never ``shutil.rmtree``.

    THE PLAIN REMOVE IS ALWAYS TRIED FIRST and is the only one used unless the
    caller asks otherwise: a dirty or locked worktree makes git refuse, and
    that refusal is the answer we want when we do not know what is in the tree.

    ``force_if_dirty`` exists because EVERY discarded experiment is dirty by
    construction -- the whole point is that a patch was written, measured and
    rejected, so the plain remove refuses on every discard and would leave a
    full checkout behind for each rejected hypothesis. It is still not a blunt
    ``--force``: the tree must first PROVE it holds no commit beyond the base
    ref it was created from, so nothing that was ever committed can be lost and
    the only thing destroyed is the uncommitted patch this loop just decided
    against. ``_holds_no_commits`` returning None (cannot tell) REFUSES -- the
    direction that keeps a directory rather than the one that deletes work.
    """
    rc, _out, err = _git(["worktree", "remove", str(path)], Path(root))
    if rc == 0:
        return {"removed": not Path(path).exists(), "path": str(path), "forced": False}

    if not force_if_dirty or not base_ref:
        return {"removed": False, "path": str(path), "forced": False,
                "error": err.strip()[:300]}

    clean_history = _holds_no_commits(Path(path), base_ref)
    if clean_history is not True:
        return {"removed": False, "path": str(path), "forced": False,
                "reason": ("history_unmeasurable" if clean_history is None
                           else "worktree_holds_commits"),
                "error": err.strip()[:300]}

    rc, _out, err2 = _git(["worktree", "remove", "--force", str(path)], Path(root))
    if rc != 0:
        return {"removed": False, "path": str(path), "forced": True,
                "error": err2.strip()[:300]}
    return {"removed": not Path(path).exists(), "path": str(path), "forced": True,
            "proof": "holds_no_commits_beyond_base"}


def delete_branch(root: Path, branch: str, base_ref: str) -> Dict[str, Any]:
    """Drop the throwaway branch of a discarded candidate.

    ``-d``, never ``-D``: git refuses to delete a branch holding unmerged
    commits, and that refusal IS the proof. A discarded candidate's branch sits
    exactly at *base_ref* with nothing committed on it, so ``-d`` succeeds; if
    it ever does not, something was committed and the branch is KEPT.
    """
    rc, _out, err = _git(["branch", "-d", branch], Path(root))
    if rc != 0:
        return {"deleted": False, "branch": branch, "error": err.strip()[:200]}
    return {"deleted": True, "branch": branch, "base_ref": base_ref}


def changed_files(worktree: Path) -> Optional[List[str]]:
    """Repo-relative paths the adapter touched, or **None** if git could not say.

    None is never an empty list: "the adapter changed nothing" and "we could
    not read the tree" send a reader to different places, and only the first is
    a statement about the patch.
    """
    rc, out, _err = _git(["status", "--porcelain"], Path(worktree))
    if rc != 0:
        return None
    paths: List[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:  # a rename names both sides; the destination is the file
            entry = entry.split(" -> ", 1)[1]
        paths.append(_norm(entry.strip('"')))
    return paths


# ── The patch ────────────────────────────────────────────────────────────────


def patch_prompt(hypothesis: str, bounds: Mapping[str, Any]) -> str:
    """The instruction handed to the adapter.

    It states the bounds it is measured against and it forbids the git verbs
    this module owns: a patch that commits, pushes or opens its own PR would
    make the before/after measurement describe a tree nobody can reconstruct.
    """
    allowed = ", ".join(bounds.get("allowed_directories") or []) or "(none)"
    forbidden = ", ".join(bounds.get("forbidden_files") or []) or "(none)"
    max_files = bounds.get("max_files_per_cycle") or 1
    return (
        "You are running one bounded autoresearch experiment on this worktree.\n\n"
        f"HYPOTHESIS TO IMPLEMENT:\n{hypothesis}\n\n"
        "RULES:\n"
        f"- Change at most {max_files} file(s). One focused change, not a sweep.\n"
        f"- You may only edit files under: {allowed}\n"
        f"- You must NOT touch: {forbidden}\n"
        "- Do NOT run git commit, git push, git branch, git worktree, or gh. The\n"
        "  harness commits and opens the PR; a commit here breaks the measurement.\n"
        "- Do NOT edit tests to make anything pass.\n"
        "- The change is measured by tools/analysis/code_analyzer.py maintainability\n"
        "  over tools/. Leave the tree importable.\n"
        "- If the hypothesis cannot be implemented safely, change nothing and say so.\n"
    )


def _terminate(proc) -> str:
    """Graceful terminate of the WHOLE tree, never a bare kill of the parent.

    ``subprocess`` kills only the process it started, and the CLI spawns
    children that keep the inherited log handle open -- kph-repark-mfx-ci-04
    measured a 115s block on exactly that shape. ``taskkill /T`` and a
    terminate+wait are the two portable spellings.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        else:
            proc.terminate()
        proc.wait(timeout=30)
        return "terminated"
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return "kill_failed:" + str(exc)[:120]


def dispatch_patch(
    candidate_id: str,
    hypothesis: str,
    worktree: Path,
    *,
    bounds: Mapping[str, Any],
    timeout_seconds: int,
    adapter: Any = None,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the claude_cli adapter against *worktree* and return what happened.

    ``spawn`` rather than ``invoke``, on purpose: spawn writes the CLI's RAW
    ``--output-format json`` envelope into a log file, which is exactly what
    ``cost.task_attribution.record_task_cost`` parses. ``invoke`` returns the
    envelope already transformed, so writing that back would hand the cost
    reader a shape its own parser does not produce.
    """
    from tools.agents.adapter_base import AgentSession  # noqa: PLC0415

    if adapter is None:
        from tools.agents.adapters.claude_cli import ClaudeCliAdapter  # noqa: PLC0415

        adapter = ClaudeCliAdapter()

    try:
        if not adapter.available():
            return {"dispatched": False, "reason": "adapter_unavailable"}
    except Exception as exc:  # noqa: BLE001
        return {"dispatched": False, "reason": "adapter_unavailable",
                "error": str(exc)[:200]}

    log = Path(log_path) if log_path else Path(worktree) / ".autoresearch-patch.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"dispatched": False, "reason": "adapter_unavailable",
                "error": str(exc)[:200]}

    session = AgentSession(
        task_id=candidate_id,
        prompt=patch_prompt(hypothesis, bounds),
        working_dir=str(worktree),
        timeout_seconds=int(timeout_seconds),
        metadata={"dispatch_source": "autoresearch",
                  "temp_dir": str(Path(worktree) / ".tmp")},
    )

    t0 = time.monotonic()
    handle = open(str(log), "w", encoding="utf-8", errors="replace")
    try:
        proc = adapter.spawn(session, stdout=handle, stderr=subprocess.STDOUT)
    except Exception as exc:  # noqa: BLE001
        handle.close()
        return {"dispatched": False, "reason": "adapter_unavailable",
                "error": str(exc)[:200], "log_path": str(log)}
    finally:
        # The child holds its own duplicated handle; this one is ours to drop.
        if not handle.closed:
            handle.close()

    deadline = t0 + float(timeout_seconds)
    while proc.poll() is None:
        if time.monotonic() > deadline:
            disposition = _terminate(proc)
            return {"dispatched": True, "timed_out": True, "reason": "patch_timed_out",
                    "kill": disposition, "log_path": str(log),
                    "duration_seconds": round(time.monotonic() - t0, 2)}
        time.sleep(1.0)

    return {
        "dispatched": True,
        "timed_out": False,
        "exit_code": proc.returncode,
        "log_path": str(log),
        "duration_seconds": round(time.monotonic() - t0, 2),
    }


def record_cost(candidate_id: str, log_path: Path) -> Dict[str, Any]:
    """xrv-cost-02's attribution, with ``task_id`` = the CANDIDATE id.

    Best-effort and never raises: a cost row that could not be written must not
    lose the experiment that produced it. An absent envelope records NOTHING --
    a zero row would read as "this dispatch was free".
    """
    try:
        from tools.cost.task_attribution import record_task_cost  # noqa: PLC0415

        return dict(record_task_cost(candidate_id, Path(log_path),
                                     project_id="autoresearch"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_cost: attribution not written for %s: %s", candidate_id, exc)
        return {"recorded": False, "reason": "attribution_failed", "error": str(exc)[:200]}


# ── Keep: commit, push, PR. Never merge. ─────────────────────────────────────


def commit_and_push(worktree: Path, branch: str, *, domain: str, hypothesis: str,
                    candidate_id: str, push: bool = True) -> Dict[str, Any]:
    """Commit the patch and push the branch. There is no merge verb here."""
    rc, _out, err = _git(["add", "-A"], Path(worktree))
    if rc != 0:
        return {"committed": False, "reason": "commit_failed", "error": err.strip()[:300]}
    headline = (hypothesis.strip().splitlines() or [""])[0][:120]
    message = (
        f"autoresearch({domain}): {headline}\n\n"
        f"Candidate: {candidate_id}\n"
        "Produced autonomously by the autoresearch loop (xrv-lab-02). NOT merged\n"
        "by the loop -- a human decides.\n"
    )
    rc, _out, err = _git(["commit", "-m", message], Path(worktree))
    if rc != 0:
        return {"committed": False, "reason": "commit_failed", "error": err.strip()[:300]}
    rc, out, _err = _git(["rev-parse", "HEAD"], Path(worktree))
    sha = out.strip() if rc == 0 else None
    if not push:
        return {"committed": True, "pushed": False, "commit": sha, "push_skipped": True}
    rc, _out, err = _git(["push", "-u", "origin", branch], Path(worktree), timeout=600)
    if rc != 0:
        return {"committed": True, "pushed": False, "commit": sha,
                "reason": "push_failed", "error": err.strip()[:300]}
    return {"committed": True, "pushed": True, "commit": sha}


def open_pr(worktree: Path, branch: str, *, domain: str, hypothesis: str,
            candidate_id: str, body_extra: str = "") -> Dict[str, Any]:
    """``gh pr create`` and NOTHING else. This module never merges."""
    headline = (hypothesis.strip().splitlines() or [""])[0][:120]
    title = f"autoresearch({domain}): {headline}"
    body = (
        f"Autonomous autoresearch experiment `{candidate_id}` (domain `{domain}`).\n\n"
        f"**Hypothesis**\n\n{hypothesis.strip()}\n\n"
        f"{body_extra}\n\n"
        "This PR was opened by the autoresearch loop and is **not merged by it**. "
        "A human decides.\n"
    )
    try:
        proc = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
            cwd=str(worktree), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, stdin=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return {"opened": False, "error": str(exc)[:300], "title": title}
    if proc.returncode != 0:
        return {"opened": False, "error": (proc.stderr or "").strip()[:300], "title": title}
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return {"opened": True, "url": lines[-1].strip() if lines else "", "title": title}


# ── Lanes ────────────────────────────────────────────────────────────────────


def _lane_column_present(conn) -> Optional[bool]:
    """Does ``experiment_candidates.lane`` exist? **None** when unreadable.

    Asked of the LIVE catalogue, never of the DDL: ``CREATE TABLE IF NOT
    EXISTS`` never alters an existing table, so a board that has not run the
    migration keeps a table the DDL has moved on from.
    """
    try:
        conn.execute("SELECT lane FROM experiment_candidates LIMIT 1").fetchall()
        return True
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "lane" in text and ("column" in text or "no such" in text):
            return False
        try:
            conn.execute("SELECT 1 FROM experiment_candidates LIMIT 1").fetchall()
        except Exception:  # noqa: BLE001 - the table itself could not be read
            return None
        return False


def set_lane(candidate_id: str, lane: str, *, conn=None) -> Dict[str, Any]:
    """Move a candidate between evidence lanes.

    A board without the migration reports ``lane_column_absent`` -- never a
    silently skipped write, and never a fabricated lane.
    """
    if lane not in LANES:
        return {"set": False, "reason": "unknown_lane", "lane": lane, "lanes": list(LANES)}

    own = conn is None
    ctx = None
    if own:
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            ctx = get_connection()
            conn = ctx.__enter__()
        except Exception as exc:  # noqa: BLE001
            return {"set": False, "reason": "db_unreachable", "error": str(exc)[:200],
                    "lane": lane}

    try:
        present = _lane_column_present(conn)
        if present is None:
            return {"set": False, "reason": "lane_column_unmeasurable", "lane": lane}
        if not present:
            return {"set": False, "reason": "lane_column_absent", "lane": lane}
        from tools.db.storage import sql_placeholder  # noqa: PLC0415

        ph = sql_placeholder(conn)
        conn.execute(
            f"UPDATE experiment_candidates SET lane = {ph}, updated_at = {ph} WHERE id = {ph}",
            (lane, now_iso(), candidate_id),
        )
        return {"set": True, "lane": lane, "candidate_id": candidate_id}
    except Exception as exc:  # noqa: BLE001
        return {"set": False, "reason": "update_failed", "error": str(exc)[:200], "lane": lane}
    finally:
        if own and ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


def lane_census(*, conn=None) -> Dict[str, Any]:
    """Counts per lane.

    ``unmeasurable`` -- never a wall of zeroes -- when the column or the board
    cannot be read: a board that has never run the migration has an UNKNOWN
    number of candidates per lane, and printing three zeroes asserts it has
    none.
    """
    own = conn is None
    ctx = None
    if own:
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            ctx = get_connection()
            conn = ctx.__enter__()
        except Exception as exc:  # noqa: BLE001
            return {"state": "unmeasurable", "reason": "db_unreachable",
                    "error": str(exc)[:200], "by_lane": None, "unassigned": None,
                    "total": None, "lanes": list(LANES)}
    try:
        present = _lane_column_present(conn)
        if present is None:
            return {"state": "unmeasurable", "reason": "catalogue_unreadable",
                    "by_lane": None, "unassigned": None, "total": None,
                    "lanes": list(LANES)}
        if not present:
            return {"state": "unmeasurable", "reason": "lane_column_absent",
                    "by_lane": None, "unassigned": None, "total": None,
                    "lanes": list(LANES)}
        rows = conn.execute(
            "SELECT lane, COUNT(*) AS cnt FROM experiment_candidates GROUP BY lane"
        ).fetchall()
        by_lane = {lane: 0 for lane in LANES}
        unassigned = 0
        for row in rows:
            data = dict(row)
            key = data.get("lane")
            count = int(data.get("cnt") or 0)
            if key in by_lane:
                by_lane[key] += count
            else:
                unassigned += count
        total = sum(by_lane.values()) + unassigned
        return {
            "state": "measured" if total else "empty",
            "by_lane": by_lane,
            "unassigned": unassigned,
            "total": total,
            "lanes": list(LANES),
        }
    except Exception as exc:  # noqa: BLE001
        return {"state": "unmeasurable", "reason": "query_failed",
                "error": str(exc)[:200], "by_lane": None, "unassigned": None,
                "total": None, "lanes": list(LANES)}
    finally:
        if own and ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


# ── The run ──────────────────────────────────────────────────────────────────


def _refuse(name: str, **extra: Any) -> Dict[str, Any]:
    """Every exit carries a NAMED reason from the closed ``REFUSALS`` mapping."""
    payload: Dict[str, Any] = {
        "outcome": OUTCOME_REFUSED,
        "reason": name,
        "reason_detail": REFUSALS.get(name, ""),
        "measured": False,
        "metric_before": None,
        "metric_after": None,
        "placeholder_metrics": None,
        "timestamp": now_iso(),
    }
    payload.update(extra)
    return payload


def run_real_experiment(
    candidate_id: str,
    hypothesis: str,
    *,
    domain: str = "code_quality",
    root: Optional[Path] = None,
    config: Optional[dict] = None,
    adapter: Any = None,
    evaluate_fn: Optional[Callable[..., dict]] = None,
    decide_fn: Optional[Callable[..., dict]] = None,
    open_pr_fn: Optional[Callable[..., dict]] = None,
    base_ref: Optional[str] = None,
    deadline: Optional[float] = None,
    push: Optional[bool] = None,
) -> Dict[str, Any]:
    """ONE real experiment: worktree -> BEFORE -> patch -> AFTER -> keep/discard.

    ``outcome`` is one of ``OUTCOMES``. ``metric_before`` and ``metric_after``
    are floats ONLY when both were measured; either being None makes the run
    ``unmeasurable``, leaves the candidate in ``incubator`` and names the
    failing side. Nothing here decides a threshold or a posterior --
    ``experiment_engine.decide`` owns that, unchanged, and is handed these two
    real numbers where an identity baseline used to supply one number twice.
    """
    base = Path(root) if root else _REPO_ROOT
    cfg = config if config is not None else (_config(base) or {})
    raw_block = cfg.get("real_mutation") if isinstance(cfg, dict) else None
    block = raw_block if isinstance(raw_block, dict) else {}

    gate = real_mutation_gate(domain, config=cfg, root=base)
    if not gate.get("enabled"):
        basis = gate.get("basis")
        return _refuse(basis if basis in REFUSALS else "domain_not_enabled",
                       gate=gate, candidate_id=candidate_id, domain=domain)

    if deadline is not None and time.monotonic() > deadline:
        return _refuse("wall_clock_spent", candidate_id=candidate_id, domain=domain)

    bounds = evolve_bounds(base)
    if not bounds.get("readable"):
        # A permissive default here would let an autonomous writer touch
        # anything the moment a config read failed.
        return _refuse("config_unreadable", candidate_id=candidate_id, domain=domain,
                       bounds=bounds)

    ref = base_ref or str(block.get("base_ref") or "origin/main")
    wt = create_worktree(base, candidate_id, base_ref=ref, config=cfg)
    if not wt.get("created"):
        return _refuse("worktree_add_failed", candidate_id=candidate_id, domain=domain,
                       worktree=wt)

    worktree = Path(wt["path"])
    branch = wt["branch"]
    project_dir = worktree / "tools"
    report: Dict[str, Any] = {
        "candidate_id": candidate_id, "domain": domain, "branch": branch,
        "worktree": str(worktree), "base_ref": ref, "real_mutation": True,
        "timestamp": now_iso(),
    }

    def finish(outcome: str, lane: str, **extra: Any) -> Dict[str, Any]:
        out = dict(report)
        out.update({"outcome": outcome, "lane": lane})
        out.update(extra)
        out["lane_write"] = set_lane(candidate_id, lane)
        return out

    def abandon(reason: str, *, lane: str, metric_before: Optional[float],
                outcome: str) -> Dict[str, Any]:
        removal = remove_worktree(base, worktree, base_ref=ref, force_if_dirty=True)
        branch_drop = delete_branch(base, branch, ref) if removal.get("removed") else {}
        return finish(outcome, lane, measured=False, reason=reason,
                      reason_detail=REFUSALS.get(reason, ""),
                      metric_before=metric_before, metric_after=None,
                      placeholder_metrics=None,
                      worktree_removed=removal.get("removed"),
                      worktree_removal=removal,
                      branch_deleted=branch_drop.get("deleted"))

    try:
        # ── BEFORE, on the base ──────────────────────────────────────────────
        before = measure(domain, project_dir, evaluate_fn=evaluate_fn)
        report["before"] = before
        if before["metric"] is None:
            return abandon("base_unmeasurable", lane=LANE_INCUBATOR,
                           metric_before=None, outcome=OUTCOME_UNMEASURABLE)

        # ── The patch ────────────────────────────────────────────────────────
        timeout = int(block.get("patch_timeout_seconds") or 900)
        if deadline is not None:
            timeout = max(60, min(timeout, int(deadline - time.monotonic())))
        patch = dispatch_patch(candidate_id, hypothesis, worktree, bounds=bounds,
                               timeout_seconds=timeout, adapter=adapter)
        report["patch"] = patch
        if patch.get("log_path"):
            report["cost"] = record_cost(candidate_id, Path(patch["log_path"]))

        if not patch.get("dispatched"):
            return abandon(patch.get("reason") or "adapter_unavailable",
                           lane=LANE_INCUBATOR, metric_before=before["metric"],
                           outcome=OUTCOME_UNMEASURABLE)
        if patch.get("timed_out"):
            return abandon("patch_timed_out", lane=LANE_INCUBATOR,
                           metric_before=before["metric"],
                           outcome=OUTCOME_UNMEASURABLE)

        touched = changed_files(worktree)
        if touched is None:
            return abandon("patch_failed", lane=LANE_INCUBATOR,
                           metric_before=before["metric"],
                           outcome=OUTCOME_UNMEASURABLE)

        # The adapter's own scratch and log never count as a patch.
        touched = [p for p in touched
                   if not p.startswith(".tmp/") and p != ".autoresearch-patch.log"]
        report["changed_files"] = touched
        if not touched:
            return abandon("no_patch_produced", lane=LANE_ARCHIVE,
                           metric_before=before["metric"], outcome=OUTCOME_DISCARDED)

        split = classify_paths(touched, bounds)
        report["bounds_check"] = split
        if split["out_of_bounds"]:
            return abandon("patch_out_of_bounds", lane=LANE_ARCHIVE,
                           metric_before=before["metric"], outcome=OUTCOME_DISCARDED)

        # ── AFTER, on the patched tree ───────────────────────────────────────
        after = measure(domain, project_dir, evaluate_fn=evaluate_fn)
        report["after"] = after
        if after["metric"] is None:
            return abandon("post_unmeasurable", lane=LANE_INCUBATOR,
                           metric_before=before["metric"],
                           outcome=OUTCOME_UNMEASURABLE)

        # The tree-wide pair above is a REAL measurement that cannot DETECT a
        # one-file change (see `scoped_measurement`). The scoped pair is the
        # delta the patch actually caused; both are carried, and which one fed
        # the decision is recorded rather than inferred.
        scoped = scoped_measurement(domain, worktree, touched, evaluate_fn=evaluate_fn)
        report["scoped"] = scoped

        return _measured(
            report=report, finish=finish, base=base, worktree=worktree, branch=branch,
            candidate_id=candidate_id, domain=domain, hypothesis=hypothesis,
            before=before, after=after, scoped=scoped, block=block,
            decide_fn=decide_fn, open_pr_fn=open_pr_fn, push=push,
        )
    except Exception as exc:  # noqa: BLE001 - a run that raised measured nothing
        logger.warning("run_real_experiment %s raised: %s", candidate_id, exc)
        removal = remove_worktree(base, worktree, base_ref=ref, force_if_dirty=True)
        return finish(OUTCOME_UNMEASURABLE, LANE_INCUBATOR, measured=False,
                      reason="patch_failed", error=str(exc)[:300],
                      metric_before=None, metric_after=None,
                      placeholder_metrics=None,
                      worktree_removed=removal.get("removed"))


def _measured(*, report, finish, base, worktree, branch, candidate_id, domain,
              hypothesis, before, after, scoped, block, decide_fn, open_pr_fn,
              push) -> Dict[str, Any]:
    """Both sides measured. Hand the two numbers to the ENGINE's own decision.

    ``experiment_engine.decide`` is UNCHANGED and still owns the keep threshold,
    the objective direction, the ``experiment_results`` row and the Thompson
    posterior. This function supplies its inputs and reads its verdict; it does
    not re-derive any of them, because a second copy of the keep rule is how the
    loop and the board come to disagree about a candidate neither of them moved.

    WHICH PAIR IT IS HANDED IS THE ONE REAL CHOICE HERE, and it is recorded on
    the result as ``decision_basis`` rather than left to be inferred:

      scoped              the patch's own directory, before and after. Used
                          whenever it is measurable, because the tree-wide
                          average over ~1,700 files cannot represent a
                          one-file change -- measured 2026-09-12, a
                          deliberately awful module moved its own directory
                          0.988 -> 0.824 and the tree 0.9312 -> 0.9312.
      tree_wide_diluted   the scoped reading could not be made. The tree pair
                          is used and the dilution is NAMED, so a delta of 0.0
                          here is never read as "the patch changed nothing".

    Both pairs ride on the result either way. Nothing is hidden by the choice.
    """
    decider = decide_fn
    if decider is None:
        from tools.autoresearch.experiment_engine import decide as decider  # noqa: PLC0415

    scoped_ok = isinstance(scoped, dict) and scoped.get("basis") == "measured"
    if scoped_ok:
        pre, post, decision_basis = scoped["before"], scoped["after"], "scoped"
    else:
        pre, post = before["metric"], after["metric"]
        decision_basis = "tree_wide_diluted"

    verdict = decider(candidate_id, pre_metric=pre, post_metric=post)
    report["decision"] = verdict

    common: Dict[str, Any] = dict(
        measured=True,
        metric_before=pre,
        metric_after=post,
        metric_delta=verdict.get("metric_delta"),
        decision_basis=decision_basis,
        # Carried whichever pair decided, so the choice hides nothing.
        tree_before=before["metric"],
        tree_after=after["metric"],
        scoped_before=scoped.get("before") if isinstance(scoped, dict) else None,
        scoped_after=scoped.get("after") if isinstance(scoped, dict) else None,
        scoped_basis=scoped.get("basis") if isinstance(scoped, dict) else None,
        scope=scoped.get("scope") if isinstance(scoped, dict) else None,
        # The ONLY path in this repo that may say False.
        placeholder_metrics=False,
        metrics_note=(REAL_METRICS_NOTE if scoped_ok else REAL_METRICS_DILUTED_NOTE),
    )

    if not verdict.get("success"):
        return finish(OUTCOME_UNMEASURABLE, LANE_INCUBATOR, reason="decision_failed",
                      reason_detail=REFUSALS["decision_failed"],
                      worktree_removed=remove_worktree(base, worktree).get("removed"),
                      **common)

    if verdict.get("decision") != "keep":
        # Measured, judged, rejected: the tree holds this loop's own patch and
        # nothing else, which `remove_worktree` re-proves before it forces.
        ref = report.get("base_ref") or ""
        removal = remove_worktree(base, worktree, base_ref=ref, force_if_dirty=True)
        branch_drop = delete_branch(base, branch, ref) if removal.get("removed") else {}
        return finish(OUTCOME_DISCARDED, LANE_ARCHIVE,
                      worktree_removed=removal.get("removed"),
                      worktree_removal=removal,
                      branch_deleted=branch_drop.get("deleted"), **common)

    do_push = bool(block.get("open_pr", True)) if push is None else bool(push)
    landed = commit_and_push(worktree, branch, domain=domain, hypothesis=hypothesis,
                             candidate_id=candidate_id, push=do_push)
    report["commit"] = landed
    if not landed.get("committed"):
        return finish(OUTCOME_UNMEASURABLE, LANE_INCUBATOR, reason="commit_failed",
                      reason_detail=REFUSALS["commit_failed"],
                      worktree_removed=False, **common)
    if do_push and not landed.get("pushed"):
        # The worktree is KEPT on purpose: the work is committed locally only,
        # and removing the tree now would leave it reachable by reflog alone.
        return finish(OUTCOME_KEPT, LANE_FRONTIER, reason="push_failed",
                      reason_detail=REFUSALS["push_failed"],
                      pr={"opened": False, "reason": "push_failed"},
                      worktree_removed=False, **common)

    pr: Dict[str, Any] = {"opened": False, "reason": "push_skipped"}
    if do_push:
        body_extra = (
            "| metric | value |\n|---|---|\n"
            f"| before (`{before.get('metric_name')}`) | {before['metric']} |\n"
            f"| after | {after['metric']} |\n"
            f"| delta | {verdict.get('metric_delta')} |\n"
        )
        opener = open_pr_fn or open_pr
        pr = opener(worktree, branch, domain=domain, hypothesis=hypothesis,
                    candidate_id=candidate_id, body_extra=body_extra)
    report["pr"] = pr

    # Pushed, so the commit is on the remote. Only the working TREE goes -- the
    # branch must survive, because the PR points at it.
    removal = (remove_worktree(base, worktree, base_ref=report.get("base_ref"),
                               force_if_dirty=True)
               if pr.get("opened") else {"removed": False})
    return finish(OUTCOME_KEPT, LANE_FRONTIER, pr=pr,
                  worktree_removed=removal.get("removed"), **common)


# ── CLI ──────────────────────────────────────────────────────────────────────


def plan(root: Optional[Path] = None) -> Dict[str, Any]:
    """What a run WOULD do. Acts on nothing: no worktree, no adapter, no write."""
    base = Path(root) if root else _REPO_ROOT
    cfg = _config(base)
    return {
        "declared_domains": list(REAL_MUTATION_DOMAINS),
        "effective_domains": list(mutation_domains(cfg or {})),
        "gates": {d: real_mutation_gate(d, root=base) for d in REAL_MUTATION_DOMAINS},
        "bounds": evolve_bounds(base),
        "worktree_base": str(worktree_base(base, cfg or {})),
        "lanes": list(LANES),
        "outcomes": list(OUTCOMES),
        "refusals": sorted(REFUSALS),
        "acts": False,
        "timestamp": now_iso(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real mutation for the autoresearch loop (xrv-lab-02)")
    parser.add_argument("--gate", action="store_true", help="Report the two switches")
    parser.add_argument("--lanes", action="store_true", help="Candidate census by lane")
    parser.add_argument("--plan", action="store_true",
                        help="What a run would do; acts on nothing")
    parser.add_argument("--domain", default="code_quality")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.lanes:
        payload: Dict[str, Any] = lane_census()
    elif args.gate:
        payload = real_mutation_gate(args.domain)
    else:
        payload = plan()

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
