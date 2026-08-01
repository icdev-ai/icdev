# CUI // SP-CTI
"""sop_workflows pack — tool / command / platform drift in procedures.

This pack is YAML only: the shared RulebookPack evaluator plus a rulebook. So
what is worth testing is not Python behaviour (covered by the rulebook_pack
tests) but the CONTENT of the shipped rules:

  * every rule compiles and uses vocabulary the CHECK constraints accept —
    a bad verdict string is only discovered at INSERT time otherwise;
  * the rules that need a false-positive guard actually have one. The costly
    failure mode for a documentation sweep is not a missed match, it is a rule
    that flags every document and trains reviewers to ignore findings;
  * the pack ships disabled, so a scan cannot pick up unvalidated rules.

No network, no LLM, no DB — evaluate()/recommend() take conn=None because a
rulebook verdict is a pure function of the rule (TRUST rule 1).
"""
from __future__ import annotations

import re
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

_PACK_YAML = REPO_ROOT / "args" / "docmod" / "packs" / "sop_workflows.yaml"
_RULEBOOK_YAML = REPO_ROOT / "args" / "docmod" / "rulebook_sop_workflows.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(_PACK_YAML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pack(cfg) -> RulebookPack:
    return RulebookPack(config=cfg)


def _ref() -> ChunkRef:
    return ChunkRef(doc_id="doc-1", version_id="ver-1", section="Deploy")


def _rule_ids(pack: RulebookPack, text: str) -> set:
    return {(e.attributes or {}).get("rule_id") for e in pack.extract(text, _ref())}


class TestPackDeclaration:
    def test_uses_the_shared_rulebook_evaluator(self, cfg):
        assert cfg["evaluator"] == "tools.doc_modernization.packs.rulebook_pack.RulebookPack"
        assert cfg["rulebook_path"] == "args/docmod/rulebook_sop_workflows.yaml"

    def test_ships_disabled_until_rules_are_validated(self, cfg):
        # Rules cover industry-wide retirements but have not been checked
        # against a real corpus. Enabling by default would put unvalidated
        # findings in front of reviewers on the next sweep.
        assert cfg["enabled"] is False

    def test_entity_type_is_declared_in_the_shared_vocabulary(self, cfg):
        # entity_type has no SQL CHECK constraint (migration 257 leaves it a
        # plain TEXT column), so KG_ENTITY_TYPES is the ONLY registry — an
        # undeclared type would fail silently rather than at INSERT.
        assert cfg["entity_types"] == ["tool_reference"]
        assert "tool_reference" in KG_ENTITY_TYPES

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
        # The rationale lands in front of a human reviewer; "deprecated" with
        # no authority is not reviewable evidence.
        for rule in pack._rules():
            assert len(rule.get("rationale", "").strip()) > 30, rule["id"]

    def test_editing_the_rulebook_changes_the_evidence_hash(self, pack):
        # scanner.py takes an incremental skip on an unchanged evidence hash,
        # so the snapshot must cover the rules themselves.
        snapshot = pack.evidence_snapshot(None)
        assert snapshot and len(snapshot) == 64


class TestScopeBoundary:
    """Scope is tool/command/platform drift ONLY.

    Role and org-structure rules would need an organizational catalog that does
    not exist in this repo; a rule asserting a team is defunct would therefore
    be asserting something the pack cannot evidence.
    """

    def test_no_rule_claims_an_org_or_role_is_defunct(self, pack):
        # Word-anchored: the product name "Microsoft Teams" is a legitimate
        # replacement target, while a bare "team" is an org claim.
        banned = [r"\bteam\b", r"\bdepartment\b", r"\borg chart\b",
                  r"\breports to\b", r"\bjob title\b", r"\brole\b"]
        for rule in pack._rules():
            haystack = f"{rule['id']} {rule.get('rationale', '')}".lower()
            for term in banned:
                assert not re.search(term, haystack), f"{rule['id']} strays into org/role scope"


class TestMatching:
    @pytest.mark.parametrize("rule_id,text", [
        ("sop-travis-ci", "Trigger the build in Travis CI as described."),
        ("sop-docker-compose-v1", "Run docker-compose up -d on the host."),
        ("sop-docker-machine", "Provision the host with docker-machine create."),
        ("sop-helm-init-tiller", "First run helm init to prepare the cluster."),
        ("sop-kubectl-generator-flag",
         "Use kubectl run web --image=nginx --generator=run-pod/v1 here."),
        ("sop-kubectl-export-flag", "Capture it with kubectl get svc web -o yaml --export."),
        ("sop-podsecuritypolicy", "Apply the restricted PodSecurityPolicy before deploy."),
        ("sop-gcr-io-registry", "Push the image to gcr.io/my-project/app."),
        ("sop-python2-invocation", "Execute python2.7 setup.py install."),
        ("sop-easy-install", "Install the agent using easy_install agent.egg."),
        ("sop-nosetests", "Validate with nosetests -v."),
        ("sop-apt-key", "Add the vendor key with apt-key add vendor.gpg."),
        ("sop-net-tools-ifconfig", "Check the interface with ifconfig eth0."),
        ("sop-net-tools-netstat", "Confirm the port with netstat -an."),
        ("sop-wmic", "Query the host with wmic os get caption."),
        ("sop-hipchat", "Notify the on-call room in HipChat."),
        ("sop-skype-for-business", "Escalate over Skype for Business."),
        ("sop-bitbucket-server", "Open the pull request in Bitbucket Server."),
    ])
    def test_rule_matches_its_canonical_phrasing(self, pack, rule_id, text):
        assert rule_id in _rule_ids(pack, text)

    @pytest.mark.parametrize("text", [
        # Compose V2 still reads this filename — flagging it would fire on
        # essentially every containerized repo's documentation.
        "The stack is defined in docker-compose.yml at the repo root.",
        "The compose file docker-compose.yaml is checked in.",
        # A year is not an interpreter version.
        "The service was introduced in python 2024 planning docs.",
        # 'svn' ships commented-out precisely because bare tokens over-match.
        "Use git stash before switching branches.",
        "Deploy via GitHub Actions and check the workflow run.",
        "Escalate in Microsoft Teams to the duty officer.",
    ])
    def test_known_false_positive_traps_stay_clean(self, pack, text):
        assert not {r for r in _rule_ids(pack, text) if r}


class TestVerdictAndReplacement:
    def test_verdict_cites_the_rule_as_evidence(self, pack):
        entity = next(e for e in pack.extract("Build runs in Travis CI.", _ref()))
        verdict = pack.evaluate(entity, None)
        assert entity.entity_type == "tool_reference"
        assert verdict.currency_verdict == "retired"
        assert verdict.finding_type == "stale_reference"
        assert verdict.confidence == 1.0
        assert verdict.evidence[0]["source"] == "rule:sop-travis-ci"

    def test_replacement_resolves_to_the_rule_that_produced_it(self, pack):
        entity = next(e for e in pack.extract("Run docker-compose up.", _ref()))
        replacement = pack.recommend(entity, pack.evaluate(entity, None), None)
        # A replacement offered in a redline must trace to a rulebook entry;
        # an unsourced one would put an invention into a document.
        assert replacement.label == "docker compose"
        assert replacement.source_ref == "rule:sop-docker-compose-v1"

    def test_platform_shutdowns_are_stale_reference_not_deprecated_tech(self, pack, cfg):
        # A decommissioned platform is a reference that no longer resolves;
        # a deprecated command still exists but should not be used.
        shutdowns = {"sop-travis-ci", "sop-hipchat", "sop-skype-for-business",
                     "sop-bitbucket-server", "sop-gcr-io-registry"}
        for rule in pack._rules():
            if rule["id"] in shutdowns:
                assert rule.get("finding_type") == "stale_reference", rule["id"]


class TestLoaderIntegration:
    def test_loader_skips_the_pack_while_it_ships_disabled(self):
        from tools.doc_modernization.pack_loader import load_packs

        assert "sop_workflows" not in load_packs(force=True)

    def test_loader_registers_the_pack_once_enabled(self, cfg, tmp_path, monkeypatch):
        # Proves the pack is scan-ready without mutating the shipped file.
        from tools.doc_modernization import pack_loader

        packs_dir = tmp_path / "packs"
        packs_dir.mkdir()
        (packs_dir / "sop_workflows.yaml").write_text(
            yaml.safe_dump({**cfg, "enabled": True}), encoding="utf-8"
        )
        monkeypatch.setattr(pack_loader, "PACKS_DIR", packs_dir)
        packs = pack_loader.load_packs(force=True)
        try:
            assert "sop_workflows" in packs
            assert packs["sop_workflows"].entity_types == ["tool_reference"]
        finally:
            pack_loader.load_packs(force=True)
