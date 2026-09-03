# CUI // SP-CTI
"""docs/irad/ carries no real IR&D cost line, and the leak gate refuses a new one.

THE INCIDENT. Two IR&D proposals in this PUBLIC repository carried our own
cost data for two months: `docs/irad/aiforge_irad.md` (Labor $380K, 2.5 FTEs;
non-labor $100K; total $480K) and `docs/irad/acoi_irad_proposal.md` (Labor
$290K, 2 FTEs; non-labor $50K; total $340K), plus the packaged mirror under
`icdev/data/docs/irad/`. A different sensitivity class from bid strategy
(PR #2007) -- not a customer's price, ours -- in the same tree, found by the
same plan review, and not covered by the GovCon path rules because those are
scoped to tools/govcon and hardprompts.

Two guards, because they fail differently: the shipped documents carry no
figure (content), and the domain leak gate refuses one landing again (rule).
The rule is PATH-SCOPED and keyed on the three cost-line LABELS, so the
market-sizing table in the same documents ($50-200M/yr cost avoidance --
public-domain estimation) is not matched, and a fake ROM in a test fixture
elsewhere is not either.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import domain_leak_gate as gate  # noqa: E402

RULE_NAME = "Cost line in an IR&D proposal document"
DOC_DIRS = ("docs/irad", "icdev/data/docs/irad")

# The literal shapes from the incident. They must trip the rule.
PLANTED = [
    "- **Total Cost:** $480K",
    "  - **Labor:** $380K (2.5 FTEs — ML engineer, full-stack, DevSecOps)",
    "  - **Non-Labor:** $100K (GovCloud Bedrock credits, air-gap compute, tooling)",
    "  - **Labor:** $290K (2 FTEs)",
]
# Same documents, must NOT trip: market sizing is not our cost data.
NOT_A_COST_LINE = "| DoD ACAT II/III PMOs | DISA, Army PEO EIS | $50–200M/yr modernization cost avoidance | Defense BU |"


def _rule() -> dict:
    cfg = gate.load_gate()
    rules = [r for r in cfg.get("scoped_patterns", []) if r.get("name") == RULE_NAME]
    assert len(rules) == 1, f"rule {RULE_NAME!r} missing from args/domain_leak_gate.yaml"
    return rules[0]


def _repo(tmp_path: Path) -> Path:
    """A throwaway repo whose gate config carries ONLY the rule under test."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "args").mkdir()
    (tmp_path / "args" / "domain_leak_gate.yaml").write_text(yaml.safe_dump({
        "domain_leak_gate": {
            "paths": {"mode": "report", "deny": []},
            "sql_markers": [],
            "patterns": {"mode": "enforce"},
            "scoped_patterns": [_rule()],
            "allow": [],
        }
    }), encoding="utf-8")
    return tmp_path


def _write(repo: Path, rel: str, text: str) -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n", encoding="utf-8")
    return rel


# --------------------------------------------------------------------------- #
# the rule discriminates
# --------------------------------------------------------------------------- #
def test_rule_matches_every_incident_line_and_not_the_market_table():
    pat = re.compile(_rule()["pattern"])
    for line in PLANTED:
        assert pat.search(line), f"rule no longer matches the incident text: {line!r}"
    assert not pat.search(NOT_A_COST_LINE), "the market-sizing table is not our cost data"


def test_rule_is_scoped_to_both_irad_trees():
    paths = _rule()["paths"]
    assert "docs/irad/*" in paths and "icdev/data/docs/irad/*" in paths, paths
    assert _rule()["severity"] == "critical"


# --------------------------------------------------------------------------- #
# the gate, end to end: refused in docs/irad, invisible one directory up
# --------------------------------------------------------------------------- #
def test_gate_refuses_a_cost_line_under_docs_irad(tmp_path):
    repo = _repo(tmp_path)
    rel = _write(repo, "docs/irad/new_proposal.md", PLANTED[1])
    rep = gate.build_report(repo, [rel])
    assert rep["findings"], "a planted labor line under docs/irad/ was not refused"
    assert any(f.get("rule") == RULE_NAME or RULE_NAME in str(f) for f in rep["findings"]), rep["findings"]


def test_gate_refuses_it_in_the_packaged_mirror_too(tmp_path):
    repo = _repo(tmp_path)
    rel = _write(repo, "icdev/data/docs/irad/new_proposal.md", PLANTED[0])
    assert gate.build_report(repo, [rel])["findings"]


def test_the_same_line_outside_docs_irad_is_not_this_rules_business(tmp_path):
    """Scoped by PATH on purpose: a fake ROM in a test fixture is correct."""
    repo = _repo(tmp_path)
    rel = _write(repo, "docs/reference/example.md", PLANTED[1])
    assert gate.build_report(repo, [rel])["findings"] == []


def test_market_sizing_table_under_docs_irad_passes(tmp_path):
    repo = _repo(tmp_path)
    rel = _write(repo, "docs/irad/proposal.md", NOT_A_COST_LINE)
    assert gate.build_report(repo, [rel])["findings"] == []


# --------------------------------------------------------------------------- #
# the shipped documents, both trees, carry no figure
# --------------------------------------------------------------------------- #
def test_shipped_irad_documents_carry_no_cost_figure():
    pat = re.compile(_rule()["pattern"])
    offenders = {}
    for d in DOC_DIRS:
        for md in sorted((ROOT / d).glob("*.md")):
            hits = [m.group(0) for m in pat.finditer(md.read_text(encoding="utf-8"))]
            if hits:
                offenders[str(md.relative_to(ROOT))] = hits
    assert not offenders, (
        f"real cost lines in a public IR&D document: {offenders}. "
        "Figures belong in the private overlay (ICDEV_GOVCON_PROMPTS_PATH)."
    )


def test_shipped_irad_documents_name_the_overlay_not_a_number():
    for d in DOC_DIRS:
        for name in ("aiforge_irad.md", "acoi_irad_proposal.md"):
            text = (ROOT / d / name).read_text(encoding="utf-8")
            assert "ICDEV_GOVCON_PROMPTS_PATH" in text, f"{d}/{name} does not say where the figures live"
            assert "{{ irad." in text, f"{d}/{name} lost its placeholders"
