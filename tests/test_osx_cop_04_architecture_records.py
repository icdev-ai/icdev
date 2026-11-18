# CUI // SP-CTI
"""The Application CoP's architecture records on the golden path (osx-cop-04).

ICDEV[IT] already keeps ADR records (icx-coe-02: docs/reference/adrs, tools/idp/adrs.py) and golden paths
(icx-coe-04: `icdev scaffold service`). This card adds the C4 views and the arc42 description a service
carries beside its ADR record, emitted by the same scaffold, and holds the boundary that matters:

  * `icdev scaffold service` renders docs/architecture/<key>/c4.md and arc42.md from the service's own pieces;
  * the arc42 description carries arc42's twelve sections in order, and section 9 LINKS the service's ADR
    record -- the ADR record (written by tools/idp/adrs.py, proposed) is the ONE place a decision is kept:
    ADR records are not duplicated, not in the documents, not as a second record, not as a second template;
  * the drift check judges it (piece `architecture_records`): a document that copies an ADR, or a second ADR
    record, is drift, never pass;
  * the templates credit their sources (C4: CC BY 4.0; arc42 9.1: CC BY-SA 4.0) and copy no arc42 text.

Everything renders into a temporary directory; nothing touches a database.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data/templates/services/golden"


@pytest.fixture()
def scaffolded(tmp_path):
    from tools.cli import scaffold
    from tools.idp import golden_paths as gp

    for rel in gp.SHARED.values():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((ROOT / rel).read_bytes())
    res = scaffold.scaffold_service("cop04_probe", "Cop04 Probe", tmp_path, path_id="service", register=False)
    assert res["success"], res
    return tmp_path, res


def test_scaffold_service_emits_c4_and_arc42_beside_the_adr_record(scaffolded):
    root, res = scaffolded
    files = set(res["rendered_files"]) | set(res["artifacts"]["files"])
    assert {"docs/architecture/cop04_probe/c4.md", "docs/architecture/cop04_probe/arc42.md",
            "docs/reference/adrs/D-COP04-PROBE-1.md"} <= files
    c4 = (root / "docs/architecture/cop04_probe/c4.md").read_text(encoding="utf-8")
    for view in ("Level 1: System context", "Level 2: Containers", "Level 3: Components",
                 "Supplementary: Deployment", "Supplementary: Dynamic"):
        assert f"## {view}" in c4, view
    for evidence in ("tools/cop04_probe/blueprint.py", "tools/cop04_probe/service.py::decide",
                     "args/workflow_hitl_templates/cop04-probe-decide.yaml"):
        assert evidence in c4 and (root / evidence.split("::")[0]).exists(), evidence
    arc = (root / "docs/architecture/cop04_probe/arc42.md").read_text(encoding="utf-8")
    heads = re.findall(r"^## (\d+)\. (.+?)\s*$", arc, flags=re.M)
    from tools.idp import golden_paths as gp
    assert [(int(n), h) for n, h in heads] == list(enumerate(gp.ARC42_SECTIONS, 1))
    assert gp.architecture_record_problems(root, "cop04_probe", "D-COP04-PROBE-1") == []


def test_adr_records_are_not_duplicated(scaffolded):
    from tools.idp import adrs
    from tools.idp import golden_paths as gp

    root, _ = scaffolded
    recs = sorted(p.name for p in (root / "docs/reference/adrs").glob("D-COP04-PROBE-*.md"))
    assert recs == ["D-COP04-PROBE-1.md"], "one ADR record, written by tools/idp/adrs.py"
    rec = adrs.read_record(root / "docs/reference/adrs/D-COP04-PROBE-1.md")
    assert rec["status"] == "proposed"
    arc_path = root / "docs/architecture/cop04_probe/arc42.md"
    arc = arc_path.read_text(encoding="utf-8")
    sec9 = arc.split("## 9. ")[1].split("## 10. ")[0]
    assert "../../reference/adrs/D-COP04-PROBE-1.md" in sec9
    for doc in ("c4", "arc42"):
        t = (root / f"docs/architecture/cop04_probe/{doc}.md").read_text(encoding="utf-8")
        assert rec["decision"].strip() not in t and rec["context"].strip() not in t, f"{doc}.md copies the ADR"
    # PLANTED: the decision copied into the arc42 description is drift, never a pass
    arc_path.write_text(arc.replace("## 10. ", rec["decision"] + "\n\n## 10. "), encoding="utf-8")
    why = gp.architecture_record_problems(root, "cop04_probe", "D-COP04-PROBE-1")
    assert any("copies the ADR record's decision" in w for w in why), why
    arc_path.write_text(arc, encoding="utf-8")
    # PLANTED: ADR front matter in a document, and a second record beside the one adrs.py wrote
    (root / "docs/architecture/cop04_probe/c4.md").write_text(
        "---\nid: D-COP04-PROBE-2\nstatus: accepted\n---\n" + (root / "docs/architecture/cop04_probe/c4.md").read_text(
            encoding="utf-8"), encoding="utf-8")
    (root / "docs/reference/adrs/D-COP04-PROBE-2.md").write_text("---\nid: D-COP04-PROBE-2\n---\ncopied\n", encoding="utf-8")
    why = gp.architecture_record_problems(root, "cop04_probe", "D-COP04-PROBE-1")
    assert any("c4.md carries ADR front matter" in w for w in why), why
    assert any("exactly one ADR record" in w for w in why), why
    # the golden template tree holds no ADR template of its own: the record comes from tools/idp/adrs.py only
    assert not [p.name for p in GOLDEN.iterdir() if re.search(r"(^|[^a-z])adrs?([^a-z]|$)", p.name.lower())]
    # a section 9 that does not link the record, or a section missing, is said
    arc_path.write_text(arc.replace("../../reference/adrs/D-COP04-PROBE-1.md", "elsewhere.md"), encoding="utf-8")
    assert any("does not link the ADR record" in w for w in gp.architecture_record_problems(root, "cop04_probe",
                                                                                              "D-COP04-PROBE-1"))
    arc_path.write_text(arc.replace("## 12. Glossary", "## 12. Notes"), encoding="utf-8")
    assert any("twelve sections in order" in w for w in gp.architecture_record_problems(root, "cop04_probe",
                                                                                          "D-COP04-PROBE-1"))


def test_the_drift_check_judges_the_architecture_records(tmp_path, monkeypatch):
    """The drift check's piece list carries `architecture_records`, judged on the scaffold it rendered."""
    from tools.idp import golden_paths as gp

    src = Path(gp.__file__).read_text(encoding="utf-8")
    pieces = src.split("def _pieces(")[1].split("\ndef ")[0]
    assert 'architecture_record_problems(overlay, key, adr_id)' in pieces
    assert '_piece("architecture_records"' in pieces


def test_the_templates_credit_their_sources_and_copy_no_arc42_text():
    c4 = (GOLDEN / "c4.md.j2").read_text(encoding="utf-8")
    arc = (GOLDEN / "arc42.md.j2").read_text(encoding="utf-8")
    assert "https://c4model.com" in c4 and "CC BY 4.0" in c4
    assert "https://arc42.org" in arc and "9.1" in arc and "CC BY-SA 4.0" in arc and "Starke" in arc
    assert "no arc42 text is copied" in arc
    manifest = (GOLDEN / "manifest.yaml").read_text(encoding="utf-8")
    assert 'dest: "docs/architecture/{{ key }}/c4.md"' in manifest and 'dest: "docs/architecture/{{ key }}/arc42.md"' in manifest
