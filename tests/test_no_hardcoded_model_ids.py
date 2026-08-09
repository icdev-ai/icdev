# CUI // SP-CTI
"""Gate: no hardcoded provider model IDs in ``tools/`` (hgx-port-01).

CLAUDE.md — "LLM config via .env, never hardcode model IDs in Python."

A literal such as ``model="claude-haiku-4-5-20251001"`` pins one vendor into
Python. Every one of these call sites lives inside ``except Exception: pass``,
so on an air-gapped or non-Anthropic deployment the call fails and the feature
degrades **silently**. The fix is always the same: route by ``llm_function``
through ``LLMRouter`` and declare that function's chain in
``args/llm_config.yaml``, so the deployment's own chain serves it.

Detection is AST-based, not textual. Only a string actually bound to a model
selector counts:

* a ``model=`` / ``model_id=`` keyword argument on a call,
* a ``{"model": "..."}`` / ``{"model_id": "..."}`` dict entry,
* an assignment to a name like ``model`` / ``MODEL_ID`` / ``DEFAULT_MODEL``.

A model name in a docstring, comment, log message, or seed-task description is
not a finding — those are prose, not routing.

Policy lives in ``args/model_id_gate.yaml``:

* ``exempt_paths`` — modules that name models by design (provider adapters,
  pricing tables, demo catalogs).
* ``grandfathered`` — pre-existing violations with an allowed count per file.
  Adding a new literal to one of those files pushes it over its count and
  fails. Do not add entries to get a commit through.

Both source trees are scanned; ``icdev/tools/x.py`` is normalised to
``tools/x.py`` so the mirror is held to the same policy as the original.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

# Repo root from __file__, never os.getcwd() — tests may be launched from a
# worktree or a subdirectory (CLAUDE.md, "Notes for agents working from
# worktrees").
REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "args" / "model_id_gate.yaml"

SOURCE_TREES = ("tools", "icdev/tools")

# Names that select a model. `executor="claude-cli"` is deliberately not one of
# them — that names a CLI executor, not a model.
MODEL_SELECTOR_NAMES = frozenset({"model", "model_id", "model_name"})

# Vendor model IDs. Anchored on the vendor prefix + a version-ish first
# segment so `claude-cli` (an executor) and `gpt-partner` (prose) do not match,
# while `claude-haiku-4-5-20251001`, `gpt-4o-mini`, `gemini-2.5-flash` and
# `o3-mini` do.
MODEL_ID_RE = re.compile(
    r"^(?:"
    r"claude-(?:opus|sonnet|haiku|fable|instant|[0-9])[\w.\-]*"
    r"|gpt-(?:[0-9]|o[0-9]?)[\w.\-]*"
    r"|gemini-[0-9][\w.\-]*"
    r"|o[134]-(?:mini|preview|pro)[\w.\-]*"
    r")$"
)

# Files this gate was created for — the kanban pipeline's LLM call sites, in
# ``tools.genesis.reflexes.kanban`` and ``tools.dashboard.api.kanban``. Both are
# clean as of hgx-port-01; zero tolerance, no grandfathering.
ZERO_TOLERANCE = (
    "tools/genesis/reflexes/kanban.py",
    "tools/dashboard/api/kanban.py",
)


def _normalize(path: Path) -> str:
    """Repo-relative POSIX path, with the icdev/ mirror folded onto tools/."""
    rel = path.resolve().relative_to(REPO_ROOT).as_posix()
    return rel[len("icdev/"):] if rel.startswith("icdev/tools/") else rel


def _load_gate() -> tuple[list[str], dict[str, int]]:
    data = yaml.safe_load(GATE_PATH.read_text(encoding="utf-8")) or {}
    exempt = list(data.get("exempt_paths") or [])
    grandfathered = {str(k): int(v) for k, v in (data.get("grandfathered") or {}).items()}
    return exempt, grandfathered


def _is_exempt(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def _model_literal(node: ast.AST) -> str | None:
    """Return the model ID if ``node`` is a str constant naming one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if MODEL_ID_RE.match(node.value.strip()):
            return node.value.strip()
    return None


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    return []


def scan_source(source: str, rel: str) -> list[tuple[int, str]]:
    """Find model-selector literals in one module. Returns (lineno, model_id)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a broken file is another test's job
        return []

    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # model="claude-..." / model_id="gpt-..." keyword arguments
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in MODEL_SELECTOR_NAMES:
                    found = _model_literal(kw.value)
                    if found:
                        findings.append((kw.value.lineno, found))
        # {"model": "claude-..."} dict entries
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in MODEL_SELECTOR_NAMES
                ):
                    found = _model_literal(value)
                    if found:
                        findings.append((value.lineno, found))
        # MODEL = "claude-..." / self.model = "gpt-..."
        elif isinstance(node, ast.Assign):
            names = [n for t in node.targets for n in _target_names(t)]
            if any(n.lower().rstrip("s").endswith(("model", "model_id")) for n in names):
                found = _model_literal(node.value)
                if found:
                    findings.append((node.value.lineno, found))
        elif isinstance(node, ast.AnnAssign):
            names = _target_names(node.target)
            if node.value is not None and any(
                n.lower().rstrip("s").endswith(("model", "model_id")) for n in names
            ):
                found = _model_literal(node.value)
                if found:
                    findings.append((node.value.lineno, found))

    return findings


def collect_findings() -> dict[str, list[tuple[str, int, str]]]:
    """Scan both trees. Returns normalized_rel -> [(actual_rel, lineno, id)]."""
    exempt, _ = _load_gate()
    findings: dict[str, list[tuple[str, int, str]]] = {}

    for tree_name in SOURCE_TREES:
        tree_root = REPO_ROOT / tree_name
        if not tree_root.is_dir():
            continue
        for path in sorted(tree_root.rglob("*.py")):
            actual = path.resolve().relative_to(REPO_ROOT).as_posix()
            rel = _normalize(path)
            if _is_exempt(rel, exempt):
                continue
            try:
                source = path.read_text(encoding="utf-8", newline="")
            except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
            for lineno, model_id in scan_source(source, rel):
                findings.setdefault(rel, []).append((actual, lineno, model_id))

    return findings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_gate_file_is_wellformed():
    """The gate config parses and has the two expected sections."""
    exempt, grandfathered = _load_gate()
    assert isinstance(exempt, list) and exempt, "exempt_paths must be a non-empty list"
    assert isinstance(grandfathered, dict), "grandfathered must be a mapping"
    assert all(v >= 1 for v in grandfathered.values()), "grandfather counts must be >= 1"
    for rel in grandfathered:
        assert rel.startswith("tools/"), f"grandfather keys are tools/-relative: {rel}"
        assert rel not in ZERO_TOLERANCE, (
            f"{rel} is zero-tolerance and must never be grandfathered"
        )


def test_kanban_reflex_has_no_hardcoded_model_ids():
    """hgx-port-01: the pinned literals are gone from BOTH tree copies.

    Covers ``tools.genesis.reflexes.kanban`` (the five sites named in the card:
    timeout-hint extraction, gap-subject extraction, resume-at parsing,
    acceptance judging and triage decomposition) and ``tools.dashboard.api.kanban``
    (task_specify + acceptance_judge, the same defect in the board's HTTP API).

    Zero tolerance — these files are not eligible for grandfathering. If a
    literal comes back, this fails.
    """
    offenders: list[str] = []
    for rel in ZERO_TOLERANCE:
        for tree_name in SOURCE_TREES:
            path = REPO_ROOT / (rel if tree_name == "tools" else f"icdev/{rel}")
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8", newline="")
            for lineno, model_id in scan_source(source, rel):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {model_id}"
                )

    assert not offenders, (
        "Hardcoded model ID(s) in a zero-tolerance module:\n  "
        + "\n  ".join(offenders)
        + "\n\nRoute the call instead: drop the `model=` argument and declare the "
        "llm_function's chain in args/llm_config.yaml."
    )


def test_routed_kanban_functions_are_declared():
    """The five reflex call sites route through functions declared in config.

    Without a declaration the router silently falls back to `routing.default`,
    which is neither cheap nor local-first — the point of the fix is that the
    deployment's own chain serves these, including when it is pointed at Ollama.
    """
    config = yaml.safe_load(
        (REPO_ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8")
    )
    routing = config.get("routing") or {}
    models = config.get("models") or {}

    for function in (
        "timeout_extraction",
        "gap_subject_extraction",
        "resume_at_extraction",
        "acceptance_judge",
        "triage_decomposition",
        "task_specify",
    ):
        assert function in routing, (
            f"routing.{function} is not declared in args/llm_config.yaml"
        )
        chain = routing[function].get("chain") or []
        assert chain, f"routing.{function}.chain is empty"
        unknown = [m for m in chain if m not in models]
        assert not unknown, f"routing.{function}.chain names unknown model(s): {unknown}"
        # A local model must be reachable so an air-gapped / Ollama-only
        # deployment can serve the call rather than failing into the silent
        # `except Exception: pass`.
        assert any(m.endswith("-local") for m in chain), (
            f"routing.{function}.chain has no local fallback: {chain}"
        )


def test_no_new_hardcoded_model_ids_in_tools():
    """No model literal outside the exempt paths and the grandfathered budget."""
    _, grandfathered = _load_gate()
    findings = collect_findings()

    counts = Counter({rel: len(hits) for rel, hits in findings.items()})
    violations: list[str] = []

    for rel, count in sorted(counts.items()):
        # A file appears once per tree, so the budget is per-tree occurrences.
        trees_present = len({hit[0].startswith("icdev/") for hit in findings[rel]})
        allowed = grandfathered.get(rel, 0) * max(1, trees_present)
        if count > allowed:
            for actual, lineno, model_id in findings[rel]:
                violations.append(f"{actual}:{lineno}: {model_id}")

    assert not violations, (
        "Hardcoded model ID(s) bound to a model selector in tools/:\n  "
        + "\n  ".join(violations)
        + "\n\nCLAUDE.md forbids pinning a model in Python. Route the call through "
        "LLMRouter by llm_function and declare the chain in args/llm_config.yaml. "
        "If the literal is genuinely data (a provider adapter, a pricing table, a "
        "demo catalog), add the module to `exempt_paths` in args/model_id_gate.yaml "
        "with a reason — do not raise a grandfather count."
    )


def test_gate_has_no_stale_grandfather_entries():
    """A grandfathered file that is now clean must be removed from the gate.

    Keeps the list shrinking: once a pin is routed, its entry has to go, so the
    gate cannot quietly re-admit it later.
    """
    _, grandfathered = _load_gate()
    findings = collect_findings()
    stale = [rel for rel in grandfathered if rel not in findings]
    assert not stale, (
        "args/model_id_gate.yaml grandfathers file(s) that no longer violate — "
        f"remove them: {stale}"
    )


def test_detector_would_catch_a_regression():
    """The detector actually fires — guards against a gate that can never fail."""
    regressed = (
        "from tools.llm.provider import LLMRequest\n"
        "req = LLMRequest(\n"
        "    messages=[],\n"
        '    model="claude-haiku-4-5-20251001",\n'
        ")\n"
    )
    assert scan_source(regressed, "tools/genesis/reflexes/kanban.py") == [
        (4, "claude-haiku-4-5-20251001")
    ]

    # ...and does not fire on prose or on a non-model selector.
    benign = (
        '"""Docstring mentioning claude-haiku-4-5-20251001 and gpt-4o."""\n'
        '# comment: gemini-2.5-flash\n'
        'dispatch(executor="claude-cli")\n'
        'log("routed to claude-sonnet-4-6")\n'
    )
    assert scan_source(benign, "tools/example.py") == []


@pytest.mark.parametrize(
    "value,expected",
    [
        ("claude-haiku-4-5-20251001", True),
        ("claude-sonnet-4-6", True),
        ("gpt-4o-mini", True),
        ("gemini-2.5-flash", True),
        ("o3-mini", True),
        ("claude-cli", False),
        ("claude-haiku", True),
        ("qwen3-local", False),
        ("kimi-cloud", False),
        ("", False),
    ],
)
def test_model_id_pattern(value, expected):
    assert bool(MODEL_ID_RE.match(value)) is expected
