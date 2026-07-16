# CUI // SP-CTI
"""Shared RulebookPack + `icdev scaffold docmod-pack`.

crypto_protocols and policy_refs are both pure rulebook packs, but each hardcodes
its own RULEBOOK_PATH — so a third rules-driven domain meant copy-pasting ~115
lines of identical Python. That was the barrier to another team covering their
own domain.

The acceptance test here is the end-to-end one: generate a pack, write ONE rule,
and it finds things — with no Python authored by the domain owner.

No network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402
from tools.doc_modernization.packs.rulebook_pack import RulebookPack, load_rulebook  # noqa: E402

_RULES = """
rules:
  - id: mil-std-882d
    pattern: '(?i)\\bMIL-STD-882D\\b'
    verdict: deprecated
    severity: high
    rationale: Superseded by MIL-STD-882E (2012).
    replacement: MIL-STD-882E
  - id: bad-regex
    pattern: '([unclosed'
"""


def _ref():
    return ChunkRef(doc_id="d", version_id="v", chunk_link_id="l", page=None, section=None)


@pytest.fixture
def rulebook(tmp_path):
    p = tmp_path / "rulebook_demo.yaml"
    p.write_text(_RULES, encoding="utf-8")
    return p


def _pack(rulebook_path, **over):
    cfg = {
        "pack_id": "demo",
        "label": "Demo",
        "rulebook_path": str(rulebook_path),
        "entity_types": ["standard"],
    }
    cfg.update(over)
    return RulebookPack(config=cfg)


class TestRulebookLoading:
    def test_a_bad_regex_is_skipped_not_fatal(self, rulebook):
        """One malformed rule must not take down every other domain's sweep."""
        rules = load_rulebook(rulebook)
        assert [r["id"] for r in rules] == ["mil-std-882d"]

    def test_missing_rulebook_yields_no_rules(self, tmp_path):
        assert load_rulebook(tmp_path / "nope.yaml") == []

    def test_cache_is_keyed_by_path(self, tmp_path):
        """The per-domain packs cache on one module-level path; this class is
        shared by every rulebook domain, so two rulebooks must not collide."""
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("rules:\n  - id: ra\n    pattern: 'AAA'\n", encoding="utf-8")
        b.write_text("rules:\n  - id: rb\n    pattern: 'BBB'\n", encoding="utf-8")
        assert [r["id"] for r in load_rulebook(a)] == ["ra"]
        assert [r["id"] for r in load_rulebook(b)] == ["rb"]
        assert [r["id"] for r in load_rulebook(a)] == ["ra"]   # not clobbered by b

    def test_edited_rulebook_is_hot_reloaded(self, tmp_path):
        import os
        import time

        p = tmp_path / "r.yaml"
        p.write_text("rules:\n  - id: one\n    pattern: 'X'\n", encoding="utf-8")
        assert len(load_rulebook(p)) == 1
        time.sleep(0.01)
        p.write_text("rules:\n  - id: one\n    pattern: 'X'\n  - id: two\n    pattern: 'Y'\n", encoding="utf-8")
        os.utime(p, None)
        assert len(load_rulebook(p)) == 2


class TestRulebookPackBehaviour:
    def test_extract_uses_rule_patterns_and_tags_the_rule(self, rulebook):
        p = _pack(rulebook)
        ents = p.extract("System safety per MIL-STD-882D applies.", _ref())
        assert [e.label for e in ents] == ["MIL-STD-882D"]
        assert ents[0].entity_type == "standard"          # from pack entity_types
        assert ents[0].attributes["rule_id"] == "mil-std-882d"

    def test_verdict_comes_from_the_rule_and_cites_it(self, rulebook):
        p = _pack(rulebook)
        e = p.extract("MIL-STD-882D", _ref())[0]
        v = p.evaluate(e, None)
        assert v.currency_verdict == "deprecated"
        assert v.severity == "high"
        assert v.is_finding is True
        assert v.evidence[0]["source"] == "rule:mil-std-882d"

    def test_defaults_fill_in_for_rules_that_omit_them(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("rules:\n  - id: bare\n    pattern: 'ZZZ'\n", encoding="utf-8")
        pack = _pack(p, default_verdict="retired", default_severity="critical",
                     default_finding_type="stale_reference")
        e = pack.extract("ZZZ", _ref())[0]
        v = pack.evaluate(e, None)
        assert v.currency_verdict == "retired"
        assert v.severity == "critical"
        assert v.finding_type == "stale_reference"

    def test_replacement_must_resolve_to_a_rule(self, rulebook):
        """A Replacement is offered as a redline into a live document, so it must
        cite a real rule — never an invention."""
        p = _pack(rulebook)
        e = p.extract("MIL-STD-882D", _ref())[0]
        r = p.recommend(e, p.evaluate(e, None), None)
        assert r.label == "MIL-STD-882E"
        assert r.source == "rulebook" and r.source_ref == "rule:mil-std-882d"

    def test_no_replacement_declared_means_none_offered(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("rules:\n  - id: x\n    pattern: 'QQQ'\n", encoding="utf-8")
        pack = _pack(p)
        e = pack.extract("QQQ", _ref())[0]
        assert pack.recommend(e, pack.evaluate(e, None), None) is None

    def test_rule_removed_between_extract_and_evaluate_is_unknown(self, rulebook):
        p = _pack(rulebook)
        e = p.extract("MIL-STD-882D", _ref())[0]
        e.attributes["rule_id"] = "gone"
        v = p.evaluate(e, None)
        assert v.currency_verdict == "unknown" and v.is_finding is False

    def test_evidence_snapshot_moves_when_rules_change(self, tmp_path):
        """Base default hashes static config only — a rulebook edit would never
        re-scan, and the rulebook IS this pack's evidence."""
        p = tmp_path / "r.yaml"
        p.write_text("rules:\n  - id: a\n    pattern: 'A'\n", encoding="utf-8")
        first = _pack(p).evidence_snapshot(None)
        p.write_text("rules:\n  - id: a\n    pattern: 'A'\n  - id: b\n    pattern: 'B'\n", encoding="utf-8")
        assert _pack(p).evidence_snapshot(None) != first

    def test_path_defaults_to_convention(self):
        pack = RulebookPack(config={"pack_id": "widgets", "label": "W"})
        assert pack._rulebook_path().as_posix().endswith("args/docmod/rulebook_widgets.yaml")


class TestGeneratorEndToEnd:
    def test_rulebook_flavor_generates_yaml_only_no_python(self, tmp_path):
        from tools.cli.scaffold import main

        rc = main(["docmod-pack", "safety_x", "--display-name", "Safety X",
                   "--entity-type", "standard", "--out", str(tmp_path)])
        assert rc == 0
        files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
        assert files == ["args/docmod/packs/safety_x.yaml", "args/docmod/rulebook_safety_x.yaml"]
        assert not any(f.endswith(".py") for f in files), "rulebook flavor must need no Python"

    def test_generated_pack_is_disabled_until_a_rule_exists(self, tmp_path):
        """An empty rulebook would add a pack to every scan that can never find
        anything."""
        from tools.cli.scaffold import main

        main(["docmod-pack", "d1", "--display-name", "D1", "--out", str(tmp_path)])
        text = (tmp_path / "args/docmod/packs/d1.yaml").read_text(encoding="utf-8")
        assert "enabled: false" in text
        assert "rulebook_pack.RulebookPack" in text

    def test_generated_pack_actually_works_with_one_rule_and_no_code(self, tmp_path):
        """The acceptance test: a domain owner writes ONE rule, nothing else."""
        import yaml

        from tools.cli.scaffold import main

        main(["docmod-pack", "demo2", "--display-name", "Demo 2",
              "--entity-type", "standard", "--out", str(tmp_path)])
        rb = tmp_path / "args/docmod/rulebook_demo2.yaml"
        rb.write_text(
            "rules:\n  - id: r1\n    pattern: '(?i)\\bOLD-SPEC-1\\b'\n"
            "    verdict: deprecated\n    severity: high\n"
            "    rationale: Superseded.\n    replacement: NEW-SPEC-2\n",
            encoding="utf-8",
        )
        cfg = yaml.safe_load((tmp_path / "args/docmod/packs/demo2.yaml").read_text(encoding="utf-8"))
        cfg["rulebook_path"] = str(rb)          # generated path is repo-relative
        pack = RulebookPack(config=cfg)

        ents = pack.extract("Built to OLD-SPEC-1 throughout.", _ref())
        assert [e.label for e in ents] == ["OLD-SPEC-1"]
        v = pack.evaluate(ents[0], None)
        assert v.currency_verdict == "deprecated" and v.is_finding
        assert pack.recommend(ents[0], v, None).label == "NEW-SPEC-2"

    def test_rerunning_never_clobbers_authored_rules(self, tmp_path):
        """The other scaffold targets silently overwrite; this one writes into
        the repo, so it must not."""
        from tools.cli.scaffold import main

        main(["docmod-pack", "keep", "--display-name", "Keep", "--out", str(tmp_path)])
        rb = tmp_path / "args/docmod/rulebook_keep.yaml"
        rb.write_text("rules:\n  - id: mine\n    pattern: 'MINE'\n", encoding="utf-8")
        main(["docmod-pack", "keep", "--display-name", "Keep", "--out", str(tmp_path)])
        assert "mine" in rb.read_text(encoding="utf-8"), "re-run clobbered authored rules"

    def test_catalog_flavor_generates_a_valid_python_stub(self, tmp_path):
        from tools.cli.scaffold import main

        rc = main(["docmod-pack", "safety_ctl", "--display-name", "Safety Ctl",
                   "--flavor", "catalog", "--evidence-table", "sc_controls",
                   "--out", str(tmp_path)])
        assert rc == 0
        py = tmp_path / "tools/doc_modernization/packs/safety_ctl.py"
        assert py.exists()
        compile(py.read_text(encoding="utf-8"), str(py), "exec")   # must be valid Python
        assert "SafetyCtlPack" in py.read_text(encoding="utf-8")
        assert "sc_controls" in py.read_text(encoding="utf-8")

    def test_catalog_flavor_requires_its_evidence_table(self, tmp_path):
        """A catalog pack with no truth table can only be a stub that lies."""
        from tools.cli.scaffold import main

        rc = main(["docmod-pack", "x", "--display-name", "X", "--flavor", "catalog",
                   "--out", str(tmp_path)])
        assert rc == 2

    def test_dry_run_writes_nothing(self, tmp_path):
        from tools.cli.scaffold import main

        main(["docmod-pack", "dr", "--display-name", "DR", "--dry-run", "--out", str(tmp_path)])
        assert not list(tmp_path.rglob("*.yaml"))

    def test_unknown_flavor_fails_cleanly(self, tmp_path):
        from tools.cli.scaffold import main

        assert main(["docmod-pack", "z", "--display-name", "Z",
                     "--flavor", "nope", "--out", str(tmp_path)]) == 2
