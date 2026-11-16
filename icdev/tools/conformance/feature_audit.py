#!/usr/bin/env python3
# CUI // SP-CTI
"""Feature conformance audit — each feature judged against the five rules (icx-cnf-01).

The owner's five rules (2026-11-14) say every feature has a console GUI, a
declared workflow for every decision a person takes, enterprise-twin observation,
a place in the architecture model with requirements traced to tests, and an
AI-native capability that only reads or proposes. ICDEV[IT] had no per-feature
check for any of them. This tool is that check.

WHAT IS JUDGED, AND FROM WHAT
-----------------------------
The features in scope, and the surfaces each owns, are listed in
``args/feature_conformance.yaml``. Every verdict is derived from the repository,
never from that file:

``gui``           the LIVE url_map, read in a scrubbed subprocess (the same probe
                  environment ``tools/dashboard/nav_paths.py`` uses, every
                  registry component forced on), the view functions' source, the
                  registry and ``base.html`` nav hrefs, and the endpoint ids the
                  templates and scripts a page ships name in code
                  (``icdevRoute('<endpoint>')``, ``icdevFetch('<endpoint>')``,
                  ``url_for('<endpoint>')``; icx-cnf-08). A page is a GET route
                  that renders a template; one must be in the nav or one link from
                  a nav page, and every mutating route must be called by a page.
                  A path a page writes or builds itself names nothing; in an
                  audited file any ``/api/`` path fails ``--check``. CI's e2e job
                  holds every write credited to a page to having been SENT by a
                  page (``--e2e-gate``).
``workflow``      a decision is a mutating route or MCP tool a person decides:
                  its name carries a decision verb (launch, confirm, approve,
                  award, delete, ...) or its source a HITL/approval/confirm
                  pattern, or the config declares one with a quotation that must
                  exist in the named file. Each maps to a template under
                  ``args/workflow_hitl_templates/`` with a manual stage held by a
                  role, or (an MCP tool) to an approval tier that halts in
                  ``args/agent_approval_policy.yaml``.
``twin``          an adapter in ``tools/twin_core/adapters/`` decorated with
                  ``@register_twin`` whose ``canvas_key`` is the component (or
                  whose ``observes`` names the feature), that carries
                  ``thread_id`` on its violations and defines ``replay``.
``architecture``  ICDEV's own platform model, ``args/platform_model/*.yaml``:
                  requirement -> element (a digital-thread artifact type) -> code
                  -> an existing test, with no orphan elements.
``ai_native``     the MCP registry (``list_tools()``) with ``READ_ONLY_DECLARATIONS`` and
                  ``tool_authorization``: at least one read tool, and every
                  mutating tool in an approval tier that halts.

Every cell lists the evidence it rests on. A rule that genuinely does not apply
takes a stated reason in the config; it excuses only that rule, and a reason the
evidence no longer needs is STALE and fails ``--check``.

Output is deterministic: no timestamps and no line numbers, everything sorted,
so two runs over one tree are byte-identical and the committed doc goes stale
only when the evidence changes.

CLI::

    python tools/conformance/feature_audit.py              # JSON report
    python tools/conformance/feature_audit.py --markdown   # rewrite doc tables + snapshot
    python tools/conformance/feature_audit.py --census     # rewrite the gap census
    python tools/conformance/feature_audit.py --check      # exit 1: stale doc/reason/census
    python tools/conformance/feature_audit.py --e2e-gate FILE...  # exit 1: a credited write no page sent
"""
from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

import yaml  # noqa: E402

from icdev.core.paths import repo_root  # noqa: E402
from tools.mbse.platform_model import _Symbols, resolve_code, resolve_test  # noqa: E402

BASE_DIR = repo_root(__file__)

CONFIG_RELPATH = "args/feature_conformance.yaml"
RULES = ("gui", "workflow", "twin", "architecture", "ai_native")
RULE_TITLES = {
    "gui": "GUI",
    "workflow": "Workflow",
    "twin": "Twin",
    "architecture": "Architecture",
    "ai_native": "AI-native",
}
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DOC_MARKER = "feature-conformance"
REGEN_HINT = "python tools/conformance/feature_audit.py --markdown --census"

#: Name tokens that make a mutating surface a decision a person takes.
DECISION_VERBS = frozenset({
    "launch", "rerun", "abort", "cancel", "confirm", "decline", "approve",
    "reject", "delete", "award", "adjudicate", "sign", "waive", "release", "hitl",
})
#: Source patterns that make a mutating route a decision (HITL gate, approval
#: gate, confirm-then-act).
DECISION_SOURCE_RE = re.compile(
    r"\b(HITLGate|approval_gate|request_approval|confirm_proposal|requires_confirm)\b"
)
#: ``render_template(...)``, or any ``*render*`` helper handed a literal ``.html``
#: (``_render_page("coworker/roles.html")``).
_RENDER_RE = re.compile(
    r"\b(?:render_template|\w*render\w*(?=\(\s*[\"'][^\"']+\.html[\"']))"
    r"\(\s*(?:[\"']([^\"']+)[\"'])?"
)
_HREF_RE = re.compile(r"href=\"(/[^\"#?\s{]*)\"")
_INCLUDE_RE = re.compile(r"\{%-?\s*(?:include|extends)\s+[\"']([^\"']+)[\"']")
_PARAM_RE = re.compile(r"<(?:[^:<>]+:)?[^<>]+>")


class AuditError(RuntimeError):
    """The audit could not be produced. Exit 2, never a clean answer."""


# ── config ───────────────────────────────────────────────────────────────────


def load_config(root: Optional[Path] = None) -> dict[str, Any]:
    path = (root or BASE_DIR) / CONFIG_RELPATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise AuditError(f"cannot read {CONFIG_RELPATH}: {exc}") from exc
    if not isinstance(data.get("features"), list) or not data["features"]:
        raise AuditError(f"{CONFIG_RELPATH} lists no features")
    return data


# ── evidence: the live url_map ───────────────────────────────────────────────

_PROBE_CODE = r'''
import inspect, json, os, sys
from tools.dashboard.app import create_app
app = create_app()
root = os.getcwd()
out = []
for r in app.url_map.iter_rules():
    view = app.view_functions.get(r.endpoint)
    src = {}
    if view is not None:
        fn = inspect.unwrap(view)
        try:
            lines, start = inspect.getsourcelines(fn)
            path = os.path.relpath(inspect.getsourcefile(fn), root)
            src = {"file": path.replace(os.sep, "/"), "start": start,
                   "end": start + len(lines) - 1}
        except (OSError, TypeError, ValueError):
            src = {}
    out.append({"rule": r.rule, "endpoint": r.endpoint,
                "methods": sorted(r.methods or []), **src})
sys.stdout.write("@@FEATURE_AUDIT@@" + json.dumps(out))
'''


def probe_rules(root: Optional[Path] = None, timeout: int = 900) -> list[dict[str, Any]]:
    """Every url_map rule with its view function's source location.

    Runs in the scrubbed probe environment of ``tools/dashboard/nav_paths.py``
    (platform-location variables only, every registry toggle forced on), so the
    answer does not depend on whose shell ran it.
    """
    from tools.dashboard.nav_paths import DeriveError, probe_env

    base = root or BASE_DIR
    try:
        env = probe_env(base)
    except DeriveError as exc:
        raise AuditError(str(exc)) from exc
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE],
        cwd=str(base), env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if "@@FEATURE_AUDIT@@" not in proc.stdout:
        tail = (proc.stdout[-1500:] + "\n" + proc.stderr[-1500:]).strip()
        raise AuditError(f"url_map probe failed (rc={proc.returncode}):\n{tail}")
    return json.loads(proc.stdout.split("@@FEATURE_AUDIT@@", 1)[1])


# ── evidence: files ──────────────────────────────────────────────────────────


class _Files:
    """Cached, root-relative reads. A missing file reads as empty."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, str] = {}

    def text(self, rel: str) -> str:
        if rel not in self._cache:
            try:
                self._cache[rel] = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._cache[rel] = ""
        return self._cache[rel]

    def exists(self, rel: str) -> bool:
        return bool(rel) and (self.root / rel).is_file()

    def view_text(self, rule: dict[str, Any]) -> str:
        if not rule.get("file"):
            return ""
        lines = self.text(rule["file"]).splitlines()
        return "\n".join(lines[max(rule["start"] - 1, 0):rule["end"]])


def _ui_files(root: Path) -> list[str]:
    """Every template and first-party script a page can ship."""
    found: set[str] = set()
    tools = root / "tools"
    for pattern in ("**/templates/**/*.html", "**/static/**/*.js"):
        for path in tools.glob(pattern):
            rel = path.relative_to(root).as_posix()
            if "/vendor/" in rel or rel.endswith(".min.js"):
                continue
            found.add(rel)
    return sorted(found)


def _template_file(files: _Files, ui: list[str], name: str) -> str:
    """Resolve a ``render_template`` name to a file under some templates/ dir."""
    preferred = f"tools/dashboard/templates/{name}"
    if files.exists(preferred):
        return preferred
    suffix = f"/templates/{name}"
    for rel in ui:
        if rel.endswith(suffix):
            return rel
    return ""


def _registry(root: Path) -> list[dict[str, Any]]:
    path = root / "args" / "component_registry.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise AuditError(f"cannot read args/component_registry.yaml: {exc}") from exc
    return [c for c in (data.get("components") or []) if isinstance(c, dict) and c.get("key")]


def _norm(path: str) -> str:
    return path.rstrip("/") or "/"


def nav_hrefs(root: Path, files: _Files) -> set[str]:
    """Hrefs the nav renders: registry nav links (as the registry normalises
    them) plus every literal href in ``base.html``."""
    hrefs: set[str] = set()
    for comp in _registry(root):
        nav = comp.get("nav") or {}
        if not nav.get("section"):
            continue
        links = nav.get("links") or []
        if links:
            hrefs.update(str(link.get("href") or "") for link in links if isinstance(link, dict))
        elif comp.get("url_prefix"):
            hrefs.add(str(comp["url_prefix"]))
    hrefs.update(_HREF_RE.findall(files.text("tools/dashboard/templates/base.html")))
    return {_norm(h) for h in hrefs if h.startswith("/")}


#: How a page names a route: the string-literal first argument of ``icdevRoute(``,
#: ``icdevFetch(`` (tools/dashboard/static/js/routes.gen.js) or Jinja's ``url_for(``.
#: A variable, a concatenation or a template literal names nothing (icx-cnf-08).
_REF_RE = re.compile(r"\b(?:icdevRoute|icdevFetch|url_for)\(\s*([\"'])([A-Za-z_][\w.]*)\1")
_COMMENT_RES = (
    re.compile(r"\{#.*?#\}", re.S),           # Jinja
    re.compile(r"<!--.*?-->", re.S),            # HTML
    re.compile(r"(?:(?<=\s)|^)/\*.*?\*/", re.S | re.M),  # JS block (not '*/*')
    re.compile(r"(?:(?<=[\s;{}(),])|^)//[^\n]*", re.M),    # JS line (not 'https://')
)
#: The generated route table: it lists every endpoint, so it names none.
ROUTES_GEN_RELPATH = "tools/dashboard/static/js/routes.gen.js"
#: The committed record of an e2e run (tests/e2e/fixtures/observed_calls.ts).
OBSERVED_RELPATH = "tests/e2e/observed-calls.json"
_API_RE = re.compile(r"/api/")


def code_of(text: str) -> str:
    """*text* with its comments blanked, so a commented-out call names nothing."""
    for pattern in _COMMENT_RES:
        text = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


def refs_in(text: str) -> set[str]:
    """Every endpoint id *text* names in code: exact, nothing inferred."""
    return {m.group(2) for m in _REF_RE.finditer(code_of(text))}


def endpoint_refs(ui_text: dict[str, str]) -> dict[str, list[str]]:
    """endpoint -> the UI files that name it."""
    out: dict[str, set[str]] = {}
    for rel, text in ui_text.items():
        if rel == ROUTES_GEN_RELPATH:
            continue
        for endpoint in refs_in(text):
            out.setdefault(endpoint, set()).add(rel)
    return {k: sorted(v) for k, v in sorted(out.items())}


# ── evidence: workflow, twin, architecture, AI-native ────────────────────────


def workflow_templates(root: Path, rel_dir: str) -> dict[str, list[dict[str, Any]]]:
    """surface -> the declared templates that decide it."""
    out: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((root / rel_dir).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        stages = data.get("stages") or []
        manual = any(isinstance(s, dict) and s.get("step_type") == "manual" and s.get("role")
                     for s in stages)
        entry = {"id": str(data.get("id") or path.stem),
                 "file": path.relative_to(root).as_posix(), "manual_role_stage": manual}
        for surface in data.get("decides") or []:
            out.setdefault(str(surface), []).append(entry)
    return out


def approval_policy(root: Path, rel: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load((root / rel).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"tiers": {}, "default": "unknown", "halts": set(), "file": rel}
    tiers = {}
    for tier, names in (data.get("tools") or {}).items():
        for name in names or []:
            tiers[str(name)] = str(tier)
    return {"tiers": tiers, "default": str(data.get("default_tier") or "unknown"),
            "halts": set(data.get("require_approval_tiers") or []), "file": rel}


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def twin_adapters(root: Path, rel_dir: str) -> list[dict[str, Any]]:
    """Registered adapters, read statically (no import, so no side effects)."""
    found = []
    for path in sorted((root / rel_dir).glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            registered = any(
                (isinstance(d, ast.Name) and d.id == "register_twin")
                or (isinstance(d, ast.Attribute) and d.attr == "register_twin")
                for d in node.decorator_list
            )
            if not registered:
                continue
            attrs: dict[str, Any] = {}
            methods = set()
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            attrs[target.id] = _literal(item.value)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
            segment = ast.get_source_segment(text, node) or ""
            found.append({
                "adapter": f"{path.relative_to(root).as_posix()}::{node.name}",
                "canvas_key": str(attrs.get("canvas_key") or ""),
                "observes": [str(x) for x in (attrs.get("observes") or [])],
                "thread_ids": "thread_id" in segment,
                "replay": "replay" in methods,
            })
    return found


def _digital_thread_types(files: _Files) -> set[str]:
    """``VALID_TYPES`` from tools/mbse/digital_thread.py, read without importing it."""
    try:
        tree = ast.parse(files.text("tools/mbse/digital_thread.py"))
    except SyntaxError:
        return set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "VALID_TYPES" for t in node.targets)):
            return set(_literal(node.value) or ())
    return set()


def platform_model(root: Path, rel_dir: str) -> dict[str, Any]:
    reqs: list[dict[str, Any]] = []
    elements: dict[str, dict[str, Any]] = {}
    for path in sorted((root / rel_dir).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        for req in data.get("requirements") or []:
            if isinstance(req, dict) and req.get("id"):
                reqs.append(req)
        # An interface is an element too (an interface block); icx-cnf-04.
        for el in (data.get("elements") or []) + (data.get("interfaces") or []):
            if isinstance(el, dict) and el.get("id"):
                elements[str(el["id"])] = el
    return {"requirements": reqs, "elements": elements}


def mcp_evidence(names: Iterable[str]) -> dict[str, dict[str, Any]]:
    """What the MCP registry declares for each tool name.

    A tool is registered when ``list_tools()`` names it: ``TOOL_REGISTRY`` or an
    ``input_schema`` entry in ``RESOURCE_REGISTRY``, the same set the read-only
    and authorization declarations cover.
    """
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    registered = set(list_tools())
    out = {}
    for name in sorted(set(names)):
        known = name in registered
        out[name] = {
            "registered": known,
            "read_only": READ_ONLY_DECLARATIONS.get(name),
            "tier": tool_authorization(name)["tier"] if known else "unknown_tool",
        }
    return out


def mcp_names() -> list[str]:
    from tools.mcp.tool_registry import list_tools

    return sorted(list_tools())


# ── judging ──────────────────────────────────────────────────────────────────


def _gap(gap_id: str, detail: str) -> dict[str, str]:
    return {"id": gap_id, "detail": detail}


def _decision_reason(endpoint: str, text: str) -> str:
    name = endpoint.split(".", 1)[-1]
    tokens = set(re.split(r"[_\W]+", name.lower()))
    verbs = sorted(tokens & DECISION_VERBS)
    if verbs:
        return f"name carries '{verbs[0]}'"
    match = DECISION_SOURCE_RE.search(text)
    if match:
        return f"source uses {match.group(1)}"
    return ""


class _Context:
    """Everything the judges read, gathered once."""

    def __init__(self, root: Path, cfg: dict[str, Any], rules: list[dict[str, Any]],
                 mcp: dict[str, dict[str, Any]]):
        self.root = root
        self.cfg = cfg
        self.files = _Files(root)
        self.rules = rules
        self.by_endpoint: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            self.by_endpoint.setdefault(rule["endpoint"], []).append(rule)
        for group in self.by_endpoint.values():
            group.sort(key=lambda r: r["rule"])
        self.mcp = mcp
        self.registry_keys = {c["key"] for c in _registry(root)}
        self.nav = nav_hrefs(root, self.files)
        ui = _ui_files(root)
        self.ui = ui
        self.ui_text = {rel: self.files.text(rel) for rel in ui}
        self.refs = endpoint_refs(self.ui_text)
        self.observed = load_observed(root)
        self.templates = workflow_templates(
            root, cfg.get("workflow_templates_dir", "args/workflow_hitl_templates"))
        self.policy = approval_policy(
            root, cfg.get("approval_policy", "args/agent_approval_policy.yaml"))
        self.adapters = twin_adapters(
            root, cfg.get("twin_adapters_dir", "tools/twin_core/adapters"))
        self.model = platform_model(root, cfg.get("platform_model_dir", "args/platform_model"))
        self.thread_types = _digital_thread_types(self.files)
        self.symbols = _Symbols(root)
        self._nav_page_text: Optional[str] = None

    def renders(self, rule: dict[str, Any]) -> list[str]:
        text = self.files.view_text(rule)
        return [m.group(1) or "<dynamic>" for m in _RENDER_RE.finditer(text)]

    def nav_page_text(self) -> str:
        """Text of every template a nav-linked page renders, with its includes."""
        if self._nav_page_text is None:
            chunks = []
            for rule in self.rules:
                if "GET" not in rule["methods"] or _norm(rule["rule"]) not in self.nav:
                    continue
                for name in self.renders(rule):
                    rel = _template_file(self.files, self.ui, name)
                    if not rel:
                        continue
                    text = self.files.text(rel)
                    chunks.append(text)
                    for inc in _INCLUDE_RE.findall(text):
                        if inc != "base.html":
                            chunks.append(self.files.text(_template_file(self.files, self.ui, inc)))
            self._nav_page_text = "\n".join(chunks)
        return self._nav_page_text

    def approval_tier(self, tool: str) -> tuple[str, bool, str]:
        tier = self.policy["tiers"].get(tool)
        source = "enumerated" if tier else "default_tier"
        tier = tier or self.policy["default"]
        return tier, tier in self.policy["halts"], source


def _feature_rules(ctx: _Context, feature: dict[str, Any],
                   served: bool = False) -> list[dict[str, Any]]:
    """The feature's own routes; with *served*, also the routes of another
    feature that serve it in the console (``served_by``: GUI evidence only)."""
    out = []
    names = list(feature.get("endpoints") or [])
    if served:
        names += [s["endpoint"] for s in feature.get("served_by") or []
                  if s.get("endpoint") not in names]
    for endpoint in names:
        out.extend(ctx.by_endpoint.get(endpoint, []))
    return out


def judge_gui(ctx: _Context, feature: dict[str, Any]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    evidence: list[str] = []
    stale: list[str] = []
    if feature["component"] not in ctx.registry_keys:
        gaps.append(_gap("gui:unregistered-component",
                         f"component '{feature['component']}' is not in "
                         "args/component_registry.yaml"))
    else:
        evidence.append(f"registry entry '{feature['component']}'")
    rules = _feature_rules(ctx, feature, served=True)
    pages = []
    for rule in rules:
        if "GET" in rule["methods"] and "/api/" not in rule["rule"]:
            names = ctx.renders(rule)
            if names:
                pages.append((rule, names))
    if not pages:
        gaps.append(_gap("gui:no-page", "no GET route of this feature renders a template"))
    reached = False
    seen_pages = set()
    for rule, names in pages:
        evidence.append(f"page {rule['rule']} renders {', '.join(sorted(set(names)))}")
        path = _norm(rule["rule"])
        if path in ctx.nav:
            reached = True
            evidence.append(f"nav links {path}")
        elif rule["endpoint"] in refs_in(ctx.nav_page_text()):
            reached = True
            evidence.append(f"{rule['rule']} linked from a nav page")
        elif rule["endpoint"] not in seen_pages and not ctx.refs.get(rule["endpoint"]):
            gaps.append(_gap(f"gui:orphan-page:{rule['endpoint']}",
                             f"{rule['rule']} is linked from no nav entry, template or script"))
        seen_pages.add(rule["endpoint"])
    if pages and not reached:
        gaps.append(_gap("gui:not-in-nav",
                         "no page of this feature is in the nav or one link from it"))
    machine = {m["endpoint"]: m for m in feature.get("machine_endpoints") or []}
    for rule in rules:
        if not MUTATING & set(rule["methods"]):
            continue
        callers = ctx.refs.get(rule["endpoint"], [])
        if rule["endpoint"] in machine:
            if callers:
                stale.append(f"{feature['key']}: machine_endpoints {rule['endpoint']} — a page "
                             f"calls it ({callers[0]}); drop the reason")
            else:
                evidence.append(f"{rule['endpoint']} machine surface (stated reason)")
            continue
        if callers:
            evidence.append(f"{rule['endpoint']} called from {callers[0]}")
        else:
            gaps.append(_gap(f"gui:uncalled:{rule['endpoint']}",
                             f"mutating route {rule['rule']} is called by no page"))
    return {"gaps": gaps, "evidence": evidence, "stale": stale,
            "routes": _route_rows(ctx, feature)}


def _route_rows(ctx: _Context, feature: dict[str, Any]) -> list[dict[str, Any]]:
    """Per route of the feature: the files that name it by id, whether it writes, and
    whether the recorded e2e run saw a page send it (icx-cnf-08)."""
    rows = []
    for rule in _feature_rules(ctx, feature):
        methods = sorted(set(rule["methods"]) - {"HEAD", "OPTIONS"})
        write = bool(MUTATING & set(methods))
        seen = any(ctx.observed.get(f"{m} {rule['endpoint']}", {}).get("page")
                   for m in methods if not write or m in MUTATING)
        rows.append({"endpoint": rule["endpoint"], "rule": rule["rule"], "methods": methods,
                     "by_id": ctx.refs.get(rule["endpoint"], []), "write": write,
                     "observed": seen})
    return sorted(rows, key=lambda r: (r["rule"], r["endpoint"]))


def _decisions(ctx: _Context, feature: dict[str, Any]) -> dict[str, str]:
    """surface -> why it is read as a decision."""
    found: dict[str, str] = {}
    for rule in _feature_rules(ctx, feature):
        if not MUTATING & set(rule["methods"]):
            continue
        why = _decision_reason(rule["endpoint"], ctx.files.view_text(rule))
        if why:
            found.setdefault(rule["endpoint"], why)
    for tool in feature.get("mcp_tools") or []:
        info = ctx.mcp.get(tool) or {}
        if info.get("registered") and info.get("read_only") is False:
            why = _decision_reason(tool, "")
            if why:
                found.setdefault(f"mcp:{tool}", why)
    return found


def judge_workflow(ctx: _Context, feature: dict[str, Any]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    evidence: list[str] = []
    stale: list[str] = []
    errors: list[str] = []
    decisions = _decisions(ctx, feature)
    for decl in feature.get("declared_decisions") or []:
        ev = decl.get("evidence") or {}
        if not ev.get("text") or ev["text"] not in ctx.files.text(str(ev.get("file") or "")):
            errors.append(f"{feature['key']}: declared decision {decl.get('id')} quotes text not "
                          f"found in {ev.get('file')}")
            continue
        if decl.get("endpoint"):
            decisions.setdefault(decl["endpoint"], f"declared ({ev['file']}: \"{ev['text']}\")")
        else:
            gaps.append(_gap(f"workflow:no-endpoint:{decl['id']}",
                             f"a person decides this ({ev['file']}: \"{ev['text']}\") but no "
                             "endpoint takes the decision"))
    for kind in ("stops", "not_decisions"):
        for entry in feature.get(kind) or []:
            surface = entry.get("surface") or entry.get("endpoint")
            if surface not in decisions:
                stale.append(f"{feature['key']}: {kind} {surface} — not read as a decision; "
                             "drop the reason")
                continue
            del decisions[surface]
            label = "stop, immediate" if kind == "stops" else "not a decision"
            evidence.append(f"{surface}: {label} (stated reason)")
    for surface, why in sorted(decisions.items()):
        templates = ctx.templates.get(surface, [])
        good = [t for t in templates if t["manual_role_stage"]]
        if good:
            evidence.append(f"{surface} ({why}) -> template {good[0]['id']} ({good[0]['file']})")
            continue
        if templates:
            gaps.append(_gap(f"workflow:auto-template:{surface}",
                             f"template {templates[0]['id']} decides {surface} with no manual "
                             "stage held by a role"))
            continue
        if surface.startswith("mcp:"):
            tier, halts, source = ctx.approval_tier(surface[4:])
            if halts:
                evidence.append(f"{surface} ({why}) -> approval tier '{tier}' ({source}) halts "
                                f"in {ctx.policy['file']}")
                continue
        gaps.append(_gap(f"workflow:undeclared:{surface}",
                         f"{surface} is a decision ({why}) with no declared workflow template"))
    if not decisions and not gaps and not evidence:
        mutating = sum(1 for r in _feature_rules(ctx, feature) if MUTATING & set(r["methods"]))
        evidence.append(f"{mutating} mutating route(s), none a decision a person takes")
        return {"gaps": [], "evidence": evidence, "stale": stale, "errors": errors, "na": True}
    return {"gaps": gaps, "evidence": evidence, "stale": stale, "errors": errors}


def judge_twin(ctx: _Context, feature: dict[str, Any]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    evidence: list[str] = []
    mine = [a for a in ctx.adapters
            if feature["key"] in a["observes"]
            or (not a["observes"] and a["canvas_key"] == feature["component"])]
    if not mine:
        gaps.append(_gap("twin:no-adapter",
                         f"no @register_twin adapter observes {feature['key']} "
                         f"(canvas_key '{feature['component']}' or observes)"))
    for adapter in mine:
        name = adapter["adapter"]
        evidence.append(f"adapter {name}")
        if adapter["canvas_key"] not in ctx.registry_keys:
            gaps.append(_gap(f"twin:unregistered-canvas:{name}",
                             f"canvas_key '{adapter['canvas_key']}' is not a registry component"))
        if not adapter["thread_ids"]:
            gaps.append(_gap(f"twin:no-thread-ids:{name}", "its violations carry no thread_id"))
        if not adapter["replay"]:
            gaps.append(_gap(f"twin:no-replay:{name}", "it defines no replay()"))
    return {"gaps": gaps, "evidence": evidence}


def judge_architecture(ctx: _Context, feature: dict[str, Any]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    evidence: list[str] = []
    key = feature["key"]
    reqs = sorted((r for r in ctx.model["requirements"] if key in (r.get("features") or [])),
                  key=lambda r: str(r["id"]))
    if not reqs:
        gaps.append(_gap("architecture:no-requirement",
                         f"no requirement in {ctx.cfg.get('platform_model_dir')} names {key}"))
    used: set[str] = set()
    for req in reqs:
        rid = str(req["id"])
        tests = [str(t) for t in req.get("tests") or []]
        # A test is a pytest node: its file AND its function must exist.
        missing = [t for t in tests if resolve_test(ctx.root, t, ctx.symbols)]
        if not tests or missing:
            gaps.append(_gap(f"architecture:untested:{rid}",
                             f"{rid} traces to no existing test" + (f" ({missing[0]} missing)"
                                                                     if missing else "")))
        elements = [str(e) for e in req.get("elements") or []]
        if not elements:
            gaps.append(_gap(f"architecture:no-element:{rid}", f"{rid} names no element"))
        for el_id in elements:
            used.add(el_id)
            el = ctx.model["elements"].get(el_id)
            if el is None:
                gaps.append(_gap(f"architecture:unknown-element:{rid}:{el_id}",
                                 f"{rid} names undeclared element {el_id}"))
                continue
            if ctx.thread_types and str(el.get("type")) not in ctx.thread_types:
                gaps.append(_gap(f"architecture:bad-type:{el_id}",
                                 f"type '{el.get('type')}' is not a digital-thread artifact type"))
            code = [str(c) for c in el.get("code") or []]
            if not code or any(resolve_code(ctx.root, c, ctx.symbols) for c in code):
                gaps.append(_gap(f"architecture:no-code:{el_id}",
                                 f"{el_id} links to no existing code"))
        if tests and not missing and elements:
            evidence.append(f"{rid} -> {', '.join(elements)} -> {', '.join(tests)}")
    for el_id, el in sorted(ctx.model["elements"].items()):
        if key in (el.get("features") or []) and el_id not in used:
            gaps.append(_gap(f"architecture:orphan-element:{el_id}",
                             f"{el_id} belongs to {key} but no requirement traces to it"))
    return {"gaps": gaps, "evidence": evidence}


def judge_ai_native(ctx: _Context, feature: dict[str, Any]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    evidence: list[str] = []
    reads = []
    for tool in sorted(feature.get("mcp_tools") or []):
        info = ctx.mcp.get(tool) or {}
        if not info.get("registered"):
            gaps.append(_gap(f"ai:unregistered:{tool}", f"{tool} is not a registered MCP tool"))
            continue
        if info.get("read_only") is None:
            gaps.append(_gap(f"ai:undeclared:{tool}",
                             f"{tool} has no READ_ONLY_DECLARATIONS entry"))
            continue
        if info["read_only"] and info["tier"] == "read":
            reads.append(tool)
            evidence.append(f"{tool} read-only, tool_authorization tier '{info['tier']}'")
            continue
        tier, halts, source = ctx.approval_tier(tool)
        if halts:
            evidence.append(f"{tool} mutating, approval tier '{tier}' ({source}) halts")
        else:
            gaps.append(_gap(f"ai:ungated-mutation:{tool}",
                             f"{tool} mutates and its approval tier '{tier}' does not halt"))
    if not reads:
        gaps.append(_gap("ai:no-read-tool", "no declared read-only MCP tool serves this feature"))
    return {"gaps": gaps, "evidence": evidence}


JUDGES = {
    "gui": judge_gui,
    "workflow": judge_workflow,
    "twin": judge_twin,
    "architecture": judge_architecture,
    "ai_native": judge_ai_native,
}


_INC_RE = re.compile(r"\{%-?\s*(?:include|extends|import|from)\s+[\"']([^\"']+)[\"']")
_STATIC_RE = re.compile(
    r"url_for\(\s*[\"']static[\"']\s*,\s*filename\s*=\s*[\"']([^\"']+)[\"']"
    r"|src=[\"']/static/([^\"'?#]+)")


def audited_files(ctx: _Context) -> list[str]:
    """The files the audit holds to route ids, derived, never listed (icx-cnf-08).

    The templates every audited feature's pages render (``served_by`` included),
    with everything they include, import or extend (``base.html``, the shell, left
    out), the first-party scripts they load, and every UI file that names an
    audited endpoint: a file is held to the rule from the moment it carries credit.
    """
    owned: set[str] = set()
    for feature in ctx.cfg["features"]:
        owned.update(r["endpoint"] for r in _feature_rules(ctx, feature, served=True))
    todo: list[str] = []
    for rule in ctx.rules:
        if rule["endpoint"] in owned and "GET" in rule["methods"]:
            todo.extend(rel for rel in (_template_file(ctx.files, ctx.ui, n)
                                        for n in ctx.renders(rule)) if rel)
    todo.extend(rel for ep in owned for rel in ctx.refs.get(ep, []))
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel in seen or rel.endswith("/templates/base.html"):
            continue
        seen.add(rel)
        text = ctx.files.text(rel)
        for name in _INC_RE.findall(text):
            found = _template_file(ctx.files, ctx.ui, name)
            if found:
                todo.append(found)
        for a, b in _STATIC_RE.findall(text):
            name = a or b
            if name.startswith("vendor/") or name.endswith(".min.js"):
                continue
            todo.extend(u for u in ctx.ui if u.endswith("/static/" + name))
    seen.discard(ROUTES_GEN_RELPATH)
    return sorted(seen)


def route_id_problems(ctx: _Context, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """What the route-id rule finds: hand-written ``/api/`` paths in audited files,
    endpoint ids no route has, and whether routes.gen.js is the url_map's."""
    from tools.dashboard import routes_gen

    hand = []
    files = audited_files(ctx)
    for rel in files:
        for no, line in enumerate(ctx.files.text(rel).splitlines(), 1):
            if _API_RE.search(line):
                hand.append(f"{rel}:{no}: {line.strip()[:120]}")
    held = set(files)
    unknown = sorted(f"{ep} ({', '.join(f for f in files_ if f in held)})"
                     for ep, files_ in ctx.refs.items()
                     if ep not in ctx.by_endpoint and ep != "static" and held & set(files_))
    return {"audited_files": files, "hand_written": hand, "unknown_ids": unknown,
            "routes_gen": {"file": ROUTES_GEN_RELPATH,
                           "current": routes_gen.current(rules, ctx.root)}}


def load_observed(root: Path) -> dict[str, dict[str, Any]]:
    """``METHOD endpoint`` -> what the recorded e2e run saw (``{"page": bool, ...}``)."""
    try:
        data = json.loads((root / OBSERVED_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(data.get("routes") or {})


def _ownership_errors(ctx: _Context) -> list[str]:
    errors: list[str] = []
    owner: dict[str, str] = {}
    tool_owner: dict[str, str] = {}
    keys = set()
    for feature in ctx.cfg["features"]:
        key = feature.get("key")
        if not key or not feature.get("component"):
            errors.append(f"feature {key!r} needs a key and a component")
            continue
        if key in keys:
            errors.append(f"feature {key} is listed twice")
        keys.add(key)
        for served in feature.get("served_by") or []:
            ev = served.get("evidence") or {}
            if served.get("endpoint") not in ctx.by_endpoint:
                errors.append(f"{key}: served_by {served.get('endpoint')} is not in the url_map")
            if not ev.get("text") or ev["text"] not in ctx.files.text(str(ev.get("file") or "")):
                errors.append(f"{key}: served_by {served.get('endpoint')} quotes text not "
                              f"found in {ev.get('file')}")
        for endpoint in feature.get("endpoints") or []:
            if endpoint not in ctx.by_endpoint:
                errors.append(f"{key}: endpoint {endpoint} is not in the url_map")
            if endpoint in owner:
                errors.append(f"endpoint {endpoint} is owned by {owner[endpoint]} and {key}")
            owner[endpoint] = key
        for tool in feature.get("mcp_tools") or []:
            if tool in tool_owner:
                errors.append(f"MCP tool {tool} is owned by {tool_owner[tool]} and {key}")
            tool_owner[tool] = key
        for rule in feature.get("reasons") or {}:
            if rule not in RULES:
                errors.append(f"{key}: reason for unknown rule {rule!r}")
    for comp, spec in sorted((ctx.cfg.get("components") or {}).items()):
        bps = set(spec.get("blueprints") or [])
        for endpoint in sorted(ctx.by_endpoint):
            bp, _, name = endpoint.partition(".")
            if bp in bps and name != "static" and endpoint not in owner:
                errors.append(f"{comp}: endpoint {endpoint} is owned by no feature")
        for prefix in spec.get("mcp_prefixes") or []:
            for tool in ctx.mcp:
                if tool.startswith(prefix) and tool not in tool_owner:
                    errors.append(f"{comp}: MCP tool {tool} is owned by no feature")
    return errors


def audit(root: Optional[Path] = None, *, rules: Optional[list[dict[str, Any]]] = None,
          mcp: Optional[dict[str, dict[str, Any]]] = None,
          config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Judge every configured feature against the five rules.

    ``rules`` (the url_map) and ``mcp`` (what the MCP registry declares) are
    probed when not given; tests pass them in.
    """
    base = root or BASE_DIR
    cfg = config if config is not None else load_config(base)
    if rules is None:
        rules = probe_rules(base)
    if mcp is None:
        prefixes = [p for spec in (cfg.get("components") or {}).values()
                    for p in spec.get("mcp_prefixes") or []]
        names = {t for f in cfg["features"] for t in f.get("mcp_tools") or []}
        names |= {t for t in mcp_names() if any(t.startswith(p) for p in prefixes)}
        mcp = mcp_evidence(names)
    ctx = _Context(base, cfg, rules, mcp)

    config_errors = _ownership_errors(ctx)
    stale: list[str] = []
    features = []
    for feature in sorted(cfg["features"], key=lambda f: str(f.get("key"))):
        if not feature.get("key") or not feature.get("component"):
            continue
        reasons = feature.get("reasons") or {}
        cells = {}
        for rule in RULES:
            result = JUDGES[rule](ctx, feature)
            stale.extend(result.get("stale", []))
            config_errors.extend(result.get("errors", []))
            gaps = sorted(result["gaps"], key=lambda g: g["id"])
            cell: dict[str, Any] = {"evidence": sorted(set(result["evidence"]))}
            if gaps:
                cell.update(status="gap", gaps=gaps)
            else:
                cell.update(status="n/a" if result.get("na") else "pass", gaps=[])
            if rule in reasons:
                if gaps:
                    cell.update(status="excused", excused=gaps, gaps=[],
                                reason=" ".join(str(reasons[rule]).split()))
                else:
                    stale.append(f"{feature['key']}: reason for '{rule}' excuses nothing — the "
                                 "rule holds without it; drop the reason")
            if "routes" in result:
                cell["routes"] = result["routes"]
            cells[rule] = cell
        features.append({"key": feature["key"], "component": feature["component"],
                         "label": feature.get("label", feature["key"]), "cells": cells})

    by_rule = {rule: sum(len(f["cells"][rule]["gaps"]) for f in features) for rule in RULES}
    routes = [r for f in features for r in f["cells"]["gui"].get("routes", [])]
    writes = [r for r in routes if r["write"] and r["by_id"]]
    return {
        "tool": "feature_audit",
        "version": 1,
        "config": CONFIG_RELPATH,
        "enforcement": str(cfg.get("enforcement") or "warn"),
        "rules": list(RULES),
        "features": features,
        "summary": {
            "features": len(features),
            "gaps_total": sum(by_rule.values()),
            "gaps_by_rule": by_rule,
            "gaps_by_feature": {f["key"]: {r: len(f["cells"][r]["gaps"]) for r in RULES}
                                for f in features},
            "features_conforming": sum(
                1 for f in features if all(not f["cells"][r]["gaps"] for r in RULES)),
            "routes": len(routes),
            "routes_by_id": sum(1 for r in routes if r["by_id"]),
            "routes_observed": sum(1 for r in routes if r["observed"]),
            "writes_by_id": len(writes),
            "writes_observed": sum(1 for r in writes if r["observed"]),
        },
        "route_ids": route_id_problems(ctx, rules),
        "config_errors": sorted(set(config_errors)),
        "stale_reasons": sorted(set(stale)),
    }


# ── census ───────────────────────────────────────────────────────────────────


def gap_keys(report: dict[str, Any]) -> list[str]:
    """Every open gap BY NAME: ``<feature>|<gap id>``."""
    return sorted(f"{f['key']}|{g['id']}" for f in report["features"]
                  for rule in RULES for g in f["cells"][rule]["gaps"])


def read_census(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return sorted(ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#"))


_CENSUS_HEADER = """\
# CUI // SP-CTI
# Feature conformance census -- every OPEN gap against the five rules, BY NAME.
#
# Policy and scope: args/feature_conformance.yaml
# Re-derive:        python tools/conformance/feature_audit.py --census
# The gate:         python tools/conformance/feature_audit.py --check
#
# ENUMERATED, NOT COUNTED: a count can hold while the set churns. A line that
# disappears is a gap someone closed -- prune it and lower `census_max`. A line
# that appears is a new gap; it fails --check until it is reviewed in here.
"""


def render_census(report: dict[str, Any]) -> str:
    return _CENSUS_HEADER + "".join(f"{key}\n" for key in gap_keys(report))


def census_status(report: dict[str, Any], root: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    path = root / cfg.get("census", "args/feature_conformance_census.txt")
    listed = set(read_census(path))
    found = set(gap_keys(report))
    ceiling = int(cfg.get("census_max") or 0)
    return {
        "census_file": path.relative_to(root).as_posix(),
        "registered": len(listed),
        "ceiling": ceiling,
        "unregistered": sorted(found - listed),
        "closed": sorted(listed - found),
        "over_ceiling": len(listed) > ceiling,
    }


# ── markdown ─────────────────────────────────────────────────────────────────


def _cell_label(cell: dict[str, Any]) -> str:
    if cell["status"] == "gap":
        return f"gap ({len(cell['gaps'])})"
    return cell["status"]


def _md(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    out = [
        f"_Generated by `{REGEN_HINT}`. Do not edit between the markers._",
        "",
        (f"{s['features']} features; {s['features_conforming']} meet all five rules; "
         f"{s['gaps_total']} open gaps. Enforcement: `{report['enforcement']}`."),
        "",
        "### Matrix",
        "",
        "| Feature | " + " | ".join(RULE_TITLES[r] for r in RULES) + " |",
        "|---|" + "---|" * len(RULES),
    ]
    for f in report["features"]:
        out.append(f"| {_md(f['label'])} (`{f['key']}`) | "
                   + " | ".join(_cell_label(f["cells"][r]) for r in RULES) + " |")
    out += ["", "### Gaps per rule", "", "| Rule | Open gaps |", "|---|---|"]
    for rule in RULES:
        out.append(f"| {RULE_TITLES[rule]} | {s['gaps_by_rule'][rule]} |")
    out += ["", "### Routes: named by id, seen in e2e", "",
            (f"Of {s['routes']} routes, {s['routes_by_id']} are named by a page by endpoint id "
             f"(`icdevRoute`/`icdevFetch` in tools/dashboard/static/js/routes.gen.js, or `url_for`), "
             f"and a page was seen sending {s['routes_observed']} in the recorded e2e run "
             f"(`{OBSERVED_RELPATH}`). Of the {s['writes_by_id']} writes a page names, "
             f"{s['writes_observed']} were seen: CI's e2e job fails on any it does not see "
             "(`--e2e-gate`). Reads may be credited by id alone."),
            "", "| Feature | Route | Endpoint | Named by id in | Seen in e2e |", "|---|---|---|---|---|"]
    for f in report["features"]:
        for r in f["cells"]["gui"].get("routes", []):
            out.append(f"| `{f['key']}` | `{_md('/'.join(r['methods']) + ' ' + r['rule'])}` | "
                       f"`{r['endpoint']}` | {_md(', '.join(r['by_id'])) or '—'} | "
                       f"{'✓' if r['observed'] else ''} |")
    out += ["", "### Evidence and gaps per feature", ""]
    for f in report["features"]:
        out += [f"#### {_md(f['label'])} (`{f['key']}`, component `{f['component']}`)", ""]
        for rule in RULES:
            cell = f["cells"][rule]
            out.append(f"- **{RULE_TITLES[rule]}: {cell['status']}**")
            for gap in cell["gaps"]:
                out.append(f"  - gap `{gap['id']}`: {_md(gap['detail'])}")
            if cell.get("reason"):
                out.append(f"  - excused: {_md(cell['reason'])}")
            for item in cell["evidence"]:
                out.append(f"  - evidence: {_md(item)}")
        out.append("")
    if report["config_errors"] or report["stale_reasons"]:
        out += ["### Problems", ""]
        out += [f"- config: {_md(e)}" for e in report["config_errors"]]
        out += [f"- stale: {_md(e)}" for e in report["stale_reasons"]]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _markers() -> tuple[str, str]:
    return (f"<!-- BEGIN GENERATED {DOC_MARKER} -->", f"<!-- END GENERATED {DOC_MARKER} -->")


def doc_block(text: str) -> Optional[str]:
    begin, end = _markers()
    if begin not in text or end not in text:
        return None
    return text.split(begin, 1)[1].split(end, 1)[0].strip("\n") + "\n"


def write_doc(report: dict[str, Any], root: Path, cfg: dict[str, Any]) -> list[str]:
    """Rewrite the doc's generated block and the JSON snapshot."""
    begin, end = _markers()
    doc = root / cfg.get("doc", "docs/audits/conformance-cortex-coworker.md")
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    block = f"{begin}\n{render_markdown(report)}{end}"
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        text = head + block + rest.split(end, 1)[1]
    else:
        text = text + ("\n" if text else "") + block + "\n"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(text, encoding="utf-8")
    snap = root / cfg.get("snapshot", "docs/audits/conformance-cortex-coworker.json")
    snap.write_text(snapshot_text(report), encoding="utf-8")
    return [doc.relative_to(root).as_posix(), snap.relative_to(root).as_posix()]


def snapshot_text(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def check(report: dict[str, Any], root: Path, cfg: dict[str, Any]) -> list[str]:
    """Every reason ``--check`` fails: stale doc, snapshot, reason or census."""
    problems = [f"config: {e}" for e in report["config_errors"]]
    problems += [f"stale reason: {e}" for e in report["stale_reasons"]]
    ids = report.get("route_ids") or {}
    problems += [f"hand-written /api/ path in an audited file (call it by endpoint id: "
                 f"icdevRoute/icdevFetch or url_for): {h}" for h in ids.get("hand_written", [])]
    problems += [f"a page names an endpoint id no route has: {u}"
                 for u in ids.get("unknown_ids", [])]
    if not (ids.get("routes_gen") or {}).get("current"):
        problems.append(f"{ROUTES_GEN_RELPATH} is stale against the url_map — run "
                        "python tools/dashboard/routes_gen.py")
    doc = root / cfg.get("doc", "docs/audits/conformance-cortex-coworker.md")
    block = doc_block(doc.read_text(encoding="utf-8")) if doc.exists() else None
    if block != render_markdown(report):
        problems.append(f"{doc.relative_to(root).as_posix()} is stale — run {REGEN_HINT}")
    snap = root / cfg.get("snapshot", "docs/audits/conformance-cortex-coworker.json")
    current = snap.read_text(encoding="utf-8") if snap.exists() else ""
    if current != snapshot_text(report):
        problems.append(f"{snap.relative_to(root).as_posix()} is stale — run {REGEN_HINT}")
    census = census_status(report, root, cfg)
    problems += [f"new gap not in the census: {k}" for k in census["unregistered"]]
    problems += [f"census lists a closed gap (prune it, lower census_max): {k}"
                 for k in census["closed"]]
    if census["over_ceiling"]:
        problems.append(f"census {census['registered']} over census_max {census['ceiling']}")
    return problems


def _audited_prefixes(report: dict[str, Any]) -> set[str]:
    """The literal head (first two segments) of every audited route."""
    out = set()
    for f in report["features"]:
        for r in f["cells"]["gui"].get("routes", []):
            head = _PARAM_RE.split(r["rule"])[0].strip("/").split("/")[:2]
            out.add("/" + "/".join(head))
    return out


def e2e_gate(report: dict[str, Any], run_files: list[Path],
             record: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    """CI's e2e gate (icx-cnf-08): every WRITE the audit credits to a page by id was
    sent by a page in this run (a test's own request does not count), the committed
    record claims no write the run did not see, and no request under an audited route
    prefix matched no route. ``run_files`` are the shards' observed-calls files."""
    seen: dict[str, dict[str, Any]] = {}
    unmatched: set[str] = set()
    for path in run_files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, v in (data.get("routes") or {}).items():
            s = seen.setdefault(key, {"page": False, "test": False})
            s["page"] = s["page"] or bool(v.get("page"))
            s["test"] = s["test"] or bool(v.get("test"))
        unmatched.update(data.get("unmatched") or [])
    missing = []
    writes = 0
    for f in report["features"]:
        for r in f["cells"]["gui"].get("routes", []):
            if not (r["write"] and r["by_id"]):
                continue
            writes += 1
            keys = [f"{m} {r['endpoint']}" for m in r["methods"] if m in MUTATING]
            if not any(seen.get(k, {}).get("page") for k in keys):
                how = " (only a test's own request)" if any(
                    seen.get(k, {}).get("test") for k in keys) else ""
                missing.append(f"{'/'.join(m for m in r['methods'] if m in MUTATING)} "
                               f"{r['rule']} ({r['endpoint']}): named by id in "
                               f"{', '.join(r['by_id'])}, never sent by a page in the e2e "
                               f"run{how}")
    record = load_observed(BASE_DIR) if record is None else record
    stale = [f"{k}: {OBSERVED_RELPATH} says a page sent it; this run did not see it"
             for k, v in sorted(record.items())
             if v.get("page") and k.split(" ", 1)[0] in MUTATING
             and not seen.get(k, {}).get("page")]
    prefixes = _audited_prefixes(report)
    stray = [f"{u}: a request under an audited route that matches no route"
             for u in sorted(unmatched)
             if any(u.split(" ", 1)[-1] == p or u.split(" ", 1)[-1].startswith(p + "/")
                    for p in prefixes)]
    return {"ok": not (missing or stale or stray), "writes": writes, "seen": len(seen),
            "missing": sorted(missing), "stale_record": stale, "unmatched": stray}


def load_snapshot(root: Optional[Path] = None) -> dict[str, Any]:
    """The committed report (what the console page and MCP tool serve)."""
    base = root or BASE_DIR
    cfg = load_config(base)
    path = base / cfg.get("snapshot", "docs/audits/conformance-cortex-coworker.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"no conformance snapshot at {path.name}: {exc}") from exc
    data["source"] = {"snapshot": path.relative_to(base).as_posix(), "regenerate": REGEN_HINT}
    return data


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=inspect.cleandoc(__doc__ or "").splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the report (the default)")
    parser.add_argument("--markdown", action="store_true",
                        help="rewrite the doc's generated tables and the JSON snapshot")
    parser.add_argument("--census", action="store_true", help="rewrite the gap census")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 on a stale doc, snapshot, reason or census")
    parser.add_argument("--e2e-gate", nargs="+", type=Path, metavar="FILE",
                        help="exit 1 unless the e2e run's observed calls (one file per shard) "
                             "include every write the committed snapshot credits to a page")
    args = parser.parse_args(argv)
    if args.e2e_gate:
        try:
            gate = e2e_gate(load_snapshot(), args.e2e_gate)
        except (AuditError, OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps(gate, indent=2, sort_keys=True))
        return 0 if gate["ok"] else 1
    try:
        cfg = load_config()
        report = audit(config=cfg)
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    written = []
    if args.markdown:
        written += write_doc(report, BASE_DIR, cfg)
    if args.census:
        path = BASE_DIR / cfg.get("census", "args/feature_conformance_census.txt")
        path.write_text(render_census(report), encoding="utf-8")
        written.append(path.relative_to(BASE_DIR).as_posix())
    if args.check:
        problems = check(report, BASE_DIR, cfg)
        print(json.dumps({"ok": not problems, "problems": problems,
                          "summary": report["summary"]}, indent=2, sort_keys=True))
        return 1 if problems else 0
    out = dict(report)
    if written:
        out["written"] = written
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
