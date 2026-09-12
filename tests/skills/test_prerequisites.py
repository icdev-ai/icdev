# CUI // SP-CTI
"""Skill `prerequisites:` frontmatter and the dry-run report (xrv-route-02).

A skill's documented commands shell out to external binaries. Before this
change the only way to learn that ``trivy`` was not installed was to run
``--exec icdev-secure`` and watch step 4 fail; now the verdict is printed before
step 1 runs.

What these tests pin, in order of how easy each is to break silently:

  * the frontmatter field is PARSED, in all three YAML spellings, and the
    committed ``registry.json`` cache cannot serve a copy that predates it;
  * ``absent``, ``unmeasurable`` and ``undeclared`` are never merged -- the
    ``bandit`` case is the reason, because ``args/tool_index.yaml`` excludes it
    BY NAME (it is invoked as ``python -m bandit``, so PATH cannot answer) and
    reporting that as ``absent`` would call a working tool missing;
  * ``not_declared`` is not ``satisfied``;
  * and NOTHING REFUSES -- an absent prerequisite changes the report and not the
    steps, the exit code or the scope enforcement.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tools.skills import invoke as inv
from tools.skills import registry as reg

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURE_CARD = REPO_ROOT / ".agents" / "skills" / "icdev-secure" / "SKILL.md"

#: The four icdev-secure declares. bandit is deliberately among them even though
#: the tool index cannot PATH-probe it -- a prerequisite list that quietly omits
#: the tools nothing can measure is a list that understates what a skill needs.
SECURE_PREREQS = ["trivy", "bandit", "detect-secrets", "pip-audit"]


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestFrontmatterParsing:
    def test_block_list(self):
        fm, _ = reg._parse_frontmatter(
            "---\nname: x\nprerequisites:\n  - trivy\n  - pip-audit\n---\nbody\n")
        assert reg._as_list(fm.get("prerequisites")) == ["trivy", "pip-audit"]

    def test_inline_list(self):
        fm, _ = reg._parse_frontmatter(
            "---\nname: x\nprerequisites: [trivy, pip-audit]\n---\nbody\n")
        assert reg._as_list(fm.get("prerequisites")) == ["trivy", "pip-audit"]

    def test_comma_separated_scalar(self):
        fm, _ = reg._parse_frontmatter(
            "---\nname: x\nprerequisites: trivy, pip-audit\n---\nbody\n")
        assert reg._as_list(fm.get("prerequisites")) == ["trivy", "pip-audit"]

    def test_absent_field_is_an_empty_list_not_an_error(self):
        fm, _ = reg._parse_frontmatter("---\nname: x\n---\nbody\n")
        assert reg._as_list(fm.get("prerequisites")) == []

    def test_parse_skill_carries_the_field(self, tmp_path, monkeypatch):
        # parse_skill records a repo-relative `path`, so the fixture root has to
        # be the one it relativises against.
        monkeypatch.setattr(reg, "BASE_DIR", tmp_path)
        card = tmp_path / "icdev-fake"
        card.mkdir()
        (card / "SKILL.md").write_text(
            "---\nname: icdev-fake\nprerequisites:\n  - git\n---\n# body\n",
            encoding="utf-8")
        entry = reg.parse_skill(card)
        assert entry["prerequisites"] == ["git"]

    def test_a_skill_without_the_field_still_parses(self):
        entry = reg.load_registry().get("skills", {}).get("icdev-status")
        assert entry is not None
        assert entry.get("prerequisites") == []


class TestRegistryCache:
    def test_schema_version_was_bumped_past_the_scoping_fields(self):
        """registry.json is COMMITTED, so an un-bumped version serves a cache
        with no `prerequisites` key and the invoker reports a skill as needing
        nothing -- failing silently in the reassuring direction."""
        assert reg.SCHEMA_VERSION >= 3

    def test_a_stale_cache_is_rebuilt_rather_than_trusted(self, tmp_path,
                                                          monkeypatch):
        stale = tmp_path / "registry.json"
        stale.write_text(json.dumps({
            "skills": {"icdev-secure": {"name": "icdev-secure"}},
            "count": 1, "schema_version": reg.SCHEMA_VERSION - 1,
        }), encoding="utf-8")
        monkeypatch.setattr(reg, "REGISTRY_PATH", stale)
        loaded = reg.load_registry()
        assert loaded["schema_version"] == reg.SCHEMA_VERSION
        assert "prerequisites" in loaded["skills"]["icdev-secure"]


# ---------------------------------------------------------------------------
# icdev-secure's declaration
# ---------------------------------------------------------------------------


class TestIcdevSecureDeclaration:
    def test_the_card_declares_its_four_binaries(self):
        fm, _ = reg._parse_frontmatter(SECURE_CARD.read_text(encoding="utf-8"))
        assert reg._as_list(fm.get("prerequisites")) == SECURE_PREREQS

    def test_the_registry_agrees_with_the_card(self):
        entry = reg.load_registry()["skills"]["icdev-secure"]
        assert entry["prerequisites"] == SECURE_PREREQS

    def test_every_declared_name_is_known_to_the_tool_index(self):
        """Either declared or deliberately excluded -- never a typo.

        A prerequisite the index has never heard of can only be reported as
        `undeclared`, which is useless to an operator; it is a defect in the
        card, and this is where it is caught.
        """
        from tools.dx import tool_index

        index = tool_index.load_index()
        known = {e.get("name") for e in tool_index.entries(index)}
        excluded = {e.get("name") for e in (index.get("excluded") or [])}
        for name in SECURE_PREREQS:
            assert name in known or name in excluded, \
                f"{name} is in neither tools: nor excluded: of args/tool_index.yaml"

    def test_bandit_is_the_excluded_one_and_that_is_deliberate(self):
        """The four are NOT one population, which is the whole reason the
        report has three verdicts rather than a boolean."""
        from tools.dx import tool_index

        index = tool_index.load_index()
        excluded = {e.get("name"): e.get("reason")
                    for e in (index.get("excluded") or [])}
        assert "bandit" in excluded
        assert str(excluded["bandit"]).strip(), \
            "an exclusion without a reason cannot be reported to an operator"


# ---------------------------------------------------------------------------
# resolve_prerequisites -- live card beats the committed cache
# ---------------------------------------------------------------------------


class TestResolvePrerequisites:
    def test_reads_the_live_card_over_a_stale_cached_entry(self, tmp_path):
        card_dir = tmp_path / ".agents" / "skills" / "icdev-fake"
        card_dir.mkdir(parents=True)
        (card_dir / "SKILL.md").write_text(
            "---\nname: icdev-fake\nprerequisites:\n  - trivy\n---\n# body\n",
            encoding="utf-8")
        entry = {"name": "icdev-fake", "prerequisites": ["stale-name"],
                 "path": ".agents/skills/icdev-fake/SKILL.md"}
        assert inv.resolve_prerequisites(entry, root=tmp_path) == ["trivy"]

    def test_falls_back_to_the_cache_when_the_card_is_unreadable(self, tmp_path):
        entry = {"name": "icdev-fake", "prerequisites": ["pip-audit"],
                 "path": ".agents/skills/icdev-gone/SKILL.md"}
        assert inv.resolve_prerequisites(entry, root=tmp_path) == ["pip-audit"]

    def test_a_card_with_no_declaration_resolves_empty(self):
        entry = reg.load_registry()["skills"]["icdev-status"]
        assert inv.resolve_prerequisites(entry) == []


# ---------------------------------------------------------------------------
# probe_prerequisites -- four verdicts, none merged
# ---------------------------------------------------------------------------


class TestProbeVerdicts:
    def test_nothing_declared_is_not_satisfied(self):
        report = inv.probe_prerequisites([])
        assert report["verdict"] == inv.PREREQ_VERDICT_NOT_DECLARED
        assert report["declared"] == 0
        assert report["verdict"] != inv.PREREQ_VERDICT_SATISFIED

    def test_an_unknown_name_is_undeclared_not_absent(self):
        report = inv.probe_prerequisites(["definitely-not-a-declared-tool"])
        tool = report["tools"][0]
        assert tool["status"] == inv.PREREQ_UNDECLARED
        assert "args/tool_index.yaml" in tool["reason"]
        assert report["verdict"] == inv.PREREQ_VERDICT_UNMEASURABLE

    def test_an_excluded_name_is_unmeasurable_and_carries_the_reason(self):
        """`bandit` works on this host via `python -m bandit`. Calling it
        `absent` because PATH has no bandit.exe is a fabricated finding."""
        report = inv.probe_prerequisites(["bandit"])
        tool = report["tools"][0]
        assert tool["status"] == inv.PREREQ_UNMEASURABLE
        assert tool["status"] != inv.PREREQ_ABSENT
        assert tool["basis"] == "excluded_from_index"
        assert "PYTHON MODULE" in tool["reason"]

    def test_a_declared_tool_missing_from_path_is_absent(self, tmp_path,
                                                         monkeypatch):
        """PATH stripped to an empty directory -- deterministic on any host."""
        monkeypatch.setenv("PATH", str(tmp_path))
        report = inv.probe_prerequisites(["trivy"])
        tool = report["tools"][0]
        assert tool["status"] == inv.PREREQ_ABSENT
        assert tool["path"] is None
        assert report["verdict"] == inv.PREREQ_VERDICT_MISSING

    def test_a_present_tool_carries_its_version_and_path(self, monkeypatch):
        from tools.dx import tool_index

        monkeypatch.setattr(tool_index, "probe", lambda name, index=None: {
            "name": name, "status": tool_index.STATUS_PRESENT,
            "path": "/usr/bin/trivy", "version": "0.69.3", "optional": True,
            "reason": None, "used_by": ["tools/security/container_scanner.py"]})
        report = inv.probe_prerequisites(["trivy"])
        tool = report["tools"][0]
        assert tool["status"] == inv.PREREQ_PRESENT
        assert tool["version"] == "0.69.3"
        assert tool["basis"] == "path_probe"
        assert report["verdict"] == inv.PREREQ_VERDICT_SATISFIED

    def test_on_path_but_silent_is_unmeasurable_not_absent(self, monkeypatch):
        """A broken install and a missing one are different repairs."""
        from tools.dx import tool_index

        monkeypatch.setattr(tool_index, "probe", lambda name, index=None: {
            "name": name, "status": tool_index.STATUS_UNMEASURABLE,
            "path": "/usr/bin/trivy", "version": None, "optional": True,
            "reason": tool_index.REASON_EXIT, "used_by": []})
        tool = inv.probe_prerequisites(["trivy"])["tools"][0]
        assert tool["status"] == inv.PREREQ_UNMEASURABLE
        assert tool["basis"] == tool_index.REASON_EXIT

    def test_an_unreadable_index_is_unmeasurable_for_every_name(self,
                                                                monkeypatch):
        from tools.dx import tool_index

        def _boom(*args, **kwargs):
            raise tool_index.ToolIndexError("args/tool_index.yaml is missing")

        monkeypatch.setattr(tool_index, "load_index", _boom)
        report = inv.probe_prerequisites(["trivy", "pip-audit"])
        assert report["verdict"] == inv.PREREQ_VERDICT_UNMEASURABLE
        assert report["unmeasurable"] == 2
        assert report["absent"] == 0, \
            "an unreadable declaration says nothing about the host"
        assert all(t["basis"] == "index_unreadable" for t in report["tools"])

    def test_counts_add_up_to_the_declared_total(self):
        report = inv.probe_prerequisites(SECURE_PREREQS)
        assert (report["present"] + report["absent"] + report["unmeasurable"]
                + report["undeclared"]) == report["declared"] == 4


# ---------------------------------------------------------------------------
# The dry-run report
# ---------------------------------------------------------------------------


class TestDryRunReport:
    def test_dry_run_json_carries_the_block(self):
        result = inv.invoke_skill("icdev-secure", [], dry_run=True)
        assert result["prerequisites"]["declared"] == 4
        assert [t["name"] for t in result["prerequisites"]["tools"]] == \
            SECURE_PREREQS

    def test_dry_run_names_an_absent_tool_by_name(self, tmp_path, monkeypatch,
                                                 capsys):
        monkeypatch.setenv("PATH", str(tmp_path))
        rc = inv.main(["--dry-run", "icdev-secure"])
        out = capsys.readouterr().out
        assert "ABSENT" in out
        assert "trivy" in out
        # And the absent line says what it COSTS, not merely that it is absent.
        assert "fail mid-run" in out
        assert rc == 0, "a missing prerequisite reports; it does not refuse"

    def test_the_report_is_printed_before_the_first_step(self, tmp_path,
                                                        monkeypatch, capsys):
        """A prerequisite verdict printed after the run is a post-mortem."""
        monkeypatch.setenv("PATH", str(tmp_path))
        inv.main(["--dry-run", "icdev-secure"])
        out = capsys.readouterr().out
        assert "prerequisites:" in out
        assert out.index("prerequisites:") < out.index("step 1")

    def test_an_undeclared_prerequisite_is_named_as_such(self):
        lines = inv.prerequisite_lines(
            inv.probe_prerequisites(["no-such-binary-anywhere"]))
        assert any("UNDECLARED" in line for line in lines)

    def test_no_declaration_says_not_measured_rather_than_ok(self):
        lines = inv.prerequisite_lines(inv.probe_prerequisites([]))
        assert len(lines) == 1
        assert "none declared" in lines[0]
        assert "not measured" in lines[0]

    def test_show_prints_the_declared_list(self, capsys):
        inv.main(["--show", "icdev-secure"])
        out = capsys.readouterr().out
        assert "Prerequisites:" in out
        for name in SECURE_PREREQS:
            assert name in out


class TestNothingRefuses:
    def test_an_absent_prerequisite_changes_no_step(self, tmp_path, monkeypatch):
        """The steps, their order and their would_run verdicts are untouched."""
        before = inv.invoke_skill("icdev-secure", [], dry_run=True)
        monkeypatch.setenv("PATH", str(tmp_path))
        after = inv.invoke_skill("icdev-secure", [], dry_run=True)
        assert after["prerequisites"]["absent"] >= 1, \
            "the fixture did not actually strip PATH"
        assert [s["command"] for s in after["steps"]] == \
            [s["command"] for s in before["steps"]]
        assert [s["would_run"] for s in after["steps"]] == \
            [s["would_run"] for s in before["steps"]]
        assert after["blocked_count"] == before["blocked_count"] == 0


# ---------------------------------------------------------------------------
# Structural: the probe goes through the ONE index
# ---------------------------------------------------------------------------


class TestOneIndexOnly:
    """Read from the SOURCE, because a behavioural test cannot see this.

    ``probe_prerequisites`` answers correctly today whether it asks the tool
    index or calls ``shutil.which`` itself; the difference only shows up on the
    host where a second resolver disagrees with the first -- which is the defect
    xrv-route-01 built the index to end (two binaries called ``helm`` on this
    machine, and ``command -v`` and ``shutil.which`` returning different ones).
    """

    def _func(self, name: str) -> ast.FunctionDef:
        tree = ast.parse(Path(inv.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found in {inv.__file__}")

    def test_probe_does_not_call_shutil_which(self):
        func = self._func("probe_prerequisites")
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) and node.attr == "which":
                raise AssertionError(
                    "probe_prerequisites resolves a binary itself; it must ask "
                    "tools/dx/tool_index.py, which is the one index")
            if isinstance(node, ast.Name) and node.id == "which":
                raise AssertionError("bare which() call in probe_prerequisites")

    def test_probe_imports_the_tool_index(self):
        func = self._func("probe_prerequisites")
        imported = [n for n in ast.walk(func)
                    if isinstance(n, ast.ImportFrom) and "tool_index" in
                    [a.name for a in n.names]]
        assert imported, "probe_prerequisites must import tools.dx.tool_index"

    def test_invoke_skill_probes_before_running_steps(self):
        """Source order, because a report computed after the loop could still
        print first and read identically in every passing test."""
        src = Path(inv.__file__).read_text(encoding="utf-8")
        body = src[src.index("def invoke_skill("):]
        body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
        assert "probe_prerequisites(" in body
        assert body.index("probe_prerequisites(") < body.index("for idx, cmd in")


@pytest.mark.parametrize("name", SECURE_PREREQS)
def test_each_declared_prerequisite_gets_a_verdict(name):
    """No prerequisite is silently dropped -- four in, four verdicts out."""
    report = inv.probe_prerequisites([name])
    assert len(report["tools"]) == 1
    assert report["tools"][0]["name"] == name
    assert report["tools"][0]["status"] in {
        inv.PREREQ_PRESENT, inv.PREREQ_ABSENT, inv.PREREQ_UNMEASURABLE,
        inv.PREREQ_UNDECLARED,
    }
