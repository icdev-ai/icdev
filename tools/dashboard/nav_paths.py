#!/usr/bin/env python3
# CUI // SP-CTI
"""Derive the two shared nav surfaces instead of hand-appending to them (mfx-sib-02).

    python tools/dashboard/nav_paths.py --write        # regenerate both blocks
    python tools/dashboard/nav_paths.py --check        # exit 1 on drift
    python tools/dashboard/nav_paths.py --check --nav-only   # the cheap half
    python tools/dashboard/nav_paths.py --json

THE DEFECT
----------
Two lines in this repository were lists that nothing generated:

  * ``- Pages:`` in ``.claude/commands/start.md``
  * ``request.path in ['/compliance', ...]`` on the Compliance dropdown trigger
    in ``tools/dashboard/templates/base.html`` and its ``icdev/`` mirror

Every route-migration card appended one token to each — the new canvas URL and
the legacy URL its 301 preserves — so N cards of one epic collided N-1 times on
two lines none of them was really editing. mfx-sib-01 stopped the concurrency;
this removes the shared line, by making both blocks CONSEQUENCES of the route
definitions rather than a parallel hand-maintained claim about them.

After this, a route-migration card edits its blueprint, adds its menu link and
its 301, and runs ``--write``.

TWO DERIVATIONS, DELIBERATELY DIFFERENT IN COST
-----------------------------------------------
``derive_nav_paths`` is STATIC — it parses base.html for the dropdown's own
menu links and walks app.py's AST for redirect literals. Milliseconds, no
imports, no database, so the pre-commit hook can run it on every commit that
touches a nav surface.

``derive_pages`` needs the real ``url_map``, which means creating the app
(~15-30s). It runs in a SUBPROCESS WITH A SCRUBBED ENVIRONMENT, because which
blueprints register depends on ``.env`` toggles: measured 2026-09-04, the same
checkout yields 4568 rules under one operator's ambient environment and 2370
under a bare one. A ``--check`` reading the inherited environment would pass or
fail according to whose shell ran it — which is not a check. Every component in
``args/component_registry.yaml`` is forced ON, so the documented list is the
superset of pages the platform can serve rather than one machine's toggles.

WHAT IS NOT GENERATED
---------------------
The ``startswith(...)`` clauses stay hand-written in the ``{% if %}``, outside
the markers. A prefix is a policy ("everything under the Security canvas counts
as Compliance"); an enumeration is a fact about the menu. Only the fact is
derived. Legacy tokens the derivation provably cannot see — a path the menu
never linked to and nothing redirects to — are DECLARED with a written reason
in ``args/nav_paths.yaml`` rather than lost inside a template attribute.

EXIT CODES
----------
0 clean · 1 drift · 2 the check could not be produced. 2 stays a failure: a
check that could not run is not a check that found nothing.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

# sys.path BOOTSTRAP, and it must precede the first first-party import: run as
# `python tools/dashboard/nav_paths.py` this file's directory is on sys.path
# and the repo root is not, so `import icdev...` below would die with
# ModuleNotFoundError before main() is reached (kax-conflict-04). `parents[2]`
# here resolves the IMPORT root -- it is the bootstrap idiom, not a self-root
# site, and it stays correct if this module moves because the line moves with
# it. The REPO root is still resolved by the one resolver, immediately below.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

CONFIG_RELPATH = "args/nav_paths.yaml"

#: Marker ids. The generated region of a file is delimited by
#: ``BEGIN GENERATED <marker>`` / ``END GENERATED <marker>`` lines, whatever
#: comment syntax the host file uses.
NAV_MARKER = "nav-active-paths:compliance"
PAGES_MARKER = "start-pages"

REGEN_HINT = "python tools/dashboard/nav_paths.py --write"


class DeriveError(RuntimeError):
    """The derivation could not be produced — exit 2, never a clean answer."""


# ── config ───────────────────────────────────────────────────────────────────

def load_config(root: Path | None = None) -> dict[str, Any]:
    import yaml

    path = (root or BASE_DIR) / CONFIG_RELPATH
    if not path.exists():
        raise DeriveError(f"missing config: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise DeriveError(f"config is not a mapping: {path}")
    return data


def _dropdown_config(dropdown: str, root: Path | None = None) -> dict[str, Any]:
    cfg = load_config(root).get("nav", {}) or {}
    entry = (cfg.get("dropdowns") or {}).get(dropdown)
    if not entry:
        raise DeriveError(f"no nav dropdown '{dropdown}' declared in {CONFIG_RELPATH}")
    return entry


def nav_targets(root: Path | None = None) -> list[Path]:
    base = root or BASE_DIR
    rels = (load_config(root).get("nav", {}) or {}).get("targets") or []
    return [base / rel for rel in rels]


def pages_target(root: Path | None = None) -> Path:
    base = root or BASE_DIR
    rel = (load_config(root).get("pages", {}) or {}).get("target")
    if not rel:
        raise DeriveError(f"no pages target declared in {CONFIG_RELPATH}")
    return base / rel


def declared_extras(dropdown: str, root: Path | None = None) -> list[str]:
    entry = _dropdown_config(dropdown, root)
    return [e["path"] for e in (entry.get("extra_paths") or []) if e.get("path")]


def declared_extra_reasons(dropdown: str, root: Path | None = None) -> dict[str, str]:
    entry = _dropdown_config(dropdown, root)
    return {
        e["path"]: (e.get("reason") or "")
        for e in (entry.get("extra_paths") or [])
        if e.get("path")
    }


# ── marked blocks ────────────────────────────────────────────────────────────

def _marker_bounds(lines: list[str], marker: str) -> tuple[int, int]:
    begin = end = -1
    for i, line in enumerate(lines):
        if f"BEGIN GENERATED {marker}" in line:
            begin = i
        elif f"END GENERATED {marker}" in line:
            end = i
    if begin < 0 or end < 0 or end <= begin:
        raise DeriveError(
            f"markers for '{marker}' not found (or out of order). "
            f"Expected 'BEGIN GENERATED {marker}' and 'END GENERATED {marker}'."
        )
    return begin, end


def read_block(path: Path, marker: str) -> str | None:
    """The text between the markers, or None if the file/markers are absent."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        begin, end = _marker_bounds(lines, marker)
    except DeriveError:
        return None
    return "\n".join(lines[begin + 1:end])


def _replace_block(path: Path, marker: str, body: str) -> bool:
    """Write ``body`` between the markers. True when the file changed."""
    original = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    begin, end = _marker_bounds(lines, marker)
    updated = lines[: begin + 1] + body.splitlines() + lines[end:]
    trailing = newline if original.endswith(("\n", "\r")) else ""
    text = newline.join(updated) + trailing
    if text == original:
        return False
    path.write_text(text, encoding="utf-8", newline="")
    return True


# ── nav derivation (static, no import) ───────────────────────────────────────

_HREF_RE = re.compile(r'href="(/[^"#?\s]*)"')

#: An href whose value is computed by Jinja. It is a menu link and it is NOT a
#: literal path, so the static derivation can neither resolve it nor pretend it
#: is absent.
_DYNAMIC_HREF_RE = re.compile(r'(href="\s*\{[{%].*?")', re.DOTALL)


def _menu_block(html: str, anchor: str) -> str:
    """The ``<ul class="nav-dropdown-menu">`` belonging to the ``anchor`` trigger."""
    lines = html.splitlines()
    trigger = None
    for i, line in enumerate(lines):
        if "nav-dropdown-trigger" in line and f">{anchor} " in line:
            trigger = i
            break
    if trigger is None:
        raise DeriveError(f"no nav-dropdown-trigger labelled '{anchor}' in base.html")

    start = None
    for i in range(trigger, min(trigger + 12, len(lines))):
        if 'class="nav-dropdown-menu"' in lines[i]:
            start = i
            break
    if start is None:
        raise DeriveError(f"'{anchor}' trigger is not followed by a nav-dropdown-menu")

    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("<ul")
        depth -= lines[i].count("</ul>")
        if depth <= 0:
            return "\n".join(lines[start:i + 1])
    raise DeriveError(f"unterminated nav-dropdown-menu for '{anchor}'")


def _menu_html(dropdown: str, root: Path | None = None) -> str:
    entry = _dropdown_config(dropdown, root)
    targets = nav_targets(root)
    if not targets:
        raise DeriveError("no nav targets declared")
    return _menu_block(targets[0].read_text(encoding="utf-8"), entry["anchor"])


def menu_hrefs(dropdown: str, root: Path | None = None) -> list[str]:
    """Every internal page the dropdown's own menu links to."""
    return sorted(set(_HREF_RE.findall(_menu_html(dropdown, root))))


def unresolvable_hrefs(dropdown: str, root: Path | None = None) -> list[str]:
    """Menu links this static derivation CANNOT read -- reported, never dropped.

    An ``href="{{ url_for('bp.page') }}"`` is a real menu link whose URL only
    exists once the app is built, so ``menu_hrefs`` cannot see it and the page
    would silently stop highlighting its own dropdown -- the exact defect this
    module exists to end, reintroduced by the fix. It is REPORTED rather than
    raised: refusing a legitimate ``url_for`` link would make this gate a false
    refusal, and a false refusal is what earns a check a ``|| true``.

    Measured 2026-09-04: 0 on the Compliance menu, all 30 links are literals.
    """
    return sorted(set(_DYNAMIC_HREF_RE.findall(_menu_html(dropdown, root))))


def _redirect_target(node: ast.AST) -> str | None:
    """``redirect("<literal>", code=301)`` → the literal, else None."""
    if not isinstance(node, ast.Call):
        return None
    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
    if name != "redirect" or not node.args:
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    code = next(
        (kw.value.value for kw in node.keywords
         if kw.arg == "code" and isinstance(kw.value, ast.Constant)),
        None,
    )
    if code != 301:
        return None
    return first.value


def legacy_aliases(root: Path | None = None) -> dict[str, list[str]]:
    """``{new_path: [old_path, ...]}`` for every literal 301 in the declared sources.

    A route-migration card writes exactly this: the page moves onto a canvas
    blueprint and the old ``@app.route`` stays behind as a permanent redirect so
    a bookmark, an e2e spec or a nav link still lands on the page. That redirect
    IS the statement "these two URLs are the same page", so the nav list reads
    it rather than asking the card to repeat it.
    """
    base = root or BASE_DIR
    sources = (load_config(root).get("nav", {}) or {}).get("redirect_sources") or []
    aliases: dict[str, set[str]] = {}
    for rel in sources:
        path = base / rel
        if not path.exists():
            raise DeriveError(f"declared redirect source is missing: {path}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rules = [
                d.args[0].value
                for d in node.decorator_list
                if isinstance(d, ast.Call)
                and getattr(d.func, "attr", None) == "route"
                and d.args
                and isinstance(d.args[0], ast.Constant)
                and isinstance(d.args[0].value, str)
            ]
            if not rules:
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Return):
                    continue
                target = _redirect_target(sub.value) if sub.value else None
                if target:
                    aliases.setdefault(target, set()).update(rules)
    return {k: sorted(v) for k, v in sorted(aliases.items())}


def derive_nav_paths(dropdown: str, root: Path | None = None) -> list[str]:
    """The active-path list for one dropdown, derived.

    menu links  ∪  the 301 aliases OF THOSE LINKS  ∪  declared residue.

    An alias whose target is not in this menu is not pulled in: a redirect says
    two URLs are one page, not that the page belongs to this dropdown.
    """
    menu = menu_hrefs(dropdown, root)
    aliases = legacy_aliases(root)
    derived = set(menu)
    for target, olds in aliases.items():
        if target in derived:
            derived.update(olds)
    derived.update(declared_extras(dropdown, root))
    return sorted(derived)


def render_nav_block(dropdown: str, root: Path | None = None, indent: str = " " * 16) -> str:
    """The Jinja ``{% set %}`` block, ONE PATH PER LINE.

    One path per line on purpose. A single long line is one merge hunk, so two
    cards regenerating it collide exactly as the hand-written line did; sorted
    one-per-line entries land in different regions of the file and merge
    cleanly unless the two paths are genuinely adjacent.
    """
    entry = _dropdown_config(dropdown, root)
    paths = derive_nav_paths(dropdown, root)
    inner = indent + "    "
    body = [f"{indent}{{% set {entry['variable']} = ["]
    body += [f"{inner}{path!r}," for path in paths]
    body.append(f"{indent}] %}}")
    return "\n".join(body)


def write_nav(root: Path | None = None) -> dict[str, Any]:
    base = root or BASE_DIR
    cfg = load_config(root).get("nav", {}) or {}
    changed: list[str] = []
    paths: dict[str, list[str]] = {}
    unresolvable: dict[str, list[str]] = {}
    for dropdown in (cfg.get("dropdowns") or {}):
        paths[dropdown] = derive_nav_paths(dropdown, root)
        unresolvable[dropdown] = unresolvable_hrefs(dropdown, root)
        block = render_nav_block(dropdown, root)
        for target in nav_targets(root):
            if not target.exists():
                raise DeriveError(f"nav target is missing: {target}")
            if _replace_block(target, NAV_MARKER, block):
                changed.append(str(target.relative_to(base)))
    return {"paths": paths, "changed": changed, "unresolvable_hrefs": unresolvable}


def check_nav(root: Path | None = None) -> dict[str, Any]:
    base = root or BASE_DIR
    cfg = load_config(root).get("nav", {}) or {}
    diffs: list[str] = []
    paths: dict[str, list[str]] = {}
    unresolvable: dict[str, list[str]] = {}
    for dropdown in (cfg.get("dropdowns") or {}):
        paths[dropdown] = derive_nav_paths(dropdown, root)
        unresolvable[dropdown] = unresolvable_hrefs(dropdown, root)
        expected = render_nav_block(dropdown, root)
        for target in nav_targets(root):
            actual = read_block(target, NAV_MARKER)
            if actual is None:
                diffs.append(f"{target.relative_to(base)}: no '{NAV_MARKER}' block")
            elif actual.strip() != expected.strip():
                diffs.append(
                    f"{target.relative_to(base)}: '{NAV_MARKER}' block differs from the "
                    f"derivation — regenerate with `{REGEN_HINT}`"
                )
    return {
        "ok": not diffs,
        "diffs": diffs,
        "paths": paths,
        "unresolvable_hrefs": unresolvable,
    }


# ── pages derivation (url_map, scrubbed subprocess) ──────────────────────────

_PROBE_CODE = (
    "import json, sys\n"
    "from tools.dashboard.app import create_app\n"
    "app = create_app()\n"
    "rules = [\n"
    "    {'rule': r.rule, 'endpoint': r.endpoint, 'methods': sorted(r.methods or [])}\n"
    "    for r in app.url_map.iter_rules()\n"
    "]\n"
    "sys.stdout.write('@@NAVPATHS@@' + json.dumps(rules))\n"
)


def probe_env(root: Path | None = None) -> dict[str, str]:
    """The declared, scrubbed environment the url_map probe runs under.

    Inheriting this process's environment is what makes a ``--check`` on the
    url_map worthless: which blueprints register depends on the toggles in
    ``.env``. Only platform-location variables are passed through; every
    component in the registry is forced ON so the answer is the superset.
    """
    base = root or BASE_DIR
    cfg = load_config(root).get("pages", {}) or {}
    env = {
        key: os.environ[key]
        for key in (cfg.get("passthrough_env") or [])
        if os.environ.get(key)
    }
    env.update({str(k): str(v) for k, v in (cfg.get("probe_env") or {}).items()})
    env["PYTHONPATH"] = str(base)
    env["ICDEV_DB_PATH"] = str(
        Path(tempfile.mkdtemp(prefix="icdev-navpaths-")) / "icdev.db"
    )
    for flag in _registry_flags(base):
        env[flag] = "true"
    return env


def _registry_flags(base: Path) -> list[str]:
    """Every env toggle the component registry declares, so none is left off."""
    try:
        from tools.config.component_registry import ComponentRegistry

        registry = ComponentRegistry()
        components = registry.list_all()
    except Exception as exc:  # pragma: no cover - registry unreadable
        raise DeriveError(f"cannot read the component registry: {exc}") from exc

    flags: list[str] = []
    for component in components:
        candidates = [getattr(component, "env_flag", None)]
        candidates += list(getattr(component, "extra_env_flags", None) or [])
        for flag in candidates:
            if flag and flag not in flags:
                flags.append(flag)
    if not flags:
        raise DeriveError("the component registry declared no env flags")
    return sorted(flags)


def probe_rules(root: Path | None = None, timeout: int = 900) -> list[dict[str, Any]]:
    base = root or BASE_DIR
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE],
        cwd=str(base), env=probe_env(root), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if "@@NAVPATHS@@" not in proc.stdout:
        tail = (proc.stdout[-1500:] + "\n" + proc.stderr[-1500:]).strip()
        raise DeriveError(f"url_map probe failed (rc={proc.returncode}):\n{tail}")
    return json.loads(proc.stdout.split("@@NAVPATHS@@", 1)[1])


def pages_from_rules(rules: Iterable[dict[str, Any]], root: Path | None = None) -> list[str]:
    """A page is a GET rule a human can open — pure, so it is unit-testable.

    ``/api/`` rules are the machine surface; they are documented by the OpenAPI
    builder, and 400-odd of them in a prose bullet is not documentation.
    """
    cfg = load_config(root).get("pages", {}) or {}
    excluded_endpoints = set(cfg.get("exclude_endpoints") or [])
    excluded_substrings = list(cfg.get("exclude_rule_substrings") or [])
    keep: set[str] = set()
    for rule in rules:
        if rule.get("endpoint") in excluded_endpoints:
            continue
        if "GET" not in (rule.get("methods") or []):
            continue
        path = rule["rule"]
        if any(sub in path for sub in excluded_substrings):
            continue
        keep.add(path)
    return sorted(keep)


def derive_pages(root: Path | None = None) -> list[str]:
    return pages_from_rules(probe_rules(root), root)


def render_pages_block(root: Path | None = None, pages: list[str] | None = None) -> str:
    pages = derive_pages(root) if pages is None else pages
    if not pages:
        raise DeriveError("the url_map probe returned no pages")
    return "- Pages: " + ", ".join(f"`{page}`" for page in pages)


def write_pages(root: Path | None = None, pages: list[str] | None = None) -> dict[str, Any]:
    base = root or BASE_DIR
    target = pages_target(root)
    if not target.exists():
        raise DeriveError(f"pages target is missing: {target}")
    block = render_pages_block(root, pages)
    changed = _replace_block(target, PAGES_MARKER, block)
    return {
        "pages": block.count("`") // 2,
        "changed": [str(target.relative_to(base))] if changed else [],
    }


def check_pages(root: Path | None = None, pages: list[str] | None = None) -> dict[str, Any]:
    base = root or BASE_DIR
    target = pages_target(root)
    expected = render_pages_block(root, pages)
    actual = read_block(target, PAGES_MARKER)
    diffs: list[str] = []
    if actual is None:
        diffs.append(f"{target.relative_to(base)}: no '{PAGES_MARKER}' block")
    elif actual.strip() != expected.strip():
        expected_set = set(re.findall(r"`([^`]+)`", expected))
        actual_set = set(re.findall(r"`([^`]+)`", actual))
        added = sorted(expected_set - actual_set)
        removed = sorted(actual_set - expected_set)
        diffs.append(
            f"{target.relative_to(base)}: Pages block differs from the url_map — "
            f"{len(added)} undocumented, {len(removed)} stale. "
            f"Regenerate with `{REGEN_HINT}`."
        )
        if added:
            diffs.append("  undocumented: " + ", ".join(added[:20]))
        if removed:
            diffs.append("  stale: " + ", ".join(removed[:20]))
    return {"ok": not diffs, "diffs": diffs}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive the nav active-path list and the start.md Pages line.",
    )
    parser.add_argument("--write", action="store_true", help="regenerate both blocks in place")
    parser.add_argument("--check", action="store_true", help="exit 1 when a block has drifted")
    parser.add_argument("--nav-only", action="store_true", help="the static half (no app import)")
    parser.add_argument("--pages-only", action="store_true", help="the url_map half only")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    if not (args.write or args.check):
        args.check = True
    do_nav = not args.pages_only
    do_pages = not args.nav_only

    result: dict[str, Any] = {"nav": None, "pages": None}
    try:
        if args.write:
            if do_nav:
                result["nav"] = write_nav()
            if do_pages:
                result["pages"] = write_pages()
        else:
            if do_nav:
                result["nav"] = check_nav()
            if do_pages:
                result["pages"] = check_pages()
    except DeriveError as exc:
        # Exit 2 — the check could not be produced. Never reported as clean.
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for dropdown, hrefs in (result.get("nav") or {}).get("unresolvable_hrefs", {}).items():
        if hrefs:
            # A warning, never a failure -- see unresolvable_hrefs().
            print(
                f"WARNING: {len(hrefs)} link(s) in the '{dropdown}' menu are built by "
                f"Jinja and cannot be derived statically, so they will not highlight "
                f"the trigger: {', '.join(hrefs)}",
                file=sys.stderr,
            )

    diffs = [d for section in result.values() if section for d in section.get("diffs", [])]
    changed = [c for section in result.values() if section for c in section.get("changed", [])]

    if args.json:
        print(json.dumps({"ok": not diffs, **result}, indent=2, default=str))
    elif args.write:
        print("nav_paths: " + (f"rewrote {', '.join(changed)}" if changed else "already current"))
    elif diffs:
        print("nav_paths: DRIFT")
        for diff in diffs:
            print(f"  {diff}")
    else:
        print("nav_paths: both generated blocks match their derivation")

    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
