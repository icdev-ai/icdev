#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-inert-01 — the compliance surface declares nothing it cannot deliver.

Two inert shapes, both of which report success while doing nothing:

  * An MCP handler that imports a symbol its module does not export. The lazy
    ``_import_tool`` returns ``None`` and the handler answers
    ``{"error": "... not available yet", "status": "pending"}`` — a STUB that
    is indistinguishable, to a caller, from a module still being written.
    The card named three; sweeping every call site found FIVE symbols across
    FOUR tools, none of which has ever existed:
        stig_check   -> tools.compliance.stig_checker.check_project
        cssp_assess  -> tools.compliance.cssp_assessor.assess_project
        control_map  -> tools.compliance.control_mapper.map_activity
        cui_mark     -> tools.compliance.cui_marker.mark_file, .mark_content

  * A page route nothing links to. ``/ato-compliance`` and ``/fedramp-20x``
    both render a real template over a real API and were reachable only by
    typing the URL.

Both are asserted against the SOURCE OF TRUTH (the module's own exports, the
rendered navigation) rather than against a hand-copied list — which is why the
sweep saw the fourth tool the card's list could not.

And the handlers are INVOKED, not merely name-checked: resolving the right
symbol and then handing it the wrong argument type is still an inert tool. It
happened here — every other handler in the file passes ``str(DB_PATH)``, and
all three of these callees open with ``path.exists()``.
"""

import re
from pathlib import Path

import pytest

from icdev.core.paths import repo_root

REPO = repo_root(__file__)


# ---------------------------------------------------------------------------
# 1. Every MCP handler resolves the symbol it names
# ---------------------------------------------------------------------------

# (tool name, handler, module path, symbol) — the three the card names.
REWIRED = [
    ("stig_check", "handle_stig_check", "tools.compliance.stig_checker", "run_stig_check"),
    ("cssp_assess", "handle_cssp_assess", "tools.compliance.cssp_assessor", "run_cssp_assessment"),
    ("control_map", "handle_control_map", "tools.compliance.control_mapper", "create_mapping"),
]


@pytest.mark.parametrize("tool,handler,module_path,symbol", REWIRED)
def test_rewired_symbol_exists_in_its_module(tool, handler, module_path, symbol):
    import importlib

    mod = importlib.import_module(module_path)
    assert callable(getattr(mod, symbol, None)), (
        f"{module_path}.{symbol} does not exist — {tool} would answer a stub"
    )


@pytest.mark.parametrize("tool,handler,module_path,symbol", REWIRED)
def test_handler_imports_the_symbol_that_exists(tool, handler, module_path, symbol):
    """The handler names the real export, so _import_tool never returns None."""
    from tools.mcp import compliance_server

    fn = getattr(compliance_server, handler)
    src = __import__("inspect").getsource(fn)
    assert f'"{symbol}"' in src, f"{handler} still resolves a symbol other than {symbol}"


def test_no_compliance_handler_resolves_a_missing_symbol():
    """Sweep EVERY _import_tool call in the server, not only the three fixed.

    A named-by-hand list of three cannot see the fourth. This re-derives the
    finding from the source and fails on any handler naming a symbol its
    module does not export.
    """
    import importlib
    import inspect

    from tools.mcp import compliance_server

    src = inspect.getsource(compliance_server)
    pairs = re.findall(
        r'_import_tool\(\s*"([\w.]+)"\s*,\s*"(\w+)"\s*\)', src
    )
    assert pairs, "no _import_tool call sites found — the sweep would pass vacuously"

    missing = []
    unimportable = []
    for module_path, symbol in pairs:
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:  # a module that will not import is its own finding
            unimportable.append(f"{module_path} ({exc.__class__.__name__})")
            continue
        if getattr(mod, symbol, None) is None:
            missing.append(f"{module_path}.{symbol}")

    assert not missing, "MCP handlers name symbols that do not exist: " + ", ".join(sorted(missing))
    assert not unimportable, "MCP handlers name modules that do not import: " + ", ".join(
        sorted(unimportable)
    )


def test_control_map_schema_matches_create_mapping_signature():
    """The DECLARED parameters must be ones the callee accepts.

    ``control_map`` declared an ``activity`` parameter for a mapper that has
    only ever taken a control id. A schema promising a capability the callee
    cannot honour is the same defect one layer up.
    """
    import inspect

    from tools.compliance.control_mapper import create_mapping
    from tools.mcp.compliance_server import create_server

    server = create_server()
    schema = _tool_schema(server, "control_map")
    declared = set(schema.get("properties", {}))
    accepted = set(inspect.signature(create_mapping).parameters)

    assert "control_id" in declared, "control_map no longer asks for the control it maps"
    assert declared <= accepted, (
        f"control_map declares parameters create_mapping cannot take: {sorted(declared - accepted)}"
    )


def test_stig_check_enum_only_offers_templates_that_exist():
    """Declaring five profiles when one template ships is a declared-but-absent claim."""
    from tools.compliance.stig_checker import _list_stig_templates
    from tools.mcp.compliance_server import create_server

    available = set(_list_stig_templates())
    assert available, "no STIG templates on disk — the assertion would be vacuous"

    schema = _tool_schema(create_server(), "stig_check")
    declared = set(schema["properties"]["stig_id"]["enum"])
    assert declared <= available, (
        f"stig_check offers profiles with no template: {sorted(declared - available)}"
    )


@pytest.mark.parametrize("tool", [t for t, _, _, _ in REWIRED] + ["cui_mark"])
def test_gateway_registry_declares_the_same_schema_as_the_server(tool):
    """``tool_registry`` carries a SECOND copy of each schema, and the gateway
    dispatches from it. A caller reading the gateway's advertised parameters
    and calling a handler that ignores them is the same declared-but-absent
    defect one layer out, so the two copies are pinned together for every tool
    this card rewired.
    """
    from tools.mcp.compliance_server import create_server
    from tools.mcp.tool_registry import TOOL_REGISTRY

    served = _tool_schema(create_server(), tool)
    declared = TOOL_REGISTRY[tool]["input_schema"]

    assert set(declared.get("properties", {})) == set(served.get("properties", {})), (
        f"{tool}: gateway registry and MCP server advertise different parameters"
    )
    assert set(declared.get("required", [])) == set(served.get("required", [])), (
        f"{tool}: gateway registry and MCP server disagree on required parameters"
    )


def _tool_schema(server, name):
    """Pull one tool's input schema off a built MCPServer, however it stores them."""
    tools = getattr(server, "tools", None) or getattr(server, "_tools", None)
    assert tools, "MCPServer exposes no tool registry"
    entry = tools[name] if isinstance(tools, dict) else next(
        t for t in tools if (t.get("name") if isinstance(t, dict) else t.name) == name
    )
    if isinstance(entry, dict):
        return entry.get("input_schema") or entry.get("inputSchema")
    return getattr(entry, "input_schema", None) or getattr(entry, "inputSchema")


# ---------------------------------------------------------------------------
# 1b. INVOKING them — a symbol check cannot see a wrong argument
# ---------------------------------------------------------------------------

_MCP_SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT, directory_path TEXT,
    classification TEXT, impact_level TEXT, created_at TEXT
);
CREATE TABLE project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, control_id TEXT,
    implementation_status TEXT, implementation_description TEXT,
    responsible_role TEXT, evidence_path TEXT, last_assessed TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, stig_id TEXT,
    finding_id TEXT, rule_id TEXT, severity TEXT, title TEXT, description TEXT,
    check_content TEXT, fix_text TEXT, status TEXT, comments TEXT,
    target_type TEXT, assessed_by TEXT, assessed_at TEXT, updated_at TEXT
);
"""

# What a stubbed handler says. If any of these survives, the tool is inert.
STUB_MARKERS = ("not available yet", "module not available")


@pytest.fixture()
def mcp_project(tmp_path, monkeypatch):
    """A throwaway project database + source tree for the MCP handlers.

    Never the live board: ``control_map`` INSERTs, and ``stig_check`` writes
    findings and an output file.
    """
    import sqlite3

    from tools.mcp import compliance_server

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "app.py").write_text("import os\n", encoding="utf-8")

    db = tmp_path / "mcp.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_MCP_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name, directory_path, classification, impact_level, created_at) "
        "VALUES ('probe-1','Probe System',?,'CUI','IL4','2026-01-01T00:00:00Z')",
        (str(proj_dir),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(compliance_server, "DB_PATH", db)
    return {"server": compliance_server, "project_dir": proj_dir, "db": db}


def _assert_not_a_stub(result, tool):
    text = str(result)
    for marker in STUB_MARKERS:
        assert marker not in text, f"{tool} still answers a stub: {text[:200]}"


def test_stig_check_returns_a_real_assessment(mcp_project):
    import sqlite3

    out = mcp_project["server"].handle_stig_check({"project_id": "probe-1"})
    _assert_not_a_stub(out, "stig_check")
    assert set(out) >= {"results", "summary", "gate_result", "output_file"}, sorted(out)
    assert out["results"], "the checklist assessed nothing"
    assert set(out["summary"]) == {"CAT1", "CAT2", "CAT3"}
    assert Path(out["output_file"]).is_file(), "no checklist was written"

    conn = sqlite3.connect(str(mcp_project["db"]))
    persisted = conn.execute("SELECT COUNT(*) FROM stig_findings").fetchone()[0]
    conn.close()
    assert persisted == len(out["results"]), "findings never reached the database"


def test_stig_check_still_accepts_the_old_stig_profile_alias(mcp_project):
    """The MCP parameter was renamed to the checker's own `stig_id`; a caller
    still sending the old name must not silently fall back to the default."""
    out = mcp_project["server"].handle_stig_check(
        {"project_id": "probe-1", "stig_profile": "webapp"}
    )
    _assert_not_a_stub(out, "stig_check")
    assert "stig_webapp_" in Path(out["output_file"]).name


def test_stig_check_gate_is_evaluated_only_when_asked(mcp_project):
    off = mcp_project["server"].handle_stig_check({"project_id": "probe-1"})
    on = mcp_project["server"].handle_stig_check({"project_id": "probe-1", "gate": True})
    assert off["gate_result"]["evaluated"] is False
    assert on["gate_result"]["evaluated"] is True


def test_cssp_assess_returns_a_real_assessment(mcp_project):
    out = mcp_project["server"].handle_cssp_assess(
        {"project_id": "probe-1", "functional_area": "Identify"}
    )
    _assert_not_a_stub(out, "cssp_assess")
    assert out.get("summary") or out.get("results"), f"no CSSP result in {sorted(out)}"


def test_control_map_records_a_real_mapping(mcp_project):
    import sqlite3

    out = mcp_project["server"].handle_control_map(
        {
            "project_id": "probe-1",
            "control_id": "ac-2",
            "implementation_status": "implemented",
            "description": "Account management via the platform IdP",
            "responsible_role": "ISSO",
        }
    )
    _assert_not_a_stub(out, "control_map")
    assert out["control_id"] == "AC-2"
    assert out["mapping_id"] is not None

    conn = sqlite3.connect(str(mcp_project["db"]))
    row = conn.execute(
        "SELECT control_id, implementation_status, responsible_role FROM project_controls"
    ).fetchone()
    conn.close()
    assert row == ("AC-2", "implemented", "ISSO"), "the mapping never reached the database"


def test_cui_mark_marks_a_real_file(mcp_project):
    target = mcp_project["project_dir"] / "app.py"
    before = target.read_text(encoding="utf-8")

    dry = mcp_project["server"].handle_cui_mark({"file_path": str(target), "dry_run": True})
    _assert_not_a_stub(dry, "cui_mark")
    assert dry["marked"] is True
    assert target.read_text(encoding="utf-8") == before, "dry_run wrote to the file"

    wet = mcp_project["server"].handle_cui_mark({"file_path": str(target)})
    assert wet["marked"] is True
    assert "CUI" in target.read_text(encoding="utf-8")

    # A file already marked is "nothing to do", never a failure — and the two
    # must not read alike.
    again = mcp_project["server"].handle_cui_mark({"file_path": str(target)})
    assert again["marked"] is False
    assert again["already_marked"] is True


def test_cui_mark_names_an_unsupported_extension_rather_than_silently_passing(mcp_project):
    odd = mcp_project["project_dir"] / "data.parquet"
    odd.write_bytes(b"\x00")
    out = mcp_project["server"].handle_cui_mark({"file_path": str(odd)})
    assert out["marked"] is False
    assert "Unsupported extension" in out["error"]


# ---------------------------------------------------------------------------
# 2. No page route that nothing links to
# ---------------------------------------------------------------------------

ORPHANS_CLOSED = ["/ato-compliance", "/fedramp-20x"]


def _all_template_text():
    roots = [REPO / "tools" / "dashboard" / "templates"]
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for root in roots
        if root.is_dir()
        for p in root.rglob("*.html")
    )


@pytest.mark.parametrize("route", ORPHANS_CLOSED)
def test_page_route_is_linked_from_a_template(route):
    """An href, not merely a fetch() of its API — a page you cannot reach is inert."""
    text = _all_template_text()
    assert text, "no dashboard templates found"
    assert re.search(rf'href="{re.escape(route)}(/|"|\?)', text), (
        f"{route} is still an orphan: no template links to it"
    )


@pytest.mark.parametrize("route", ORPHANS_CLOSED)
def test_page_route_still_exists(route):
    """Linking, not deleting — assert the route survived the fix."""
    app_src = (REPO / "tools" / "dashboard" / "app.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert f'@app.route("{route}")' in app_src, f"{route} was removed rather than linked"


def test_linked_orphans_appear_in_the_compliance_nav_dropdown():
    """The nav's active-path list must know about them, or they never highlight."""
    base = (REPO / "tools" / "dashboard" / "templates" / "base.html").read_text(
        encoding="utf-8", errors="replace"
    )
    for route in ORPHANS_CLOSED:
        assert f"'{route}'" in base, f"{route} missing from the nav active-path list"
