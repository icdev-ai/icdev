#!/usr/bin/env python3
# CUI // SP-CTI
"""Single-toggle isolation for retrieval benchmarking (oss-meas-01-d2).

``oss-meas-01`` asks for a KEEP/DROP decision per retrieval toggle, backed by
numbers. Producing a number is the easy half. The hard half is that a toggle
which is **not wired to anything** also measures as zero, and a report that
cannot tell those apart launders dead code into an evidence-backed "DROP — no
measurable benefit".

Three of the five toggles named in the task are in exactly that state: the
modules behind ``reflective_rerank``, ``adaptive_routing`` and ``auto_indexer``
are imported by nothing outside their own tests. So this harness refuses to
report a delta for a toggle it cannot first prove is reachable.

Two independent things happen per toggle:

1. :func:`probe_reachability` — static import-closure walk from the retrieval
   entry point. Answers "could flipping this possibly change retrieval?"
2. :func:`isolated_config` — writes a temp ``rag_config.yaml`` with exactly one
   toggle changed and points ``ICDEV_RAG_CONFIG`` at it, so a run attributes its
   delta to that toggle and nothing else.

The shared committed ``args/rag_config.yaml`` is never written. Several agent
sessions and the kanban scheduler share this checkout; a benchmark that edits
the real config races them and leaves the tree dirty if it dies mid-run.

Usage::

    from tools.rag.toggle_harness import TOGGLES, isolated_config, probe_reachability

    for name in TOGGLES:
        verdict = probe_reachability(name)
        if not verdict.reachable:
            continue                      # NOT-WIRED — do not fabricate a number
        with isolated_config(name, True):
            result = RAGBenchmark().run()
"""
from __future__ import annotations

import ast
import contextlib
import copy
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import yaml

from tools.rag.config_path import CONFIG_ENV_VAR, rag_config_path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RAG_PKG = BASE_DIR / "tools" / "rag"

#: The retrieval entry point. Reachability is "can you get here from there".
ENTRY_MODULE = "tools.rag.retriever"


@dataclass(frozen=True)
class Toggle:
    """One measurable retrieval switch.

    Attributes:
        name: Short handle used on the CLI.
        path: Dotted path into ``rag_config.yaml``, e.g. ``rag.rerank.enabled``.
        consumer: Module that reads the toggle. Reachability is decided by
            whether this module is in the import closure of :data:`ENTRY_MODULE`.
        note: Why it exists / what it does, for the report.
        retrieval_side: False for ingest-time switches, which cannot move a
            retrieval metric even when wired.
    """

    name: str
    path: str
    consumer: str
    note: str
    retrieval_side: bool = True


#: The five toggles oss-meas-01 names, plus raptor for contrast (already
#: measured as a regression in rce-eval-05 — kept so the sweep reproduces it
#: rather than taking the prior result on faith).
TOGGLES: Dict[str, Toggle] = {
    t.name: t
    for t in (
        Toggle(
            "rerank",
            "rag.rerank.enabled",
            "tools.rag.reranker",
            "Cross-encoder reranking of the post-vector candidate set (BGE + LLM providers).",
        ),
        Toggle(
            "reflective_rerank",
            "rag.reflective_rerank.enabled",
            "tools.rag.reflective_reranker",
            "Self-RAG per-document reflection over RELEVANT/SUPPORTS/USEFUL axes (agx-rag-02).",
        ),
        Toggle(
            "adaptive_routing",
            "rag.adaptive_routing.enabled",
            "tools.rag.adaptive_router",
            "Query-complexity pre-routing to a cheaper or richer retrieval path (agx-rag-01).",
        ),
        Toggle(
            "binary_prefilter",
            "rag.quantization.binary_prefilter.enabled",
            "tools.rag.sqlite_vector_store",
            "Binary-quantised prefilter that shrinks the candidate set before exact scoring.",
        ),
        Toggle(
            "auto_indexer",
            "rag.auto_indexer.enabled",
            "tools.rag.auto_indexer",
            "Filesystem watcher that indexes changed files automatically.",
            retrieval_side=False,
        ),
        Toggle(
            "raptor",
            "rag.raptor.enabled",
            "tools.rag.raptor",
            "Multi-level summary tree retrieval. Measured as a REGRESSION in rce-eval-05.",
        ),
    )
}


# ── Reachability ──────────────────────────────────────────────────────────────


@dataclass
class Reachability:
    """Whether a toggle's consumer can be reached from the retrieval entry point."""

    toggle: str
    reachable: bool
    consumer: str
    reason: str
    importers: List[str] = field(default_factory=list)
    retrieval_side: bool = True

    @property
    def verdict(self) -> str:
        if not self.reachable:
            return "NOT-WIRED"
        return "WIRED" if self.retrieval_side else "WIRED-INGEST-ONLY"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "toggle": self.toggle,
            "verdict": self.verdict,
            "reachable": self.reachable,
            "consumer": self.consumer,
            "reason": self.reason,
            "importers": self.importers,
            "retrieval_side": self.retrieval_side,
        }


def _module_to_path(module: str) -> Optional[Path]:
    """Map ``tools.rag.x`` to its file, or None when it is not a tools module."""
    if not module.startswith("tools."):
        return None
    candidate = BASE_DIR / Path(*module.split("."))
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imports_of(path: Path) -> Set[str]:
    """Every ``tools.*`` module imported by *path*, including inside functions.

    Deferred imports are the norm in this package (``from tools.rag.reranker
    import rerank_results`` sits inside the branch that uses it), so a top-level
    scan would miss the wiring that actually exists and report every toggle as
    dead. ``ast.walk`` covers both.
    """
    found: Set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tools."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative import — not used in this tree
                continue
            mod = node.module or ""
            if not mod.startswith("tools."):
                continue
            found.add(mod)
            # `from tools.rag import reranker` -> tools.rag.reranker
            for alias in node.names:
                found.add(f"{mod}.{alias.name}")
    return found


def import_closure(entry: str = ENTRY_MODULE, max_depth: int = 6) -> Dict[str, List[str]]:
    """Transitive ``tools.*`` import closure from *entry*.

    Returns ``{module: [modules that import it]}``. Depth-bounded because the
    closure otherwise pulls in most of ``tools/`` through the storage layer, and
    a toggle reachable only via a 10-hop path through the audit logger is not
    meaningfully "on the retrieval path" anyway.
    """
    importers: Dict[str, List[str]] = {}
    seen: Set[str] = set()
    frontier = [(entry, 0)]

    while frontier:
        module, depth = frontier.pop()
        if module in seen or depth > max_depth:
            continue
        seen.add(module)
        path = _module_to_path(module)
        if path is None:
            continue
        for imported in _imports_of(path):
            importers.setdefault(imported, []).append(module)
            if imported not in seen:
                frontier.append((imported, depth + 1))
    return importers


def probe_reachability(
    name: str, closure: Optional[Dict[str, List[str]]] = None
) -> Reachability:
    """Decide whether flipping *name* could change retrieval at all.

    This is the check that keeps a benchmark honest. A toggle whose consumer no
    module imports produces the same zero delta as a toggle that is wired and
    useless — and only one of those means "delete the config key".
    """
    toggle = TOGGLES[name]
    closure = closure if closure is not None else import_closure()
    importers = sorted(set(closure.get(toggle.consumer, [])))

    if not importers:
        return Reachability(
            toggle=name,
            reachable=False,
            consumer=toggle.consumer,
            reason=(
                f"{toggle.consumer} is not in the import closure of {ENTRY_MODULE}; "
                "flipping this toggle cannot change retrieval. Benchmarking it "
                "would report a zero delta that means 'not connected', not "
                "'no benefit'."
            ),
            retrieval_side=toggle.retrieval_side,
        )

    if not toggle.retrieval_side:
        return Reachability(
            toggle=name,
            reachable=True,
            consumer=toggle.consumer,
            reason=(
                "Ingest-side switch: wired, but it changes what gets indexed, not "
                "how a query is served. A retrieval benchmark cannot score it."
            ),
            importers=importers,
            retrieval_side=False,
        )

    return Reachability(
        toggle=name,
        reachable=True,
        consumer=toggle.consumer,
        reason=f"imported by {', '.join(importers)}",
        importers=importers,
        retrieval_side=True,
    )


def probe_all() -> List[Reachability]:
    """Reachability for every toggle, computed against one shared closure."""
    closure = import_closure()
    return [probe_reachability(name, closure) for name in TOGGLES]


# ── Isolation ─────────────────────────────────────────────────────────────────


def _set_path(cfg: dict, dotted: str, value: Any) -> None:
    """Set ``a.b.c`` in *cfg*, creating intermediate dicts."""
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def get_path(cfg: dict, dotted: str) -> Any:
    """Read ``a.b.c`` from *cfg*, or None when any hop is missing."""
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def load_base_config() -> dict:
    """The committed config, as the baseline every isolated run varies from."""
    path = rag_config_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_isolated_cfg(
    name: Optional[str], value: Any = True, base: Optional[dict] = None
) -> dict:
    """Config with *every* toggle written explicitly and only *name* enabled.

    Writing all of them — not just the one under test — is what makes a run
    reproducible. Inheriting the others from whatever the file happens to say
    means a default flipped between two arms silently becomes part of the
    measured delta, and nothing in the output would show it. ``name=None``
    builds the all-off control.

    Adopted from PR #820, which got this right where the first cut of this
    module did not.
    """
    cfg = copy.deepcopy(base if base is not None else load_base_config())
    for toggle_name, spec in TOGGLES.items():
        _set_path(cfg, spec.path, value if toggle_name == name else False)
    return cfg


#: Loaders to patch in-process, as ``(module suffix, attribute, factory)``.
#: These are the resolution points the retrieval path actually calls; patching
#: the function rather than the file also covers modules that have not adopted
#: ``config_path.rag_config_path()``.
_PATCH_TARGETS = (
    ("rag.retriever", "_load_rag_config", "full"),
    ("rag.vector_store_factory", "_load_rag_config", "full"),
    ("rag.sqlite_vector_store", "_load_quantization_config", "quantization"),
)


@contextlib.contextmanager
def _patched_loaders(cfg: dict) -> Iterator[None]:
    """Point already-imported retrieval loaders at *cfg* for the duration.

    Belt to the env var's braces. ``ICDEV_RAG_CONFIG`` only reaches modules that
    call :func:`rag_config_path`, and 13 modules under ``tools/rag/`` resolve the
    path themselves. For an in-process run the loader patch closes that gap.

    Both ``tools.*`` and ``icdev.tools.*`` module objects are patched when
    already imported: under the compat shim they are **distinct module objects**,
    and patching one leaves the other serving on-disk defaults. Adopted from
    PR #820.
    """
    quant = (cfg.get("rag") or {}).get("quantization", {}) or {}
    saved: List[Any] = []
    try:
        for suffix, attr, scope in _PATCH_TARGETS:
            payload = quant if scope == "quantization" else cfg
            for root in ("tools.", "icdev.tools."):
                mod = sys.modules.get(root + suffix)
                if mod is None or not hasattr(mod, attr):
                    continue
                saved.append((mod, attr, getattr(mod, attr)))
                # default arg binds payload per-iteration
                setattr(mod, attr, lambda *a, _p=payload, **k: copy.deepcopy(_p))
        yield
    finally:
        for mod, attr, original in reversed(saved):
            setattr(mod, attr, original)


def import_retrieval_modules() -> None:
    """Import the retrieval modules so their loaders exist to be patched.

    Best-effort: a module that cannot import (missing optional backend) is
    simply not patched, and the run degrades to the zeroed-baseline path rather
    than aborting the sweep.
    """
    import importlib

    for suffix, _attr, _scope in _PATCH_TARGETS:
        with contextlib.suppress(Exception):
            importlib.import_module("tools." + suffix)


@contextlib.contextmanager
def isolated_config(
    name: Optional[str],
    value: Any = True,
    base: Optional[dict] = None,
) -> Iterator[Path]:
    """Run a block with exactly one toggle on and every other one off.

    Three layers, because no single one covers every consumer:

    1. Every toggle is written explicitly (:func:`build_isolated_cfg`), so an
       arm cannot inherit ambient state from the committed file.
    2. A temp ``rag_config.yaml`` + ``ICDEV_RAG_CONFIG`` — survives into
       subprocesses and modules that adopted :func:`rag_config_path`.
    3. In-process loader patches (:func:`_patched_loaders`) — covers the
       modules that resolve the path themselves, on both sides of the
       ``tools.``/``icdev.tools.`` shim.

    ``name=None`` yields the all-off control, built and applied through the
    identical path as every variant.

    The committed ``args/rag_config.yaml`` is never written — this checkout is
    shared with other agent sessions and the kanban scheduler.

    Yields:
        Path to the temp config in force.
    """
    cfg = build_isolated_cfg(name, value, base)

    previous = os.environ.get(CONFIG_ENV_VAR)
    tmp_dir = tempfile.mkdtemp(prefix="icdev-rag-toggle-")
    tmp_path = Path(tmp_dir) / "rag_config.yaml"
    try:
        tmp_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        os.environ[CONFIG_ENV_VAR] = str(tmp_path)
        import_retrieval_modules()
        with _patched_loaders(cfg):
            yield tmp_path
    finally:
        if previous is None:
            os.environ.pop(CONFIG_ENV_VAR, None)
        else:
            os.environ[CONFIG_ENV_VAR] = previous
        with contextlib.suppress(OSError):
            tmp_path.unlink()
            Path(tmp_dir).rmdir()


def verify_isolation(name: str) -> Dict[str, Any]:
    """Confirm the override actually reaches the loader, and changes only *name*.

    Guards the failure this whole module exists to prevent: a run that reports a
    delta of zero because the toggle never actually flipped.
    """
    from tools.rag.retriever import _load_rag_config

    toggle = TOGGLES[name]
    base = load_base_config()
    before = get_path(_load_rag_config(), toggle.path)
    with isolated_config(name, True, base=base):
        during = get_path(_load_rag_config(), toggle.path)
        seen_cfg = _load_rag_config()
    after = get_path(_load_rag_config(), toggle.path)

    # every other toggle must be OFF (they are written explicitly now, so the
    # check is "off", not "same as the committed file")
    bled: List[str] = []
    for other, spec in TOGGLES.items():
        if other == name:
            continue
        if get_path(seen_cfg, spec.path) is not False:
            bled.append(other)

    return {
        "toggle": name,
        "path": toggle.path,
        "before": before,
        "during": during,
        "after": after,
        "override_applied": during is True,
        "restored": before == after,
        "isolated": not bled,
        "bled_into": bled,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Retrieval toggle isolation + reachability probe (oss-meas-01-d2)"
    )
    parser.add_argument("--probe", action="store_true",
                        help="Report WIRED / NOT-WIRED per toggle and exit")
    parser.add_argument("--verify", metavar="TOGGLE",
                        help="Prove the override reaches the loader for one toggle")
    parser.add_argument("--list", action="store_true", help="List known toggles")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args(argv)

    if args.list:
        payload = [
            {"name": t.name, "path": t.path, "consumer": t.consumer, "note": t.note}
            for t in TOGGLES.values()
        ]
        print(json.dumps(payload, indent=2) if args.json_out
              else "\n".join(f"{p['name']:18s} {p['path']:40s} {p['consumer']}" for p in payload))
        return 0

    if args.verify:
        if args.verify not in TOGGLES:
            print(f"unknown toggle {args.verify!r}; known: {', '.join(TOGGLES)}", file=sys.stderr)
            return 2
        result = verify_isolation(args.verify)
        print(json.dumps(result, indent=2) if args.json_out else result)
        return 0 if result["override_applied"] and result["restored"] and result["isolated"] else 1

    if args.probe or True:
        results = probe_all()
        if args.json_out:
            print(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            for r in results:
                print(f"{r.verdict:18s} {r.toggle:18s} {r.reason}")
            wired = sum(1 for r in results if r.reachable and r.retrieval_side)
            print(f"\n{wired}/{len(results)} toggles are measurable by a retrieval benchmark.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
