# CUI // SP-CTI
"""tools/dx/tool_index.py -- the declared external-binary index (xrv-route-01).

The three states are the whole point, so each is proven against a REAL
subprocess on a temporary PATH rather than against a monkeypatched
``subprocess.run``: a fake that returns whatever we tell it proves only that
the code can read a tuple. A fake binary that answers proves ``present``; the
SAME fake with a non-zero exit proves ``unmeasurable``; an empty PATH proves
``absent``.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dx import tool_index  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: a real executable on a temporary PATH
# ---------------------------------------------------------------------------


def _make_fake(directory: Path, name: str, message: str, exit_code: int = 0) -> Path:
    """Write a genuinely executable stub that prints ``message`` and exits."""
    if os.name == "nt":
        target = directory / f"{name}.cmd"
        body = f"@echo off\r\necho {message}\r\nexit /b {exit_code}\r\n"
        target.write_text(body, encoding="utf-8", newline="")
    else:
        target = directory / name
        body = f'#!/bin/sh\necho "{message}"\nexit {exit_code}\n'
        target.write_text(body, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _index(tmp_path: Path, **overrides) -> dict:
    entry = {
        "name": "faketool",
        "binary": "faketool",
        "version_cmd": ["faketool", "--version"],
        "optional": True,
        "used_by": ["tools/dx/tool_index.py"],
    }
    entry.update(overrides)
    return {
        "version": 1,
        "defaults": {"version_regex": r"(\d+(?:\.\d+)+)", "timeout_seconds": 20},
        "tools": [entry],
        "excluded": [],
    }


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------


def test_fake_binary_on_path_reads_present_with_its_version(tmp_path):
    _make_fake(tmp_path, "faketool", "faketool 9.9.9")
    result = tool_index.probe("faketool", _index(tmp_path), path=str(tmp_path))

    assert result["status"] == tool_index.STATUS_PRESENT
    assert result["version"] == "9.9.9"
    assert result["reason"] is None
    assert result["path"] and Path(result["path"]).exists()


def test_version_cmd_exiting_nonzero_reads_unmeasurable_never_present(tmp_path):
    """The binary IS on PATH. That is not the same as the binary WORKING."""
    _make_fake(tmp_path, "faketool", "faketool 9.9.9", exit_code=3)
    result = tool_index.probe("faketool", _index(tmp_path), path=str(tmp_path))

    assert result["status"] == tool_index.STATUS_UNMEASURABLE
    assert result["status"] != tool_index.STATUS_PRESENT
    assert result["status"] != tool_index.STATUS_ABSENT
    assert result["reason"] == tool_index.REASON_EXIT
    # It was FOUND -- reporting it absent would send a reader to install a
    # tool that is already installed and broken.
    assert result["path"] is not None
    assert result["version"] is None


def test_output_the_regex_cannot_read_is_unmeasurable_not_present(tmp_path):
    _make_fake(tmp_path, "faketool", "no version here at all")
    result = tool_index.probe("faketool", _index(tmp_path), path=str(tmp_path))

    assert result["status"] == tool_index.STATUS_UNMEASURABLE
    assert result["reason"] == tool_index.REASON_UNPARSED


def test_binary_not_on_path_reads_absent(tmp_path):
    result = tool_index.probe("faketool", _index(tmp_path), path=str(tmp_path))

    assert result["status"] == tool_index.STATUS_ABSENT
    assert result["path"] is None
    assert result["version"] is None
    assert result["meets_min"] is None


def test_unmeasurable_is_counted_apart_from_absent(tmp_path):
    """A run with one of each must not collapse two verdicts into one bucket."""
    _make_fake(tmp_path, "goodtool", "goodtool 1.2.3")
    _make_fake(tmp_path, "sicktool", "sicktool 1.2.3", exit_code=1)
    index = {
        "version": 1,
        "defaults": {"version_regex": r"(\d+(?:\.\d+)+)"},
        "tools": [
            {"name": n, "binary": n, "version_cmd": [n, "--version"],
             "optional": True, "used_by": ["tools/dx/tool_index.py"]}
            for n in ("goodtool", "sicktool", "ghosttool")
        ],
        "excluded": [],
    }
    report = tool_index.probe_all(index=index, path=str(tmp_path))

    assert report["counts"] == {
        tool_index.STATUS_PRESENT: 1,
        tool_index.STATUS_ABSENT: 1,
        tool_index.STATUS_UNMEASURABLE: 1,
    }
    assert sum(report["counts"].values()) == report["declared"] == 3


# ---------------------------------------------------------------------------
# meets_min -- True | False | None, never False for unknown
# ---------------------------------------------------------------------------


def test_min_version_is_compared_when_it_can_be(tmp_path):
    _make_fake(tmp_path, "faketool", "faketool 1.2.3")
    below = tool_index.probe("faketool", _index(tmp_path, min_version="2.0"),
                             path=str(tmp_path))
    above = tool_index.probe("faketool", _index(tmp_path, min_version="1.0"),
                             path=str(tmp_path))

    assert below["meets_min"] is False
    assert above["meets_min"] is True


def test_meets_min_is_none_and_never_false_when_the_comparison_cannot_be_made():
    assert tool_index.meets_minimum("1.2.3", None) is None      # no minimum declared
    assert tool_index.meets_minimum(None, "1.0") is None        # no version read
    assert tool_index.meets_minimum("unknown", "1.0") is None   # unparseable
    # A real comparison still answers.
    assert tool_index.meets_minimum("2.55.0.windows.5", "2.20") is True


def test_absent_tool_never_reports_a_version_verdict(tmp_path):
    result = tool_index.probe("faketool", _index(tmp_path, min_version="99.0"),
                              path=str(tmp_path))
    assert result["status"] == tool_index.STATUS_ABSENT
    assert result["meets_min"] is None


# ---------------------------------------------------------------------------
# Rates: None over an empty denominator, never 0.0 and never 100.0
# ---------------------------------------------------------------------------


def test_present_pct_is_none_over_an_empty_denominator():
    empty = {"version": 1, "defaults": {}, "tools": [], "excluded": []}
    report = tool_index.probe_all(index=empty)

    assert report["declared"] == 0
    assert report["present_pct"] is None
    assert report["present_pct"] != 0.0
    assert report["present_pct"] != 100.0


def test_a_measured_rate_is_still_reported(tmp_path):
    _make_fake(tmp_path, "goodtool", "goodtool 1.0.0")
    index = {
        "version": 1, "defaults": {},
        "tools": [{"name": n, "binary": n, "version_cmd": [n, "--version"],
                   "optional": True, "used_by": ["tools/dx/tool_index.py"]}
                  for n in ("goodtool", "ghosttool")],
        "excluded": [],
    }
    report = tool_index.probe_all(index=index, path=str(tmp_path))
    assert report["present_pct"] == 50.0


# ---------------------------------------------------------------------------
# which() -- the ONE lookup, and it refuses an undeclared name
# ---------------------------------------------------------------------------


def test_which_resolves_a_declared_binary(tmp_path):
    made = _make_fake(tmp_path, "faketool", "faketool 1.0.0")
    found = tool_index.which("faketool", _index(tmp_path), path=str(tmp_path))

    assert found is not None
    assert Path(found).resolve() == made.resolve()


def test_which_returns_none_when_the_declared_binary_is_absent(tmp_path):
    assert tool_index.which("faketool", _index(tmp_path), path=str(tmp_path)) is None


def test_which_refuses_an_undeclared_name(tmp_path):
    """An index that silently answers for anything can never grow."""
    with pytest.raises(KeyError) as exc:
        tool_index.which("not-declared-anywhere", _index(tmp_path), path=str(tmp_path))
    assert "args/tool_index.yaml" in str(exc.value)


def test_an_excluded_name_raises_carrying_its_declared_reason():
    """`bandit` is deliberately not a PATH tool. The refusal says why."""
    with pytest.raises(KeyError) as exc:
        tool_index.which("bandit")
    message = str(exc.value)
    assert "DECLARED ABSENT" in message
    assert "PYTHON MODULE" in message


# ---------------------------------------------------------------------------
# Per-OS binary stem overrides
# ---------------------------------------------------------------------------


def test_per_os_binary_override_selects_the_platform_stem():
    entry = {"name": "t", "binary": {"default": "t", "windows": "t-win",
                                     "linux": "t-nix", "darwin": "t-mac"}}
    assert tool_index.binary_for(entry, system="Windows") == "t-win"
    assert tool_index.binary_for(entry, system="Linux") == "t-nix"
    assert tool_index.binary_for(entry, system="Darwin") == "t-mac"
    assert tool_index.binary_for(entry, system="Haiku") == "t"   # unknown -> default


def test_a_plain_string_binary_is_used_on_every_platform():
    entry = {"name": "t", "binary": "t"}
    for system in ("Windows", "Linux", "Darwin"):
        assert tool_index.binary_for(entry, system=system) == "t"


def test_the_override_count_is_measured_not_assumed():
    """Zero overrides today is a REPORTED number, so its growth is visible."""
    count = tool_index.os_overrides_declared(tool_index.load_index())
    assert isinstance(count, int)
    assert count == sum(1 for e in tool_index.entries()
                        if isinstance(e.get("binary"), dict))


# ---------------------------------------------------------------------------
# The shipped declaration validates
# ---------------------------------------------------------------------------


def test_shipped_index_validates_and_every_used_by_module_exists():
    problems = tool_index.validate_index(tool_index.load_index(), root=REPO_ROOT)
    assert problems == [], "args/tool_index.yaml is invalid:\n  " + "\n  ".join(problems)


def test_shipped_index_declares_at_least_the_core_binaries():
    names = {e["name"] for e in tool_index.entries()}
    for required in ("git", "gh", "docker", "node", "npx", "trivy",
                     "osv-scanner", "detect-secrets", "pip-audit",
                     "oscal-cli", "tesseract", "java"):
        assert required in names, f"{required} is not declared"


def test_git_is_the_one_required_tool_and_everything_else_is_optional():
    required = [e["name"] for e in tool_index.entries() if e.get("optional") is False]
    assert required == ["git"]


def test_every_entry_declares_a_version_cmd():
    """Without one, `present` would be a claim about a file name."""
    for entry in tool_index.entries():
        cmd = entry.get("version_cmd")
        assert isinstance(cmd, list) and cmd, f"{entry.get('name')} has no version_cmd"


def test_every_declared_absence_carries_a_reason():
    excluded = tool_index.load_index().get("excluded") or []
    assert excluded, "the excluded list is where a deliberate omission becomes visible"
    for item in excluded:
        assert str(item.get("reason") or "").strip(), f"{item.get('name')} has no reason"


def test_validation_catches_a_used_by_module_that_no_longer_exists(tmp_path):
    broken = _index(tmp_path, used_by=["tools/dx/no_such_module_at_all.py"])
    problems = tool_index.validate_index(broken, root=REPO_ROOT)
    assert any("no_such_module_at_all" in p for p in problems)


def test_validation_catches_a_missing_version_cmd(tmp_path):
    broken = _index(tmp_path)
    broken["tools"][0].pop("version_cmd")
    problems = tool_index.validate_index(broken, root=REPO_ROOT)
    assert any("version_cmd" in p for p in problems)


def test_validation_catches_a_name_both_declared_and_excluded(tmp_path):
    broken = _index(tmp_path)
    broken["excluded"] = [{"name": "faketool", "reason": "contradiction"}]
    problems = tool_index.validate_index(broken, root=REPO_ROOT)
    assert any("both declared and excluded" in p for p in problems)


def test_the_yaml_on_disk_parses_and_matches_the_loader():
    raw = yaml.safe_load(
        (REPO_ROOT / "args" / "tool_index.yaml").read_text(encoding="utf-8"))
    assert [t["name"] for t in raw["tools"]] == [e["name"] for e in tool_index.entries()]


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_refresh_json_lists_every_declared_tool_with_a_three_state_status():
    proc = subprocess.run(
        [sys.executable, "-m", "tools.dx.tool_index", "--refresh", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)

    declared = [e["name"] for e in tool_index.entries()]
    assert [r["name"] for r in report["tools"]] == declared
    assert report["declared"] == len(declared)

    allowed = {tool_index.STATUS_PRESENT, tool_index.STATUS_ABSENT,
               tool_index.STATUS_UNMEASURABLE}
    for row in report["tools"]:
        assert row["status"] in allowed
        assert row["meets_min"] in (True, False, None)
    assert sum(report["counts"].values()) == report["declared"]
    assert "os_overrides_declared" in report


def test_validate_exits_zero_on_the_shipped_declaration():
    proc = subprocess.run(
        [sys.executable, "-m", "tools.dx.tool_index", "--validate", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["valid"] is True


def test_an_unreadable_index_exits_two_and_never_reports_a_clean_run(tmp_path):
    """Exit 2 = no report was produced, which is never a clean report."""
    proc = subprocess.run(
        [sys.executable, "-m", "tools.dx.tool_index", "--refresh", "--json",
         "--index", str(tmp_path / "nope.yaml")],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["state"] == tool_index.STATUS_UNMEASURABLE
    assert "tools" not in payload


def test_name_probes_one_declared_tool():
    proc = subprocess.run(
        [sys.executable, "-m", "tools.dx.tool_index", "--name", "git", "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["declared"] == 1
    assert report["tools"][0]["name"] == "git"


# ---------------------------------------------------------------------------
# The health_check seam
# ---------------------------------------------------------------------------


def test_health_check_registers_external_tools():
    from tools.testing import health_check

    assert "external_tools" in health_check._HEALTH_CHECKS
    assert health_check._HEALTH_CHECKS["external_tools"] is health_check.check_external_tools


def test_health_check_warns_on_absent_optional_tools_and_never_fails(monkeypatch):
    from tools.testing import health_check

    monkeypatch.setattr(tool_index, "probe_all", lambda *a, **k: {
        "declared": 2, "present_pct": 50.0,
        "counts": {tool_index.STATUS_PRESENT: 1, tool_index.STATUS_ABSENT: 1,
                   tool_index.STATUS_UNMEASURABLE: 0},
        "required_absent": [], "below_min_version": [],
        "tools": [
            {"name": "here", "status": tool_index.STATUS_PRESENT, "optional": True,
             "reason": None},
            {"name": "gone", "status": tool_index.STATUS_ABSENT, "optional": True,
             "reason": None},
        ],
    })
    result = health_check.check_external_tools()

    assert result.success is True
    assert result.error is None
    assert "1 optional tool(s) absent" in (result.warning or "")


def test_health_check_errors_only_when_a_required_tool_is_absent(monkeypatch):
    from tools.testing import health_check

    monkeypatch.setattr(tool_index, "probe_all", lambda *a, **k: {
        "declared": 1, "present_pct": 0.0,
        "counts": {tool_index.STATUS_PRESENT: 0, tool_index.STATUS_ABSENT: 1,
                   tool_index.STATUS_UNMEASURABLE: 0},
        "required_absent": ["git"], "below_min_version": [],
        "tools": [{"name": "git", "status": tool_index.STATUS_ABSENT,
                   "optional": False, "reason": None}],
    })
    result = health_check.check_external_tools()

    assert result.success is False
    assert "git" in (result.error or "")


def test_health_check_treats_unmeasurable_as_a_warning_never_an_error(monkeypatch):
    from tools.testing import health_check

    monkeypatch.setattr(tool_index, "probe_all", lambda *a, **k: {
        "declared": 1, "present_pct": 0.0,
        "counts": {tool_index.STATUS_PRESENT: 0, tool_index.STATUS_ABSENT: 0,
                   tool_index.STATUS_UNMEASURABLE: 1},
        "required_absent": [], "below_min_version": [],
        "tools": [{"name": "helm", "status": tool_index.STATUS_UNMEASURABLE,
                   "optional": True, "reason": tool_index.REASON_EXIT}],
    })
    result = health_check.check_external_tools()

    assert result.success is True
    assert result.error is None
    assert "unmeasurable" in (result.warning or "")
    assert result.details["unmeasurable"] == 1
    assert result.details["absent"] == 0


# ---------------------------------------------------------------------------
# used_by is principal consumers -- the full set is re-derivable
# ---------------------------------------------------------------------------


def test_call_sites_re_derives_invocations_from_the_tree():
    sites = tool_index.call_sites("osv-scanner", tool_index.load_index(),
                                  root=REPO_ROOT)
    assert "tools/security/osv_scanner.py" in sites


def test_declared_used_by_modules_are_a_subset_of_what_the_tree_still_does():
    """A declared consumer that no longer invokes the binary is stale data.

    Checked on a binary with a small, stable call-site set rather than on
    ``git`` (81 sites), so the assertion stays about correctness and not
    about how long an AST walk takes.
    """
    names = ("osv-scanner", "detect-secrets", "trivy")
    derived_all = tool_index.call_sites_many(names, root=REPO_ROOT)
    for name in names:
        declared = set(tool_index.entry_for(name).get("used_by") or [])
        derived = set(derived_all[name])
        # health_check reaches osv-scanner through the OsvScanner class, not
        # through argv, so it is a real consumer an AST argv walk cannot see.
        assert declared - derived <= {"tools/testing/health_check.py"}, name
