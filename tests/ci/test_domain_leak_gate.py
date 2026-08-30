# CUI // SP-CTI
"""xit-leak-01 -- the public-repo domain leak gate.

The negative controls below PLANT fake broker-credential shapes. They are the
proof the rules fire; this file is an `allow` entry in args/domain_leak_gate.yaml
for exactly that reason. None of them is a real credential.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tools.ci import domain_leak_gate as gate
from tools.security.secret_detector import BUILTIN_PATTERNS

REPO_ROOT = Path(__file__).resolve().parents[2]

# Deliberately fake shapes (the letters spell it out).
FAKE_ALPACA_PAPER = "PK" + "FAKEFAKEFAKEFAKE01"          # PK + 18
FAKE_ALPACA_LIVE = "AK" + "FAKEFAKEFAKEFAKE02"           # AK + 18, not AKIA
AWS_EXAMPLE = "AKIAIOSFODNN7EXAMPLE"                     # the AWS doc example
FAKE_COINBASE = "organizations/00000000-0000-4000-8000-000000000000/apiKeys/11111111-1111-4111-8111-111111111111"


def _repo(tmp_path: Path, paths_mode: str = "report") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "args").mkdir(parents=True)
    (tmp_path / "args" / "domain_leak_gate.yaml").write_text(yaml.safe_dump({
        "domain_leak_gate": {
            "paths": {"mode": paths_mode, "deny": ["tools/trading/**", "args/trading*.yaml"]},
            "sql_markers": ["COPY ad_", "INSERT INTO ad_"],
            "patterns": {"mode": "enforce"},
            "allow": [{"path": "tests/fixtures/*", "reason": "planted negative controls"}],
        }
    }), encoding="utf-8")
    (tmp_path / "tools" / "pkg").mkdir(parents=True)
    (tmp_path / "tools" / "pkg" / "clean.py").write_text("X = 1\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# the pattern table
# --------------------------------------------------------------------------- #
def test_broker_rules_live_in_the_one_secret_pattern_table():
    names = {p["name"] for p in gate.broker_patterns()}
    assert {"Alpaca Paper Key ID", "Alpaca Live Key ID", "Alpaca API Header Value",
            "Kraken Private Key", "Coinbase CDP Key Name"} <= names
    assert all(p.get("category") == gate.BROKER_CATEGORY for p in gate.broker_patterns())
    # and they are part of what the general secret scanner compiles
    assert all(p in BUILTIN_PATTERNS for p in gate.broker_patterns())


def test_alpaca_live_rule_excludes_aws_access_keys(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tools" / "pkg" / "aws.py").write_text(f'KEY = "{AWS_EXAMPLE}"\n', encoding="utf-8")
    rep = gate.build_report(repo, ["tools/pkg/aws.py"])
    assert rep["findings"] == []  # AWS keys are the AWS rule's business


# --------------------------------------------------------------------------- #
# the negative controls: planted fakes MUST be refused
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("planted,rule", [
    (f'ALPACA_KEY = "{FAKE_ALPACA_PAPER}"', "Alpaca Paper Key ID"),
    (f'ALPACA_KEY = "{FAKE_ALPACA_LIVE}"', "Alpaca Live Key ID"),
    ('headers = {"APCA-API-SECRET-KEY": "abcdefghijklmnop0123456789"}', "Alpaca API Header Value"),
    (f'name = "{FAKE_COINBASE}"', "Coinbase CDP Key Name"),
    ('tradier_token = "abcdefghijklmnopqrstuvwxyz0123456789"', "Tradier Access Token"),
])
def test_planted_broker_credential_is_refused(tmp_path, planted, rule):
    repo = _repo(tmp_path)
    (repo / "tools" / "pkg" / "leak.py").write_text(planted + "\n", encoding="utf-8")
    rep = gate.build_report(repo, ["tools/pkg/leak.py", "tools/pkg/clean.py"])
    assert rep["ok"] is False and rep["violations"]["patterns"] is True
    assert [f["rule"] for f in rep["findings"]] == [rule]
    assert gate.main(["--check", "--changed", "tools/pkg/leak.py", "--root", str(repo)]) == 1


def test_planted_credential_in_an_allowed_path_is_reported_not_counted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "fake.py").write_text(f'K = "{FAKE_ALPACA_PAPER}"\n', encoding="utf-8")
    rep = gate.build_report(repo, ["tests/fixtures/fake.py"])
    assert rep["ok"] is True and rep["findings"] == []
    assert rep["allowed_hits"] and rep["allowed_hits"][0]["allowed_reason"] == "planted negative controls"


def test_ad_table_dump_in_a_data_file_is_refused_but_source_is_not(tmp_path):
    repo = _repo(tmp_path)
    (repo / "dump.sql").write_text("COPY ad_positions FROM stdin;\n1\t2\n", encoding="utf-8")
    (repo / "tools" / "pkg" / "writer.py").write_text(
        'conn.execute("INSERT INTO ad_positions VALUES (1)")\n', encoding="utf-8")
    rep = gate.build_report(repo, ["dump.sql", "tools/pkg/writer.py"])
    assert [f["kind"] for f in rep["findings"]] == ["ad_table_dump"]
    assert rep["ok"] is False


# --------------------------------------------------------------------------- #
# the path half arms later
# --------------------------------------------------------------------------- #
def test_denied_path_is_reported_in_report_mode_and_refused_in_enforce_mode(tmp_path):
    repo = _repo(tmp_path, paths_mode="report")
    (repo / "tools" / "trading").mkdir()
    (repo / "tools" / "trading" / "x.py").write_text("X = 1\n", encoding="utf-8")
    rep = gate.build_report(repo, ["tools/trading/x.py"])
    assert rep["denied_paths_present"] == ["tools/trading/x.py"]
    assert rep["ok"] is True and rep["violations"]["paths"] is False

    repo2 = _repo(tmp_path / "two", paths_mode="enforce")
    (repo2 / "tools" / "trading").mkdir()
    (repo2 / "tools" / "trading" / "x.py").write_text("X = 1\n", encoding="utf-8")
    rep2 = gate.build_report(repo2, ["tools/trading/x.py"])
    assert rep2["ok"] is False and rep2["violations"]["paths"] is True


def test_guard_env_stands_the_refusal_down_but_still_reports(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "tools" / "pkg" / "leak.py").write_text(f'K = "{FAKE_ALPACA_PAPER}"\n', encoding="utf-8")
    monkeypatch.setenv(gate.GUARD_ENV, "0")
    rep = gate.build_report(repo, ["tools/pkg/leak.py"])
    assert rep["ok"] is True and rep["enforced"] is False and len(rep["findings"]) == 1


# --------------------------------------------------------------------------- #
# the checked-in configuration and the real tree
# --------------------------------------------------------------------------- #
def test_checked_in_gate_has_written_reasons_and_this_file_is_allowed():
    cfg = gate.load_gate(REPO_ROOT / "args" / "domain_leak_gate.yaml")
    for entry in cfg.get("allow", []):
        assert len(str(entry.get("reason", ""))) >= 12, entry
    assert gate._allowed("tests/ci/test_domain_leak_gate.py", cfg) is not None
    assert cfg["patterns"]["mode"] == "enforce"
    assert cfg["paths"]["mode"] in ("report", "enforce")


def test_the_tracked_tree_carries_no_broker_credential():
    """The survey that justified arming the pattern half, re-run as a test."""
    rep = gate.build_report(REPO_ROOT)
    assert rep["findings"] == [], [(f["file"], f["rule"]) for f in rep["findings"]][:10]
    assert rep["rules"] >= 8


def test_tool_is_mirrored_byte_for_byte():
    a = (REPO_ROOT / "tools" / "ci" / "domain_leak_gate.py").read_bytes()
    b = (REPO_ROOT / "icdev" / "tools" / "ci" / "domain_leak_gate.py").read_bytes()
    assert a == b


def test_precommit_and_ci_consume_the_gate():
    hook = (REPO_ROOT / "tools" / "testing" / "pre_commit_check.py").read_text(encoding="utf-8")
    assert "domain_leak_gate.py" in hook and "_run_domain_leak_gate()" in hook
    ci = (REPO_ROOT / ".github" / "workflows" / "icdev-ci.yml").read_text(encoding="utf-8")
    assert "python tools/ci/domain_leak_gate.py --check" in ci
    assert textwrap.dedent(ci).count("domain_leak_gate.py --check || true") == 0


# --------------------------------------------------------------------------- #
# personal_financial -- the ICDEV[RT] category
# --------------------------------------------------------------------------- #
# Deliberately fake shapes, as above.
FAKE_SIMPLEFIN = "https://user123:s3cr3ttoken@beta-bridge.simplefin.org/simplefin"
FAKE_SSN = "123-45-6789"          # the canonical documentation SSN


def _repo_rt(tmp_path: Path) -> Path:
    """A repo whose gate config enforces the RT category and the data rules."""
    repo = _repo(tmp_path)
    cfg = yaml.safe_load((repo / "args" / "domain_leak_gate.yaml").read_text(encoding="utf-8"))
    cfg["domain_leak_gate"]["patterns"]["categories"] = ["broker_credential", "personal_financial"]
    cfg["domain_leak_gate"]["data_patterns"] = [
        {"name": "SSN shape in a data file", "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
         "severity": "critical"},
    ]
    (repo / "args" / "domain_leak_gate.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return repo


def test_a_config_without_categories_keeps_the_broker_only_set(tmp_path):
    """An older args/domain_leak_gate.yaml must behave exactly as before."""
    repo = _repo(tmp_path)
    cfg = yaml.safe_load((repo / "args" / "domain_leak_gate.yaml").read_text(encoding="utf-8"))
    loaded = gate.gate_patterns(cfg["domain_leak_gate"])
    assert {p["category"] for p in loaded} == {"broker_credential"}


def test_declaring_the_rt_category_loads_it(tmp_path):
    repo = _repo_rt(tmp_path)
    cfg = yaml.safe_load((repo / "args" / "domain_leak_gate.yaml").read_text(encoding="utf-8"))
    loaded = gate.gate_patterns(cfg["domain_leak_gate"])
    assert {p["category"] for p in loaded} == {"broker_credential", "personal_financial"}
    assert {"SimpleFIN Access URL", "Labelled Account Number", "Bank Routing Number",
            "Boldin API Credential"} <= {p["name"] for p in loaded}


def test_an_unknown_category_is_a_config_error_not_an_empty_ruleset(tmp_path):
    """Silently matching nothing is how a gate reports clean over rules it
    never loaded -- the exact failure shape this file exists to refuse."""
    repo = _repo(tmp_path)
    cfg = yaml.safe_load((repo / "args" / "domain_leak_gate.yaml").read_text(encoding="utf-8"))
    cfg["domain_leak_gate"]["patterns"]["categories"] = ["broker_credential", "typo_category"]
    with pytest.raises(SystemExit) as exc:
        gate.gate_patterns(cfg["domain_leak_gate"])
    assert "typo_category" in str(exc.value)


@pytest.mark.parametrize("planted,rule", [
    (f"RET_SIMPLEFIN={FAKE_SIMPLEFIN}", "SimpleFIN Access URL"),
    ("account_number: 4432119087", "Labelled Account Number"),
    ("Routing Number = 021000021", "Bank Routing Number"),
    ("BOLDIN_API_TOKEN: abcd1234efgh5678", "Boldin API Credential"),
])
def test_planted_personal_financial_credential_is_refused(tmp_path, planted, rule):
    repo = _repo_rt(tmp_path)
    (repo / "leak.py").write_text(planted + "\n", encoding="utf-8")
    rep = gate.build_report(repo, ["leak.py"])
    assert rep["ok"] is False
    assert rule in {f["rule"] for f in rep["findings"]}


def test_an_ssn_shape_is_refused_in_a_DATA_file_and_allowed_in_SOURCE(tmp_path):
    """The whole reason the SSN rule is data-scoped.

    Measured over ICDEV[IT] 2026-08-30: the bare shape hits 45 times -- 40 in
    .py, 5 in .md -- and every one is a fixture or doc example of the redaction
    subsystem itself. Arming it against source would refuse the tests that
    prove redaction works. In a data file it is 0, and a data file is what a
    real leak looks like.
    """
    repo = _repo_rt(tmp_path)
    (repo / "export.csv").write_text(f"name,ssn\nAlice,{FAKE_SSN}\n", encoding="utf-8")
    (repo / "tools" / "pkg" / "redact_test.py").write_text(
        f'assert redact("SSN: {FAKE_SSN}") == "SSN: [US_SSN]"\n', encoding="utf-8")

    rep = gate.build_report(repo, ["export.csv", "tools/pkg/redact_test.py"])
    assert rep["ok"] is False
    assert {f["file"] for f in rep["findings"]} == {"export.csv"}
    assert [f["kind"] for f in rep["findings"]] == ["personal_data_dump"]


def test_the_tracked_tree_carries_no_personal_financial_credential():
    """The live assertion, not a fixture one: arming this refused nothing.

    Surveyed over BOTH parents before arming -- ICDEV[IT] 20,788 tracked files
    and ICDEV[FT] 901 -- zero hits for all four rules. FT counts because the
    generic scanner compiles the whole table regardless of category.
    """
    cfg = gate.load_gate()
    declared = (cfg.get("patterns", {}) or {}).get("categories", [])
    assert "personal_financial" in declared, (
        "the checked-in gate no longer enforces the ICDEV[RT] category")

    rep = gate.build_report(REPO_ROOT)
    assert rep["findings"] == [], [(f["file"], f["rule"]) for f in rep["findings"]][:10]
    # 8 broker + 4 personal_financial, all loaded from the one table.
    assert rep["rules"] >= 12
