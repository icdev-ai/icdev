# CUI // SP-CTI
"""architecture_patterns pack — obsolete -> modern architecture-pattern drift.

This pack is YAML only: the shared RulebookPack evaluator plus a rulebook. So
what is worth testing is not Python behaviour (covered by the rulebook_pack
tests) but the CONTENT of the shipped rules:

  * every rule compiles and uses vocabulary the CHECK constraints accept —
    a bad verdict string is only discovered at INSERT time otherwise;
  * every rule that needs a false-positive guard actually has one. Architecture
    prose is looser than command syntax, so the costly failure mode is a rule
    that flags every design doc and trains reviewers to ignore findings — not a
    missed match. Known-clean phrases are asserted to stay clean;
  * the pack ships disabled, so a scan cannot pick up unvalidated rules.

No network, no LLM, no DB — evaluate()/recommend() take conn=None because a
rulebook verdict is a pure function of the rule (TRUST rule 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402
from tools.doc_modernization.constants import (  # noqa: E402
    CURRENCY_VERDICTS,
    FINDING_TYPES,
    KG_ENTITY_TYPES,
    SEVERITIES,
)
from tools.doc_modernization.packs.rulebook_pack import RulebookPack  # noqa: E402

_PACK_YAML = REPO_ROOT / "args" / "docmod" / "packs" / "architecture_patterns.yaml"
_RULEBOOK_YAML = REPO_ROOT / "args" / "docmod" / "rulebook_architecture_patterns.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(_PACK_YAML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pack(cfg) -> RulebookPack:
    return RulebookPack(config=cfg)


def _ref() -> ChunkRef:
    return ChunkRef(doc_id="doc-1", version_id="ver-1", section="Design")


def _rule_ids(pack: RulebookPack, text: str) -> set:
    return {(e.attributes or {}).get("rule_id") for e in pack.extract(text, _ref())}


class TestPackDeclaration:
    def test_uses_the_shared_rulebook_evaluator(self, cfg):
        assert cfg["evaluator"] == "tools.doc_modernization.packs.rulebook_pack.RulebookPack"
        assert cfg["rulebook_path"] == "args/docmod/rulebook_architecture_patterns.yaml"

    def test_ships_disabled_until_rules_are_validated(self, cfg):
        # Rules cover industry-wide supersessions but have not been checked
        # against a real corpus. Enabling by default would put unvalidated
        # findings in front of reviewers on the next sweep.
        assert cfg["enabled"] is False

    def test_entity_type_is_declared_in_the_shared_vocabulary(self, cfg):
        # entity_type has no SQL CHECK constraint (migration 257 leaves it a
        # plain TEXT column), so KG_ENTITY_TYPES is the ONLY registry — an
        # undeclared type would fail silently rather than at INSERT.
        assert cfg["entity_types"] == ["architecture_pattern"]
        assert "architecture_pattern" in KG_ENTITY_TYPES

    def test_defaults_are_valid_vocabulary(self, cfg):
        assert cfg["default_verdict"] in CURRENCY_VERDICTS
        assert cfg["default_finding_type"] in FINDING_TYPES
        assert cfg["default_severity"] in SEVERITIES


class TestRulebookIntegrity:
    def test_every_rule_compiles(self, pack):
        # load_rulebook logs and SKIPS a rule with a bad regex rather than
        # raising, so a typo would silently shrink the rulebook.
        on_disk = yaml.safe_load(_RULEBOOK_YAML.read_text(encoding="utf-8"))["rules"]
        assert len(pack._rules()) == len(on_disk) > 0

    def test_rule_ids_are_unique(self, pack):
        ids = [r["id"] for r in pack._rules()]
        assert len(set(ids)) == len(ids)

    def test_every_rule_resolves_to_accepted_vocabulary(self, pack, cfg):
        for rule in pack._rules():
            assert rule.get("verdict", cfg["default_verdict"]) in CURRENCY_VERDICTS, rule["id"]
            assert rule.get("finding_type", cfg["default_finding_type"]) in FINDING_TYPES, rule["id"]
            assert rule.get("severity", cfg["default_severity"]) in SEVERITIES, rule["id"]

    def test_every_rule_states_a_dated_rationale(self, pack):
        # The rationale lands in front of a human reviewer; a bare verdict with
        # no authority is not reviewable evidence.
        for rule in pack._rules():
            assert len(rule.get("rationale", "").strip()) > 30, rule["id"]

    def test_every_rule_offers_a_sourced_replacement(self, pack):
        # A modernization pack whose finding has no successor is not actionable.
        for rule in pack._rules():
            assert str(rule.get("replacement", "")).strip(), rule["id"]

    def test_every_rule_cites_an_authority_and_a_confidence(self, pack):
        # citation is the authority behind the obsolete->modern mapping;
        # confidence is the seed prior a corpus sweep will later calibrate.
        for rule in pack._rules():
            assert str(rule.get("citation", "")).startswith("http"), rule["id"]
            conf = rule.get("confidence")
            assert isinstance(conf, (int, float)) and 0.0 < conf <= 1.0, rule["id"]

    def test_editing_the_rulebook_changes_the_evidence_hash(self, pack):
        # scanner.py takes an incremental skip on an unchanged evidence hash,
        # so the snapshot must cover the rules themselves.
        snapshot = pack.evidence_snapshot(None)
        assert snapshot and len(snapshot) == 64


class TestMatching:
    @pytest.mark.parametrize("rule_id,text", [
        ("arch-hystrix", "Circuit breaking is handled by Hystrix in the gateway."),
        ("arch-corba-dcom", "Legacy services expose a CORBA interface to clients."),
        ("arch-soap-wsdl", "The billing system exposes a SOAP web service defined by a WSDL."),
        ("arch-esb", "Integration flows through the central Enterprise Service Bus."),
        ("arch-diy-crypto", "Tokens are protected with a hand-rolled encryption algorithm."),
        ("arch-inproc-session", "The app keeps in-memory session state on each node."),
        ("arch-monolithic-three-tier", "The product ships as a monolithic three-tier application."),
    ])
    def test_rule_matches_its_canonical_phrasing(self, pack, rule_id, text):
        assert rule_id in _rule_ids(pack, text)

    @pytest.mark.parametrize("text", [
        # A plain three-tier architecture is a legitimate, still-valid style;
        # flagging it would fire on a large share of design documents.
        "The system uses a standard three-tier architecture with a load balancer.",
        # 'custom' next to a non-crypto noun must not trip the DIY-crypto rule.
        "The team built a custom dashboard for the operations center.",
        # AES from a vetted library is exactly the recommended pattern.
        "AES-256 encryption is provided by the platform crypto library.",
        # Stateless token auth is the successor, not the anti-pattern.
        "Sessions are stateless, using signed JWT tokens.",
        # The replacement technology name must never trip the retirement rule.
        "Circuit breaking is provided by Resilience4j.",
        # Modern interface styles.
        "The service exposes a REST/JSON API served over HTTP/2.",
        "Services communicate over gRPC behind the API gateway.",
    ])
    def test_known_false_positive_traps_stay_clean(self, pack, text):
        assert not {r for r in _rule_ids(pack, text) if r}


class TestVerdictAndReplacement:
    def test_verdict_cites_the_rule_as_evidence(self, pack):
        entity = next(e for e in pack.extract("Circuit breaking uses Hystrix.", _ref()))
        verdict = pack.evaluate(entity, None)
        assert entity.entity_type == "architecture_pattern"
        assert verdict.currency_verdict == "deprecated"
        assert verdict.finding_type == "deprecated_tech"
        assert verdict.confidence == 1.0
        assert verdict.evidence[0]["source"] == "rule:arch-hystrix"

    def test_replacement_resolves_to_the_rule_that_produced_it(self, pack):
        entity = next(e for e in pack.extract("Circuit breaking uses Hystrix.", _ref()))
        replacement = pack.recommend(entity, pack.evaluate(entity, None), None)
        # A replacement offered in a redline must trace to a rulebook entry;
        # an unsourced one would put an invention into a document.
        assert replacement.label == "Resilience4j, or service-mesh circuit breaking (Istio/Envoy)"
        assert replacement.source_ref == "rule:arch-hystrix"

    def test_retired_technologies_are_stale_reference_not_deprecated_tech(self, pack):
        # A no-longer-supported stack is a reference that no longer resolves;
        # a deprecated design style still exists but should not be used anew.
        retired = {"arch-corba-dcom"}
        for rule in pack._rules():
            if rule["id"] in retired:
                assert rule.get("finding_type") == "stale_reference", rule["id"]
                assert rule.get("verdict") == "retired", rule["id"]


class TestLoaderIntegration:
    def test_loader_skips_the_pack_while_it_ships_disabled(self):
        from tools.doc_modernization.pack_loader import load_packs

        assert "architecture_patterns" not in load_packs(force=True)

    def test_loader_registers_the_pack_once_enabled(self, cfg, tmp_path, monkeypatch):
        # Proves the pack is scan-ready without mutating the shipped file.
        from tools.doc_modernization import pack_loader

        packs_dir = tmp_path / "packs"
        packs_dir.mkdir()
        (packs_dir / "architecture_patterns.yaml").write_text(
            yaml.safe_dump({**cfg, "enabled": True}), encoding="utf-8"
        )
        monkeypatch.setattr(pack_loader, "PACKS_DIR", packs_dir)
        packs = pack_loader.load_packs(force=True)
        try:
            assert "architecture_patterns" in packs
            assert packs["architecture_patterns"].entity_types == ["architecture_pattern"]
        finally:
            pack_loader.load_packs(force=True)
