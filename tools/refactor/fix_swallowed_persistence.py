#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Rewrite ``except Exception: pass`` around persistence writes into logged best-effort handlers.

A ``try`` block that performs an ``INSERT`` and whose handler body is exactly
``pass`` is a write that can fail forever without anyone noticing. The behaviour
is usually correct — the write really is best-effort and must not break the
caller — but the silence is not. This codemod keeps the behaviour and adds the
report, matching the pattern established in ``tools/llm/router.py`` and
``tools/llm/chain_orchestrator.py``::

    try:
        conn.execute("INSERT INTO audit_trail ...", params)
    except Exception:            ->    except Exception as exc:  # noqa: BLE001 - best-effort ...
        pass                     ->        logger.warning("fn: best-effort INSERT into audit_trail failed ...: %s", exc)

The detector shared with ``tools/workflow/coherence_checker.py`` lives in
:mod:`tools.refactor.swallowed_persistence` so the gate and the fixer can never
disagree about what counts as a violation.

One shape is deliberately detected but not rewritten: a bare ``except:``. Naming
its exception would mean choosing between ``Exception`` (narrows the catch) and
``BaseException`` (keeps ``KeyboardInterrupt`` swallowed), and the tree contains
none. The fixer logs the skip and leaves it for a human; the gate still fails on
it, which is the behaviour that matters.

Usage::

    python tools/refactor/fix_swallowed_persistence.py --dry-run --json
    python tools/refactor/fix_swallowed_persistence.py --write --json
    python tools/refactor/fix_swallowed_persistence.py --write --path tools/govcon
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.refactor.swallowed_persistence import (  # noqa: E402
    LOGGER_IMPORT,
    SwallowSite,
    find_sites,
    get_logger_import_line,
    header_import_line,
    read_source,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.refactor.fix_swallowed_persistence")

#: Comment appended to the rewritten ``except`` header when it carries none.
_NOQA = "  # noqa: BLE001 - best-effort persistence; logged, never raised"

#: Matches an ``except ...:`` header, splitting off any trailing comment.
_EXCEPT_RE = re.compile(r"^(?P<head>\s*except\s+.+?)\s*:(?P<tail>\s*(?:#.*)?)$")

#: Matches a ``pass`` statement, splitting off any trailing comment.
_PASS_RE = re.compile(r"^(?P<indent>\s*)pass\b(?P<tail>\s*(?:#.*)?)$")

_MAX_LINE = 120


def _module_dotted(rel_posix: str) -> str:
    """Derive the logger channel from a repo-relative path.

    ``tools/dashboard/api/intake.py``       -> ``icdev.dashboard.api.intake``
    ``icdev/tools/dashboard/api/intake.py`` -> ``icdev.dashboard.api.intake``

    The mirror resolves to the same channel as its origin on purpose: the two
    trees hold the same module, and byte-parity between them is gated.
    """
    stem = rel_posix[: -len(".py")] if rel_posix.endswith(".py") else rel_posix
    parts = [p for p in stem.split("/") if p]
    if parts and parts[0] == "icdev":
        parts = parts[1:]
    if parts and parts[0] == "tools":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return "icdev." + ".".join(parts) if parts else "icdev"


def _module_logger_name(tree: ast.Module) -> Optional[str]:
    """Return the module-level logger name, whether assigned or imported.

    Some modules share one logger across a package
    (``from tools.network.routes._common import logger``). Defining a second one
    would shadow it and trip F811, so reuse what is already bound.
    """
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if fname in ("get_logger", "getLogger") and isinstance(node.targets[0], ast.Name):
                found = node.targets[0].id
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound in ("logger", "LOGGER") and found is None:
                    found = bound
    return found


def _imports_get_logger(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("logging.icdev_logger"):
            if any(a.name == "get_logger" for a in node.names):
                return True
    return False


def _logger_anchor(tree: ast.Module) -> int:
    """1-based line after which the module logger definition may be inserted.

    The header import block, except when the module already imports
    ``get_logger`` further down — the definition must follow its import.
    """
    return max(header_import_line(tree), get_logger_import_line(tree))


def _local_logger_shadows(site: SwallowSite) -> bool:
    """True when the enclosing function binds ``logger`` itself.

    Adding a module-level ``logger`` would then be shadowed by the local binding
    and our call — which precedes it — would raise ``UnboundLocalError``.
    """
    scope = site.func_node
    if scope is None:
        return False
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "logger":
                    return True
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            tgt = node.target
            if isinstance(tgt, ast.Name) and tgt.id == "logger":
                return True
        elif isinstance(node, ast.arguments):
            for arg in list(node.args) + list(node.posonlyargs) + list(node.kwonlyargs):
                if arg.arg == "logger":
                    return True
    return False


def _pick_exc_name(site: SwallowSite) -> str:
    """Pick a binding name that cannot clobber an existing one in the same scope.

    ``except ... as exc`` unbinds ``exc`` when the handler exits, so reusing a
    name the surrounding function already relies on would delete it.
    """
    scope = site.func_node
    taken = set()
    if scope is not None:
        for node in ast.walk(scope):
            if isinstance(node, ast.Name):
                taken.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                taken.add(node.name)
            elif isinstance(node, ast.arg):
                taken.add(node.arg)
    for cand in ("exc", "_exc", "_persist_exc"):
        if cand not in taken:
            return cand
    return "_persist_exc"


def _warning_call(indent: str, log_name: str, site: SwallowSite, exc_name: str) -> List[str]:
    """Render the ``logger.warning(...)`` replacement for a ``pass`` body."""
    where = site.func_name or "<module>"
    target = f"INSERT into {site.table}" if site.table else "persistence write"
    msg = f"{where}: best-effort {target} failed (non-blocking): %s"
    one = f'{indent}{log_name}.warning("{msg}", {exc_name})'
    if len(one) <= _MAX_LINE:
        return [one]

    body_indent = indent + "    "
    out = [f"{indent}{log_name}.warning("]
    # Split the message across adjacent literals so no emitted line runs long.
    budget = _MAX_LINE - len(body_indent) - 2  # the two quote characters
    words = msg.split(" ")
    chunk = ""
    chunks: List[str] = []
    for word in words:
        candidate = f"{chunk} {word}" if chunk else word
        if len(candidate) > budget and chunk:
            chunks.append(chunk + " ")
            chunk = word
        else:
            chunk = candidate
    chunks.append(chunk)
    for idx, part in enumerate(chunks):
        # Adjacent literals concatenate; only the last one takes the argument comma.
        comma = "," if idx == len(chunks) - 1 else ""
        out.append(f'{body_indent}"{part}"{comma}')
    out.append(f"{body_indent}{exc_name},")
    out.append(f"{indent})")
    return out


def _rewrite_file(path: Path, rel: str, sites: List[SwallowSite]) -> Optional[Tuple[str, List[Dict]]]:
    """Return (new_source, per-site records) or None when nothing changed."""
    src, had_bom, newline = read_source(path)
    lines = src.split("\n")
    tree = ast.parse(src)

    log_name = _module_logger_name(tree)
    need_logger_def = log_name is None
    if need_logger_def:
        # A function-local `logger` would shadow a new module-level one.
        if any(_local_logger_shadows(s) for s in sites):
            log_name = "_persist_logger"
        else:
            log_name = "logger"

    # 1-based line of the last top-level import; 0 when the module has none.
    import_anchor = _logger_anchor(tree)
    # A site rewritten *above* the anchor shifts it. Track that, or the logger
    # definition lands mid-function — which can still parse, so it would be silent.
    anchor_shift = 0

    records: List[Dict] = []
    # Apply bottom-up so earlier line numbers stay valid.
    for site in sorted(sites, key=lambda s: s.handler_lineno, reverse=True):
        hdr_idx = site.handler_lineno - 1
        pass_idx = site.pass_lineno - 1
        hdr = lines[hdr_idx]
        m_hdr = _EXCEPT_RE.match(hdr)
        m_pass = _PASS_RE.match(lines[pass_idx])
        if not m_hdr or not m_pass:
            logger.warning("%s:%d — unexpected handler shape, skipped", rel, site.handler_lineno)
            continue

        exc_name = site.handler_name or _pick_exc_name(site)
        if site.handler_name is None:
            tail = m_hdr.group("tail").strip()
            new_tail = f"  {tail}" if tail else _NOQA
            lines[hdr_idx] = f"{m_hdr.group('head')} as {exc_name}:{new_tail}"

        indent = m_pass.group("indent")
        pass_tail = m_pass.group("tail").strip()
        replacement = _warning_call(indent, log_name, site, exc_name)
        if pass_tail:
            # Keep the author's note; it explains *why* the write is best-effort.
            replacement = [f"{indent}{pass_tail}"] + replacement
        lines[pass_idx : pass_idx + 1] = replacement
        if pass_idx < import_anchor:
            anchor_shift += len(replacement) - 1

        records.append(
            {
                "file": rel,
                "line": site.handler_lineno,
                "function": site.func_name,
                "table": site.table,
                "exc_name": exc_name,
            }
        )

    if not records:
        return None

    if need_logger_def:
        insert_at = import_anchor + anchor_shift
        block: List[str] = []
        if not _imports_get_logger(tree):
            block.append(LOGGER_IMPORT)
        block.append("")
        block.append(f'{log_name} = get_logger("{_module_dotted(rel)}")')
        lines[insert_at:insert_at] = block

    new_src = "\n".join(lines)
    new_tree = ast.parse(new_src)  # refuse to emit a file we just broke
    if _module_logger_name(new_tree) != log_name:
        # Either the definition landed inside a function or the module binds a
        # different logger than we called. Both are silent failures at runtime.
        raise RuntimeError(f"logger '{log_name}' is not bound at module level after rewrite")
    if newline != "\n":
        new_src = new_src.replace("\n", newline)
    # `had_bom` is deliberately not re-emitted: a BOM makes ast.parse() raise,
    # which is how a few of these files dodged every AST gate in the repo.
    del had_bom
    return new_src, records


def run(paths: List[Path], write: bool) -> Dict:
    by_file: Dict[Path, List[SwallowSite]] = {}
    for site in find_sites(paths):
        by_file.setdefault(site.path, []).append(site)

    changed: List[str] = []
    records: List[Dict] = []
    errors: List[str] = []
    for path, sites in sorted(by_file.items()):
        rel = path.relative_to(_PROJECT_ROOT).as_posix()
        try:
            result = _rewrite_file(path, rel, sites)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the sweep
            errors.append(f"{rel}: {exc}")
            logger.warning("rewrite failed for %s: %s", rel, exc)
            continue
        if result is None:
            continue
        new_src, recs = result
        records.extend(recs)
        changed.append(rel)
        if write:
            path.write_text(new_src, encoding="utf-8", newline="")

    return {
        "files_changed": len(changed),
        "sites_fixed": len(records),
        "written": write,
        "files": changed,
        "sites": records,
        "errors": errors,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Log instead of swallowing at persistence sites")
    ap.add_argument("--path", action="append", default=None, help="Limit to a path (repeatable)")
    ap.add_argument("--write", action="store_true", help="Apply the rewrite")
    ap.add_argument("--dry-run", action="store_true", help="Report only (default)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    targets = [Path(p) if Path(p).is_absolute() else _PROJECT_ROOT / p for p in (args.path or ["tools"])]
    result = run(targets, write=args.write and not args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        verb = "Rewrote" if result["written"] else "Would rewrite"
        print(f"{verb} {result['sites_fixed']} site(s) across {result['files_changed']} file(s)")
        for err in result["errors"]:
            print(f"  ERROR {err}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
