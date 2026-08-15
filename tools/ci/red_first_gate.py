#!/usr/bin/env python3
# CUI // SP-CTI
"""Red-first proof as a merge gate — record the RED, do not just mandate it.

WHY THIS EXISTS (trust-disc-01)
-------------------------------
ANVIL mandates RED -> GREEN and nothing anywhere records the RED. A process
instruction whose evidence is never captured is the ``|| true`` failure of D394
in a second form: the rule is stated, the artifact proving it fired is absent,
and no reader can distinguish a check that ran from one that did not.

So this gate re-derives the RED rather than trusting that it happened. For every
test file the PR ADDS or MODIFIES it checks out the merge base, applies ONLY that
test file on top, and runs it there. The test must FAIL against the pre-change
tree and PASS against the post-change tree.

  fails before, passes after -> ``discriminating``. The RED is recorded, with the
                                pre-change pytest output as the artifact.
  passes before              -> it asserts CURRENT behaviour rather than REQUIRED
                                behaviour. It could never have gone red.
  fails after too            -> it is broken, not red-first.

THE CASE THAT MOTIVATES IT
--------------------------
A test asserting that ``check_project_card_coverage`` "degrades honestly when the
board is unreachable" passed locally because the UNPATCHED call raised: the
monkeypatch had landed on ``tools.db.storage`` while the checker resolved
``icdev.tools.db.storage``, two distinct module objects. It was correct-looking,
reviewed, and worthless. This gate catches it because that test passes against
the pre-change tree.

PRIOR ART, AND THE PRIMITIVE IS SHARED
--------------------------------------
``tools/security/reproduction_validator.py`` already states the rule exactly —
*the same reproduction must fire against the vulnerable target and must STOP
firing once the fix is applied; only then is ``discriminating`` set.* That module
is scoped to HTTP replay against an allowlisted target, so it is not a drop-in.
What IS shared is the decision itself: ``decide_discrimination`` and
``DiscriminationVocabulary`` live there and are imported here, so the two gates
cannot drift apart on the one question they both ask. "Fires" means "the replay's
predicate evaluated true" over there and "the test FAILED" over here.

WHAT THIS GATE CANNOT PROVE
---------------------------
Two honest limits, stated rather than hidden:

* A test for a module the PR also ADDS goes red at the merge base by ImportError,
  whatever it asserts. That is a genuine RED — the canonical TDD red for new code
  — but it is a weak one, and ``collection_error`` is reported distinctly from
  ``failed`` in the proof so a reader can tell which they are looking at.
* Only the test file is applied on top. If it imports a helper the PR also adds,
  the merge-base run fails on the missing helper. Same shape as above: a real
  red, a weak one, and visible in the captured output.

The gate is a FLOOR. It makes "the test never went red" impossible to ship
silently; it does not make every test that clears it a good test.

Usage
-----
    python tools/ci/red_first_gate.py                       # report, exit 0
    python tools/ci/red_first_gate.py --gate                # exit 1 on a finding
    python tools/ci/red_first_gate.py --json --out proof.json
    python tools/ci/red_first_gate.py --base origin/main --gate
    python tools/ci/red_first_gate.py --files tests/test_foo.py --gate
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

#: ``@@ -a,b +c,d @@`` — group 1 is the first line number in the NEW file.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

#: Token types carrying TEXT rather than executable code — dropped before a
#: marker is matched. FSTRING_* exists only on Python 3.12+, where an f-string
#: is no longer one STRING token but START / MIDDLE / expression / END. Without
#: naming them, ``f"assert {x}"`` leaks its literal half back in as code, which
#: is the same false positive one layer down. The embedded EXPRESSION tokens are
#: not listed: those really are code.
_TEXT_TOKENS = frozenset(
    {tokenize.COMMENT, tokenize.STRING}
    | {getattr(tokenize, name) for name in
       ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
       if hasattr(tokenize, name)}
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# The decision table is NOT reimplemented here. Both import paths are tried
# because this module is mirrored to icdev/tools/ci/ byte-for-byte, and the two
# trees sit at different depths relative to the repository root.
try:
    from tools.security.reproduction_validator import (
        DiscriminationVocabulary,
        decide_discrimination,
    )
except ImportError:  # pragma: no cover - packaged tree without the tools/ shim
    from icdev.tools.security.reproduction_validator import (  # type: ignore[no-redef]
        DiscriminationVocabulary,
        decide_discrimination,
    )

#: Policy config, relative to the repository root.
CONFIG_PATH = Path("args") / "red_first_gate.yaml"

#: Refs tried, in order, when ``--base`` is not given.
DEFAULT_BASE_REFS = ("origin/main", "origin/master", "main", "master")


class RedFirstError(RuntimeError):
    """The gate could not run at all — not a finding, a harness failure."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class RunOutcome:
    """Result of running one test file in one state of the tree."""

    failed = "failed"
    """pytest exit 1 — assertions failed. The RED everyone means by "RED"."""

    collection_error = "collection_error"
    """pytest exit 2 — the module did not even import. Still a red (the test
    cannot pass against this tree), but a weaker one: for a brand-new module it
    says only that the module is new. Reported distinctly for that reason."""

    passed = "passed"
    """pytest exit 0 — every collected test passed."""

    no_tests = "no_tests"
    """pytest exit 5 — nothing was collected. We learned NOTHING; explicitly not
    ``passed``, because "no tests ran" and "the tests passed" are the exact pair
    this task exists to stop conflating."""

    timeout = "timeout"
    """The run exceeded ``run.timeout_seconds``. Indecisive."""

    error = "error"
    """pytest internal/usage error, or the process could not be started."""


#: Outcomes meaning "the test did not pass here" — i.e. the probe FIRED.
_FIRED = frozenset({RunOutcome.failed, RunOutcome.collection_error})

#: Outcomes that told us something either way. Everything else is absence of
#: evidence, and absence of evidence never yields a verdict.
_DECISIVE = frozenset({RunOutcome.failed, RunOutcome.collection_error, RunOutcome.passed})

#: pytest exit code -> outcome. See ``pytest.ExitCode``.
_EXIT_CODES = {
    0: RunOutcome.passed,
    1: RunOutcome.failed,
    2: RunOutcome.collection_error,
    3: RunOutcome.error,  # INTERNAL_ERROR
    4: RunOutcome.error,  # USAGE_ERROR
    5: RunOutcome.no_tests,
}

RED_FIRST_VOCABULARY = DiscriminationVocabulary(
    discriminating=(
        "the test did not pass against the merge-base tree and passes against this one — "
        "the RED is recorded"
    ),
    tautology=(
        "the test does not pass against THIS tree either — it is broken, not red-first; "
        "fix the test (or the code) before asking whether it discriminates"
    ),
    inverted=(
        "the test PASSES against the merge-base tree and FAILS against this one — "
        "this change breaks a test that was green"
    ),
    never_established=(
        "the test PASSES unchanged against the merge-base tree — it asserts CURRENT behaviour "
        "rather than REQUIRED behaviour, so it can never have gone RED and it would not have "
        "caught the defect it claims to cover"
    ),
    indecisive=(
        "indecisive run (merge-base={before}, post-change={after}) — the RED could not be "
        "established either way, so this file is reported and not blocked"
    ),
)


class ProofStatus:
    """Per-file disposition."""

    discriminating = "discriminating"
    not_discriminating = "not_discriminating"
    indecisive = "indecisive"
    exempt = "exempt"
    not_applicable = "not_applicable"
    not_proven = "not_proven"
    """Above ``run.max_files``. Named in the report, never silently dropped."""


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass
class RunResult:
    """One pytest execution of one test file, in one state of the tree."""

    outcome: str = RunOutcome.error
    exit_code: int = -1
    duration_ms: int = 0
    summary: str = ""
    """pytest's last line, e.g. ``1 failed in 0.34s`` — the headline evidence."""
    tail: List[str] = field(default_factory=list)
    """Bounded tail of the output. For the merge-base run this IS the RED."""

    @property
    def fired(self) -> bool:
        """True when the test did NOT pass here."""
        return self.outcome in _FIRED

    @property
    def decisive(self) -> bool:
        return self.outcome in _DECISIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "tail": self.tail,
        }


@dataclass
class RedFirstProof:
    """The red-first verdict for one test file.

    Field names mirror :class:`DiscriminationProof` in the prior-art module on
    purpose — same verdict shape, different domain.
    """

    path: str = ""
    status: str = ProofStatus.indecisive
    discriminating: bool = False
    reason: str = ""
    before: RunResult = field(default_factory=RunResult)
    after: RunResult = field(default_factory=RunResult)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "discriminating": self.discriminating,
            "reason": self.reason,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


# --------------------------------------------------------------------------- #
# Repo / config
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str, check: bool = False, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run git against *root*. UTF-8 explicitly — `text=True` decodes with the
    locale encoding, which is cp1252 on a Windows dev box and raises on a
    non-ASCII path byte that git emitted as UTF-8.
    """
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, timeout=timeout, check=check,
        encoding="utf-8", errors="replace",
    )


def repo_root(start: Optional[Path] = None) -> Path:
    """The checkout this gate operates on.

    Resolved from ``__file__``, never ``os.getcwd()``: this runs from CI runners
    that change directory and from git worktrees, and both would answer wrong.
    Prefers git's own answer so a worktree resolves to the worktree, then falls
    back to walking up for ``args/``.
    """
    here = (start or Path(__file__).resolve()).resolve()
    probe = _git(here.parent, "rev-parse", "--show-toplevel")
    if probe.returncode == 0 and probe.stdout.strip():
        return Path(probe.stdout.strip()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_PATH).is_file():
            return candidate
    raise RedFirstError(
        f"could not locate a git checkout or {CONFIG_PATH.as_posix()} above {here} — pass --root"
    )


def load_config(root: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``args/red_first_gate.yaml``. A missing policy is a hard error.

    Deliberately NOT defaulted: a gate that silently invents its own policy when
    its config vanishes is the same "cannot tell a check that ran from one that
    did not" problem this module exists to close.
    """
    root = root or repo_root()
    path = root / CONFIG_PATH
    if not path.is_file():
        raise RedFirstError(f"{path} is missing — the red-first policy cannot be resolved")
    try:
        import yaml  # noqa: PLC0415 - lazy so an import error names the cause
    except ImportError as exc:  # pragma: no cover - pyyaml is a core dependency
        raise RedFirstError(f"pyyaml is required to read {CONFIG_PATH.as_posix()}: {exc}") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RedFirstError(f"{path} did not parse to a mapping")
    return data


def _matches(rel: str, pattern: str) -> bool:
    """Glob match with a recursive ``**`` that behaves the way people expect.

    Identical to ``gated_test_list._matches`` on purpose: two sibling gates that
    disagreed about what a pattern covers would be worse than either.
    """
    if pattern.endswith("/**") and rel.startswith(pattern[:-2]):
        return True
    return fnmatch.fnmatch(rel, pattern)


def in_scope(rel: str, config: Dict[str, Any]) -> bool:
    """True when *rel* is a file this gate considers a test file."""
    scope = config.get("scope") or {}
    roots = [str(r) for r in (scope.get("roots") or ["tests/"])]
    patterns = [str(p) for p in (scope.get("patterns") or ["test_*.py"])]
    rel = rel.replace("\\", "/")
    if not any(rel.startswith(r) for r in roots):
        return False
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def exemption_for(rel: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The exemption stanza covering *rel*, or None.

    An entry with no ``reason`` is ignored — the written reason IS the
    exemption. A bare pattern is a bare count with extra steps.
    """
    for rule in config.get("exemptions") or []:
        if not isinstance(rule, dict):
            continue
        pattern = str(rule.get("pattern", "")).strip()
        reason = str(rule.get("reason", "")).strip()
        if pattern and reason and _matches(rel, pattern):
            return rule
    return None


# --------------------------------------------------------------------------- #
# What changed
# --------------------------------------------------------------------------- #
def resolve_merge_base(root: Path, base_ref: Optional[str] = None) -> Tuple[str, str]:
    """Return ``(merge_base_sha, ref_used)``.

    Raises rather than degrading. A gate that reports "0 files to check" because
    it could not find the base is indistinguishable from a gate that ran and
    found nothing — which is the exact failure this task is about. In CI the
    usual cause is a shallow checkout: the ``test`` job sets ``fetch-depth: 0``.
    """
    candidates: List[str] = []
    if base_ref:
        candidates.append(base_ref)
    else:
        env_base = os.environ.get("GITHUB_BASE_REF", "").strip()
        if env_base:
            candidates.append(f"origin/{env_base}")
        candidates.extend(DEFAULT_BASE_REFS)

    tried: List[str] = []
    for ref in candidates:
        probe = _git(root, "merge-base", "HEAD", ref)
        if probe.returncode == 0 and probe.stdout.strip():
            return probe.stdout.strip(), ref
        tried.append(ref)
    raise RedFirstError(
        "could not resolve a merge base against any of " + ", ".join(tried)
        + " — in CI this means a shallow checkout (set fetch-depth: 0); "
        "locally, fetch the default branch or pass --base"
    )


def changed_test_files(root: Path, merge_base: str, config: Dict[str, Any]) -> List[str]:
    """Test files this branch ADDS or MODIFIES, relative to *merge_base*.

    Compared against the WORKING TREE rather than ``HEAD``, so the same command
    is useful before committing. On a clean CI checkout the two are identical.
    Deletions are excluded: there is no RED to record for a file that is gone.
    """
    probe = _git(root, "diff", "--name-only", "--diff-filter=AMR", merge_base)
    if probe.returncode != 0:
        raise RedFirstError(f"git diff against {merge_base} failed: {probe.stderr.strip()}")
    changed = {line.strip().replace("\\", "/") for line in probe.stdout.splitlines() if line.strip()}
    # `git diff` cannot see an UNTRACKED file, and a brand-new test file is
    # untracked right up until `git add` — which is exactly when a developer
    # wants to run this locally. In CI the tree is clean and this adds nothing.
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if untracked.returncode == 0:
        changed |= {line.strip().replace("\\", "/") for line in untracked.stdout.splitlines() if line.strip()}
    return sorted(rel for rel in changed if in_scope(rel, config))


def code_only_lines(path: Path) -> Optional[Dict[int, str]]:
    """Each line of ``path`` with comments and string literals blanked out.

    A marker must match real CODE. Matching raw text means the word "assert" in
    a docstring makes a file applicable, and then the file is asked a red-first
    question it cannot answer — it added no test logic, so of course it passes
    against the merge base, and the gate reports a fabricated "worthless test".
    That happened: PR #1700 added one autouse fixture whose docstring reads
    "they assert the IDENTITY's tenant", and two files failed the gate on it.

    Tokenising is what separates the two, and it is exact rather than heuristic:
    COMMENT and STRING tokens carry no executable meaning, so they contribute
    nothing. Tokens are written back at their original column, which keeps
    dotted and spaced forms intact for substring matching -- ``pytest.raises``,
    ``self.assertEqual``, ``@pytest.mark.parametrize``, ``def test_foo``.

    Returns None when the file cannot be read or tokenised (a syntax error, a
    partial write). The caller then falls back to raw matching, which over-
    reports applicability rather than under-reporting it: asking a redundant
    question costs a run, skipping a real one lets a worthless test through.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines: Dict[int, List[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in _TEXT_TOKENS:
                continue
            if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            (srow, scol), (erow, _) = tok.start, tok.end
            if srow != erow:  # a multi-line token has no single line to sit on
                continue
            row = lines.setdefault(srow, [])
            if len(row) < scol:
                row.extend(" " * (scol - len(row)))
            row.extend(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None

    return {n: "".join(chars) for n, chars in lines.items()}


def _added_line_numbers(diff: str) -> Set[int]:
    """Line numbers in the NEW file that this unified diff adds."""
    added: Set[int] = set()
    cursor = 0
    for line in diff.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk:
            cursor = int(hunk.group(1))
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.add(cursor)
            cursor += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue
        else:
            cursor += 1
    return added


def added_test_logic(root: Path, merge_base: str, rel: str, config: Dict[str, Any]) -> bool:
    """True when this file's diff ADDS at least one line that looks like a test.

    The applicability gate. Without it, a docstring fix or an import reorder
    inside a test file lands in ``never_established`` and reads as "you wrote a
    worthless test" — an applicability check with no gate is 100% false
    positives, and a gate that cries wolf gets a ``|| true`` bolted onto it.

    Crude and readable on purpose: it decides whether to ASK the question, never
    what the answer is. A brand-new file has every line as an addition, so it is
    always in scope.
    """
    applicability = config.get("applicability") or {}
    if not applicability.get("require_added_test_logic", True):
        return True
    markers = [str(m) for m in (applicability.get("markers") or ["assert"])]
    # Comments and string literals blanked out, so a marker only matches code.
    # None => the file could not be tokenised; fall back to raw text below.
    code = code_only_lines(root / rel)
    probe = _git(root, "diff", "--unified=0", merge_base, "--", rel)
    if probe.returncode != 0:
        # Cannot read the diff -> ask the question. Failing OPEN on applicability
        # would let a real finding through; failing CLOSED only costs a run.
        return True
    if not probe.stdout.strip():
        # No diff at all: the file is UNTRACKED (git diff cannot see it) or was
        # named explicitly via --files. Either way every line is new to the merge
        # base, so read the file rather than concluding "nothing was added".
        if code is not None:
            return any(marker in text
                       for text in code.values() for marker in markers)
        try:
            body = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        return any(marker in body for marker in markers)
    if code is not None:
        return any(marker in code.get(n, "")
                   for n in _added_line_numbers(probe.stdout) for marker in markers)
    for line in probe.stdout.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        if any(marker in body for marker in markers):
            return True
    return False


# --------------------------------------------------------------------------- #
# The merge-base worktree
# --------------------------------------------------------------------------- #
def _worktree_location(root: Path, merge_base: str) -> Path:
    """Where the throwaway merge-base checkout goes.

    Uses the sanctioned ``verify`` actor root so this cannot land inside the
    repo or in a flat shared temp dir where two runs would collide — the same
    rule ``.claude/hooks/pre_tool_use.py::check_worktree_path`` enforces for
    hand-typed worktrees. Keyed on the merge-base sha, so concurrent runs on
    different branches get different directories.
    """
    slug = f"redfirst-{merge_base[:12]}-{os.getpid()}"
    try:
        from tools.git.worktree_paths import worktree_path  # noqa: PLC0415

        return worktree_path("verify", slug)
    except Exception:  # noqa: BLE001 - fall back rather than fail the gate
        import tempfile

        return Path(tempfile.gettempdir()) / "icdev-worktrees" / "verify" / slug


@contextlib.contextmanager
def merge_base_worktree(root: Path, merge_base: str) -> Iterator[Path]:
    """Detached worktree at *merge_base*, removed on the way out."""
    path = _worktree_location(root, merge_base)
    if path.exists():
        _git(root, "worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    made = _git(root, "worktree", "add", "--detach", str(path), merge_base, timeout=600)
    if made.returncode != 0:
        raise RedFirstError(
            f"could not create a merge-base worktree at {path}: {made.stderr.strip()}"
        )
    try:
        yield path
    finally:
        _git(root, "worktree", "remove", "--force", str(path), timeout=300)
        shutil.rmtree(path, ignore_errors=True)
        _git(root, "worktree", "prune")


@contextlib.contextmanager
def only_this_file(worktree: Path, root: Path, rel: str) -> Iterator[None]:
    """Apply ONLY *rel* from the working tree on top of the merge-base checkout.

    The restriction is the experiment: everything else in the worktree stays at
    the merge base, so a pass there means the test asserts something that was
    already true. Reverted afterwards so one worktree serves every file.
    """
    dst = worktree / rel
    existed = dst.exists()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / rel, dst)
    try:
        yield
    finally:
        if existed:
            _git(worktree, "checkout", "--", rel)
        else:
            with contextlib.suppress(OSError):
                dst.unlink()


# --------------------------------------------------------------------------- #
# Running one side of the proof
# --------------------------------------------------------------------------- #
def _pytest_env(tree: Path) -> Dict[str, str]:
    env = os.environ.copy()
    # Absolute, and pointing at THIS tree: the two runs must import the code of
    # the tree they are measuring, not whichever copy happens to be on sys.path.
    env["PYTHONPATH"] = str(tree)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # A leaked ICDEV_DB_PATH from the invoking shell would point both runs at one
    # database, so state written by the first would be visible to the second and
    # the runs would not be independent. tests/conftest.py provisions its own.
    env.pop("ICDEV_DB_PATH", None)
    return env


def _classify(exit_code: int) -> str:
    return _EXIT_CODES.get(exit_code, RunOutcome.error)


def run_test_file(tree: Path, rel: str, config: Dict[str, Any]) -> RunResult:
    """Run one test file inside *tree* and classify the outcome."""
    run_cfg = config.get("run") or {}
    timeout = int(run_cfg.get("timeout_seconds", 600))
    extra = [str(a) for a in (run_cfg.get("pytest_args") or [])]
    captured = int(run_cfg.get("captured_lines", 40))

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", rel, *extra],
            cwd=str(tree), capture_output=True, text=True, timeout=timeout,
            check=False, encoding="utf-8", errors="replace", env=_pytest_env(tree),
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            outcome=RunOutcome.timeout,
            exit_code=-1,
            duration_ms=int((time.monotonic() - started) * 1000),
            summary=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return RunResult(
            outcome=RunOutcome.error,
            exit_code=-1,
            duration_ms=int((time.monotonic() - started) * 1000),
            summary=f"could not start pytest: {exc}",
        )

    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return RunResult(
        outcome=_classify(proc.returncode),
        exit_code=proc.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        summary=lines[-1][:300] if lines else "",
        tail=[line[:300] for line in lines[-captured:]],
    )


# --------------------------------------------------------------------------- #
# The proof
# --------------------------------------------------------------------------- #
def prove(
    root: Path,
    worktree: Path,
    rel: str,
    config: Dict[str, Any],
) -> RedFirstProof:
    """Run *rel* at the merge base and here, and decide whether it discriminates."""
    with only_this_file(worktree, root, rel):
        before = run_test_file(worktree, rel, config)
    after = run_test_file(root, rel, config)

    discriminating, reason = decide_discrimination(
        before_fired=before.fired,
        before_decisive=before.decisive,
        before_outcome=before.outcome,
        after_fired=after.fired,
        after_decisive=after.decisive,
        after_outcome=after.outcome,
        vocabulary=RED_FIRST_VOCABULARY,
    )
    if discriminating:
        status = ProofStatus.discriminating
    elif before.decisive and after.decisive:
        status = ProofStatus.not_discriminating
    else:
        status = ProofStatus.indecisive

    return RedFirstProof(
        path=rel, status=status, discriminating=discriminating,
        reason=reason, before=before, after=after,
    )


def _finding_message(proof: RedFirstProof) -> str:
    return (
        f"{proof.path}: {proof.reason}. "
        f"merge-base run: {proof.before.outcome} (exit {proof.before.exit_code}) "
        f"{proof.before.summary!r}; this tree: {proof.after.outcome} "
        f"(exit {proof.after.exit_code}) {proof.after.summary!r}. "
        "Reproduce locally with `python tools/ci/red_first_gate.py --files "
        f"{proof.path}`. If a red-first proof is genuinely the wrong question for "
        "this file, add it to args/red_first_gate.yaml -> exemptions WITH A "
        "WRITTEN REASON; do not switch mode to advisory to get a commit through"
    )


def enforce(
    root: Optional[Path] = None,
    *,
    base_ref: Optional[str] = None,
    files: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Prove every changed test file went RED first. Returns the report.

    Raises :class:`RedFirstError` only for a harness failure (no merge base, no
    policy, no worktree). A per-file indecisive result is reported, never raised
    and never blocking: we learned nothing, and saying so out loud is the
    difference between this gate and the mandate it replaces.
    """
    root = root or repo_root()
    config = config if config is not None else load_config(root)
    merge_base, ref_used = resolve_merge_base(root, base_ref)

    if files is not None:
        subjects = sorted({str(f).replace("\\", "/") for f in files})
    else:
        subjects = changed_test_files(root, merge_base, config)

    proofs: List[RedFirstProof] = []
    to_run: List[str] = []
    for rel in subjects:
        exemption = exemption_for(rel, config)
        if exemption is not None:
            proofs.append(RedFirstProof(
                path=rel, status=ProofStatus.exempt,
                reason=f"exempt via {exemption['pattern']!r}: "
                       + " ".join(str(exemption["reason"]).split()),
            ))
            continue
        if not (root / rel).is_file():
            proofs.append(RedFirstProof(
                path=rel, status=ProofStatus.indecisive,
                reason="named but not present in the working tree — nothing to run",
            ))
            continue
        if not added_test_logic(root, merge_base, rel, config):
            proofs.append(RedFirstProof(
                path=rel, status=ProofStatus.not_applicable,
                reason="the diff for this file adds no assertion or test function, "
                       "so there is no new RED to record",
            ))
            continue
        to_run.append(rel)

    max_files = int((config.get("run") or {}).get("max_files", 25))
    deferred = to_run[max_files:]
    to_run = to_run[:max_files]
    for rel in deferred:
        proofs.append(RedFirstProof(
            path=rel, status=ProofStatus.not_proven,
            reason=f"above run.max_files ({max_files}) — NOT proven, and named here "
                   "rather than dropped so the report cannot read as full coverage",
        ))

    if to_run:
        with merge_base_worktree(root, merge_base) as worktree:
            for rel in to_run:
                proofs.append(prove(root, worktree, rel, config))

    proofs.sort(key=lambda p: p.path)

    # Staleness is measured against the whole tree, not the changed set: an
    # exemption only ever matches a handful of PRs, so "matched nothing today"
    # would flag every healthy entry.
    tracked = _tracked_test_files(root, config)
    stale_exemptions = sorted(
        str(rule.get("pattern"))
        for rule in (config.get("exemptions") or [])
        if isinstance(rule, dict) and str(rule.get("pattern", "")).strip()
        and not any(_matches(f, str(rule["pattern"])) for f in tracked)
    )

    findings = [p for p in proofs if p.status == ProofStatus.not_discriminating]
    return {
        "ran": True,
        "mode": str(config.get("mode", "enforce")),
        "base_ref": ref_used,
        "merge_base": merge_base,
        "subjects": len(subjects),
        "proven": sum(1 for p in proofs if p.status in
                      (ProofStatus.discriminating, ProofStatus.not_discriminating)),
        "discriminating": sum(1 for p in proofs if p.status == ProofStatus.discriminating),
        "not_discriminating": len(findings),
        "indecisive": sum(1 for p in proofs if p.status == ProofStatus.indecisive),
        "exempt": sum(1 for p in proofs if p.status == ProofStatus.exempt),
        "not_applicable": sum(1 for p in proofs if p.status == ProofStatus.not_applicable),
        "not_proven": [p.path for p in proofs if p.status == ProofStatus.not_proven],
        "stale_exemptions": stale_exemptions,
        "proofs": [p.to_dict() for p in proofs],
        "errors": [_finding_message(p) for p in findings],
        "ok": not findings,
    }


def _tracked_test_files(root: Path, config: Dict[str, Any]) -> List[str]:
    probe = _git(root, "ls-files")
    if probe.returncode != 0:
        return []
    return [
        line.strip().replace("\\", "/")
        for line in probe.stdout.splitlines()
        if line.strip() and in_scope(line.strip().replace("\\", "/"), config)
    ]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_human(report: Dict[str, Any]) -> None:
    print(
        f"Red-first proof vs {report['base_ref']} ({report['merge_base'][:12]}): "
        f"{report['subjects']} changed test file(s) — "
        f"{report['discriminating']} discriminating, "
        f"{report['not_discriminating']} not, "
        f"{report['indecisive']} indecisive, "
        f"{report['exempt']} exempt, "
        f"{report['not_applicable']} not applicable."
    )
    for proof in report["proofs"]:
        print(f"  [{proof['status']}] {proof['path']}: {proof['reason']}")
        # The recorded RED. Printed for the discriminating case too — the whole
        # point of this task is that the evidence exists in a readable form, not
        # only that the verdict does.
        if proof["status"] in (ProofStatus.discriminating, ProofStatus.not_discriminating):
            print(f"      merge-base: {proof['before']['outcome']} "
                  f"(exit {proof['before']['exit_code']}) {proof['before']['summary']}")
            print(f"      this tree:  {proof['after']['outcome']} "
                  f"(exit {proof['after']['exit_code']}) {proof['after']['summary']}")
        if proof["status"] == ProofStatus.not_discriminating:
            for line in proof["before"]["tail"]:
                print(f"      | {line}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--root", type=Path, help="repository root (default: derived from git)")
    parser.add_argument("--base", help="base ref to merge-base against (default: origin/main)")
    parser.add_argument("--files", nargs="+", help="prove these paths instead of the PR diff")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 on a non-discriminating test, 2 if the gate could not run")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    parser.add_argument("--out", type=Path, help="write the JSON proof to a file (the artifact)")
    args = parser.parse_args(argv)

    # LF on every platform; see gated_test_list.main for the CRLF scar.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")  # type: ignore[union-attr]

    try:
        root = args.root.resolve() if args.root else repo_root()
        report = enforce(root, base_ref=args.base, files=args.files)
    except RedFirstError as exc:
        # Exit 2, not 1, and never 0. A gate that cannot run is not a gate that
        # found nothing, and the log must be able to tell them apart.
        print(f"::error::red-first gate could not run: {exc}", file=sys.stderr)
        return 2 if args.gate else 0

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    for pattern in report["stale_exemptions"]:
        print(f"::warning::red-first exemption {pattern!r} matches no test file in the tree — "
              "delete it or fix the pattern")
    for path in report["not_proven"]:
        print(f"::warning::red-first proof NOT RUN for {path} — above run.max_files")
    for proof in report["proofs"]:
        if proof["status"] == ProofStatus.indecisive:
            print(f"::warning::red-first proof indecisive for {proof['path']}: {proof['reason']}")

    if not args.gate:
        return 0

    advisory = str(report["mode"]).lower() != "enforce"
    for err in report["errors"]:
        prefix = "ADVISORY: " if advisory else ""
        stream = sys.stdout if advisory else sys.stderr
        print(f"::error::{prefix}red-first proof: {err}", file=stream)
    if report["ok"] or advisory:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
