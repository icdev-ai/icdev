# CUI // SP-CTI
"""Unit tests for progressive skill disclosure (hgx-sess-02).

DB-independent: ``skills_lifecycle._connect`` is redirected to an in-memory
sqlite connection that performs the same ``%s`` -> ``?`` translation the real
storage layer does. The registry is stubbed per-test so the assertions describe
behaviour rather than whatever happens to sit in ``.agents/skills`` today.
"""
from __future__ import annotations

import sqlite3

import pytest

import tools.agent_runtime.skill_tools as st
import tools.agent_runtime.skills_lifecycle as sl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
class _Conn:
    """Minimal storage-layer stand-in (translates %s placeholders)."""

    def __init__(self) -> None:
        self._c = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self) -> None:
        self._c.commit()


@pytest.fixture()
def db(monkeypatch):
    conn = _Conn()
    sl._ensure_schema(conn)
    monkeypatch.setattr(sl, "_connect", lambda c=None: conn if c is None else c)
    return conn


@pytest.fixture()
def skills_tree(tmp_path, monkeypatch):
    """A two-skill .agents/skills tree with the registry pointed at it."""
    root = tmp_path
    entries = {}
    for name, desc, body in (
        ("icdev-alpha", "Do the alpha thing.", "# Alpha\n" + "alpha body line\n" * 20),
        ("icdev-auto-beta", "Do the beta thing.", "# Beta\nbeta body\n"),
    ):
        d = root / ".agents" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}",
            encoding="utf-8",
        )
        # Backslash separators on purpose: registry.json is a COMMITTED cache and
        # the one on main was generated on Windows, so POSIX checkouts read
        # exactly this shape.
        entries[name] = {
            "name": name,
            "description": desc,
            "path": ".agents\\skills\\%s\\SKILL.md" % name,
        }
    monkeypatch.setattr(st, "_repo_root", lambda: root)
    monkeypatch.setattr(st, "_skill_entries", lambda: entries)
    return root


def _register(db, name: str) -> None:
    db.execute(
        "INSERT INTO sag_skill_registry (name, use_count, status) VALUES (%s, 0, 'active')",
        (name,),
    )
    db.commit()


def _use_count(db, name: str) -> int:
    row = db.execute(
        "SELECT use_count FROM sag_skill_registry WHERE name = %s", (name,)
    ).fetchone()
    return int(row[0]) if row else -1


# ---------------------------------------------------------------------------
# list_skills — the cheap half of the disclosure
# ---------------------------------------------------------------------------
def test_list_skills_names_and_descriptions(skills_tree):
    out = st.list_skills()
    assert "icdev-alpha - Do the alpha thing." in out
    assert "icdev-auto-beta - Do the beta thing." in out
    assert "2 skills available" in out


def test_list_skills_never_reads_a_body(skills_tree, monkeypatch):
    """The listing must not open a SKILL.md — that is what bounds its cost."""

    def _boom(_path):
        raise AssertionError("list_skills read a skill body")

    monkeypatch.setattr(st, "_read_skill_text", _boom)
    assert "icdev-alpha" in st.list_skills()


def test_list_skills_cost_is_independent_of_body_size(tmp_path, monkeypatch):
    """Growing every body 1000x must not change the listing by one character."""
    entries = {
        "icdev-a": {"name": "icdev-a", "description": "A.", "path": "x/SKILL.md"},
        "icdev-b": {"name": "icdev-b", "description": "B.", "path": "y/SKILL.md"},
    }
    monkeypatch.setattr(st, "_skill_entries", lambda: entries)
    small = st.list_skills()
    for e in entries.values():
        e["body_line_count"] = 10_000_000
    assert st.list_skills() == small


def test_list_skills_truncates_a_runaway_description(monkeypatch):
    entries = {"icdev-x": {"name": "icdev-x", "description": "z" * 5_000, "path": ""}}
    monkeypatch.setattr(st, "_skill_entries", lambda: entries)
    out = st.list_skills()
    assert "…" in out
    assert len(out) < 1_000


def test_list_skills_caps_total_size(monkeypatch):
    entries = {
        f"icdev-{i:04d}": {
            "name": f"icdev-{i:04d}", "description": "d" * 200, "path": "",
        }
        for i in range(2_000)
    }
    monkeypatch.setattr(st, "_skill_entries", lambda: entries)
    out = st.list_skills()
    assert len(out) <= st._MAX_LIST_CHARS + 500
    assert "more omitted" in out


def test_list_skills_degrades_when_registry_unavailable(monkeypatch):
    monkeypatch.setattr(st, "_skill_entries", dict)
    assert "no skills indexed" in st.list_skills()


# ---------------------------------------------------------------------------
# load_skill — the on-demand half
# ---------------------------------------------------------------------------
def test_load_skill_returns_body(skills_tree):
    out = st.load_skill("icdev-alpha")
    assert "# skill: icdev-alpha" in out
    assert "# source: .agents/skills/icdev-alpha/SKILL.md" in out
    assert "alpha body line" in out


def test_load_skill_resolves_bare_and_slashed_names(skills_tree):
    assert "alpha body line" in st.load_skill("alpha")
    assert "alpha body line" in st.load_skill("/icdev-alpha")
    assert "alpha body line" in st.load_skill("ICDEV-Alpha")
    assert "beta body" in st.load_skill("beta")


def test_load_skill_unknown_name_lists_alternatives(skills_tree):
    out = st.load_skill("nope")
    assert out.startswith("error: no skill named 'nope'")
    assert "icdev-alpha" in out


def test_load_skill_requires_a_name(skills_tree):
    assert st.load_skill("").startswith("error: 'name' is required")


def test_load_skill_reports_a_missing_file(skills_tree, monkeypatch):
    monkeypatch.setattr(st, "_skill_md_path", lambda *_a, **_k: None)
    assert "not present in this checkout" in st.load_skill("icdev-alpha")


def test_load_skill_truncates_an_oversized_body(skills_tree, monkeypatch):
    monkeypatch.setattr(st, "_MAX_BODY_CHARS", 50)
    out = st.load_skill("icdev-alpha")
    assert "truncated at 50 characters" in out


def test_load_skill_normalises_crlf(tmp_path, monkeypatch):
    p = tmp_path / "SKILL.md"
    with open(p, "wb") as fh:
        fh.write(b"line one\r\nline two\r\n")
    assert st._read_skill_text(p) == "line one\nline two\n"


# ---------------------------------------------------------------------------
# record_use — the defect this closes
# ---------------------------------------------------------------------------
def test_load_skill_increments_use_count(skills_tree, db):
    _register(db, "icdev-auto-beta")
    assert _use_count(db, "icdev-auto-beta") == 0
    st.load_skill("icdev-auto-beta")
    assert _use_count(db, "icdev-auto-beta") == 1
    st.load_skill("icdev-auto-beta")
    assert _use_count(db, "icdev-auto-beta") == 2


def test_load_skill_stamps_last_activity(skills_tree, db):
    """The curator's idle sweep grades last_activity_at — a load must move it."""
    _register(db, "icdev-auto-beta")
    db.execute(
        "UPDATE sag_skill_registry SET last_activity_at = %s WHERE name = %s",
        ("2000-01-01T00:00:00+00:00", "icdev-auto-beta"),
    )
    db.commit()
    st.load_skill("icdev-auto-beta")
    row = db.execute(
        "SELECT last_activity_at FROM sag_skill_registry WHERE name = %s",
        ("icdev-auto-beta",),
    ).fetchone()
    assert not str(row[0]).startswith("2000-")


def test_record_use_gets_the_canonical_name(skills_tree, db):
    """An alias must credit the registry key, not what the model happened to type."""
    _register(db, "icdev-auto-beta")
    st.load_skill("beta")
    assert _use_count(db, "icdev-auto-beta") == 1


def test_listing_records_no_use(skills_tree, db):
    _register(db, "icdev-auto-beta")
    st.list_skills()
    assert _use_count(db, "icdev-auto-beta") == 0


def test_load_skill_survives_a_dead_recorder(skills_tree, monkeypatch):
    def _boom(_name):
        raise RuntimeError("db down")

    monkeypatch.setattr(sl, "record_use", _boom)
    assert "alpha body line" in st.load_skill("icdev-alpha")


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------
def test_handlers_match_the_agent_loop_contract(skills_tree):
    tools, handlers = st.build_skill_toolset()
    assert {t["function"]["name"] for t in tools} == {"list_skills", "load_skill"}
    assert set(handlers) == {"list_skills", "load_skill"}
    for t in tools:
        assert t["is_read_only"] is True
    assert "icdev-alpha" in handlers["list_skills"]({}, None)
    assert "alpha body line" in handlers["load_skill"]({"name": "icdev-alpha"}, None)


def test_load_skill_handler_tolerates_a_missing_argument(skills_tree):
    _tools, handlers = st.build_skill_toolset()
    assert handlers["load_skill"]({}, None).startswith("error:")


def test_exposed_in_the_builtin_toolset():
    """Always available — the listing is cheap, so it needs no opt-in bundle."""
    import tools.agent_runtime.builtin_tools as bt

    tools, handlers = bt.build_builtin_toolset()
    names = {t["function"]["name"] for t in tools}
    assert {"list_skills", "load_skill"} <= names
    assert {"list_skills", "load_skill"} <= set(handlers)
    assert {"list_skills", "load_skill"} <= set(bt.builtin_tool_names())


def test_exposed_in_the_skills_bundle():
    from tools.agent_runtime import toolsets

    bundles = {b["name"]: b for b in toolsets.list_bundles()}
    assert bundles["skills"]["mutating"] is False
    assert set(bundles["skills"]["tools"]) == {"list_skills", "load_skill"}
    registry = toolsets.build_registry_for_bundles(["skills"])
    assert set(registry) == {"list_skills", "load_skill"}
    # Read-only, so the dispatch safety gate lets them through unconditionally.
    assert all(spec.read_only for spec in registry.values())


def test_exposed_in_the_ace_registry():
    import tools.ace.agent_tools as at

    assert {"list_skills", "load_skill"} <= set(at._SCHEMAS)
    reg = at.AgentToolRegistry(spec=object(), instance_id="i-1")
    tools, handlers = reg.build(["list_skills", "load_skill"])
    assert {t["function"]["name"] for t in tools} == {"list_skills", "load_skill"}
    assert set(handlers) == {"list_skills", "load_skill"}
