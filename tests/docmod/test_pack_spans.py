# CUI // SP-CTI
"""dwr-anchor-01: packs stop discarding the offsets they already compute.

Every docmod pack matched an entity with a compiled regex and used
``m.start()``/``m.end()`` ONLY to slice a +/-60 char ``context`` string,
throwing the span away. ``CandidateEntity`` now carries ``span_start`` /
``span_end`` -- CHUNK-LOCAL, the convention ``claim_lifecycle.verify_claim_anchors``
already reads -- and every extractor populates them.

THE INVARIANT, asserted per pack on the text the match came from:

    chunk_text[e.span_start:e.span_end] == e.raw_match

Two guards beside the behavioural one, because the defect survived by being
invisible: a structural scan proving EVERY ``CandidateEntity(`` construction in
the packs, the temporal extractor and the catalog scaffold template passes a
span (a new pack that drops it fails here), and a rendered-template check so a
future ``icdev scaffold docmod-pack --flavor catalog`` cannot regress it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef, match_span

REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKS_DIR = REPO_ROOT / "tools" / "doc_modernization" / "packs"
_TEMPORAL = REPO_ROOT / "tools" / "doc_modernization" / "temporal.py"
_CATALOG_TEMPLATE = REPO_ROOT / "data" / "templates" / "docmod_packs" / "catalog" / "pack.py.j2"


def _ref(link_id: str | None = "link-1") -> ChunkRef:
    return ChunkRef(doc_id="doc-1", version_id="doc-1_v1", chunk_link_id=link_id, section="S")


def _assert_anchored(entities: list[CandidateEntity], text: str) -> None:
    """The one assertion the card asks for, applied to every extracted entity."""
    assert entities, "fixture text produced no entities -- the invariant was not exercised"
    for e in entities:
        assert isinstance(e.span_start, int) and isinstance(e.span_end, int), (
            f"{e.pack_id}: {e.label!r} carries no span"
        )
        assert 0 <= e.span_start < e.span_end <= len(text), (e.pack_id, e.label, e.span_start, e.span_end)
        assert text[e.span_start:e.span_end] == e.raw_match, (
            f"{e.pack_id}: text[{e.span_start}:{e.span_end}]={text[e.span_start:e.span_end]!r} "
            f"!= raw_match={e.raw_match!r}"
        )


# ── the helper every pack shares ─────────────────────────────────────────────

class TestMatchSpan:
    def test_plain_match_is_the_regex_span(self):
        m = re.search(r"TLS 1\.1", "use TLS 1.1 here")
        assert match_span(m) == (m.start(), m.end()) == (4, 11)

    def test_whitespace_admitting_pattern_is_trimmed_to_the_stripped_match(self):
        """Packs set ``raw_match = m.group(0).strip()``. A pattern that swallows
        surrounding whitespace would otherwise leave the span bounding a string
        that is NOT raw_match -- the exact drift the invariant exists to catch."""
        text = "runs on  Windows Server 2012  clusters"
        m = re.search(r"\s+Windows Server \d{4}\s+", text)
        start, end = match_span(m)
        assert (start, end) != (m.start(), m.end())
        assert text[start:end] == m.group(0).strip() == "Windows Server 2012"


# ── the shipped packs, one by one ────────────────────────────────────────────

class TestEveryPackKeepsItsSpan:
    def test_crypto_protocols(self):
        from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack

        pack = CryptoProtocolsPack(config={"pack_id": "crypto_protocols"})
        text = "All external services shall use TLS v1.1 and MD5 digests over SSLv3."
        ents = pack.extract(text, _ref())
        _assert_anchored(ents, text)
        assert len(ents) >= 2

    def test_network_hardware(self):
        from tools.doc_modernization.packs.network_hardware import NetworkHardwarePack

        pack = NetworkHardwarePack(config={
            "pack_id": "network_hardware",
            "extraction": {"patterns": [r"(?i)\bCatalyst\s?\d{4}\b"], "inventory_terms": False},
        })
        text = "Core switching uses Catalyst 6500 chassis pairs behind Catalyst 9500 spines."
        _assert_anchored(pack.extract(text, _ref()), text)

    def test_software(self):
        from tools.doc_modernization.packs.software import SoftwarePack

        pack = SoftwarePack(config={
            "pack_id": "software",
            "extraction": {"patterns": [r"(?i)\bWindows Server \d{4}(?:\s?R2)?\b"]},
        })
        text = "File shares run on Windows Server 2012 R2 clusters, not Windows Server 2008."
        _assert_anchored(pack.extract(text, _ref()), text)

    def test_policy_refs_rulebook_temporal_and_dynamic_nist_sites(self):
        """policy_refs has THREE extract sites: the supersession rulebook, the
        temporal entities the dated rules add, and the dynamic NIST-revision
        matcher. All three must anchor."""
        from tools.doc_modernization.packs.policy_refs import PolicyRefsPack
        from tools.doc_modernization.temporal import TEMPORAL_KIND

        pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
        text = ("Controls are tailored per NIST SP 800-53 Rev 4 guidance; "
                "the annex is aligned to NIST SP 800-53 Rev 5 as well.")
        ents = pack.extract(text, _ref())
        _assert_anchored(ents, text)
        kinds = {(e.attributes or {}).get("kind") for e in ents}
        assert TEMPORAL_KIND in kinds, "a dated rule should have produced a temporal entity"
        assert any("nist_pub_id" in (e.attributes or {}) for e in ents), "dynamic NIST site not exercised"

    def test_change_control(self):
        from tools.doc_modernization.packs.change_control import ChangeControlPack

        pack = ChangeControlPack(config={
            "pack_id": "change_control",
            "extraction": {"patterns": [r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,8}){1,3}\b"]},
        })
        text = "Failover is handled by CORE-RTR-01 and EDGE-MX304-02."
        _assert_anchored(pack.extract(text, _ref()), text)

    @pytest.mark.parametrize("pack_key, text", [
        ("architecture_patterns", "Circuit breaking uses Hystrix in front of the monolith."),
        ("sop_workflows", "Build runs in Travis CI; then run docker-compose up."),
    ])
    def test_rulebook_pack_domains(self, pack_key, text):
        """RulebookPack is ONE class serving architecture_patterns AND
        sop_workflows -- assert it against each domain's real rulebook."""
        from tools.doc_modernization.packs.rulebook_pack import RulebookPack

        cfg = yaml.safe_load(
            (REPO_ROOT / "args" / "docmod" / "packs" / f"{pack_key}.yaml").read_text(encoding="utf-8")
        )
        pack = RulebookPack(config=cfg)
        _assert_anchored(pack.extract(text, _ref()), text)

    def test_temporal_entities_directly(self):
        from tools.doc_modernization.temporal import temporal_entities

        rules = [{
            "id": "r-dated", "pattern": r"(?i)\s*\bTEST-STD\b\s*",
            "compiled": re.compile(r"(?i)\s*\bTEST-STD\b\s*"),
            "effective_date": "2001-01-01", "sunset_date": "2021-09-23",
        }]
        text = "Cites  TEST-STD  throughout."
        ents = temporal_entities(rules, text, _ref(), pack_id="p", entity_type="standard")
        _assert_anchored(ents, text)
        assert ents[0].raw_match == "TEST-STD"

    def test_evidence_currency_has_no_text_span_and_says_so(self):
        """The anchor entity IS the citation, not a run of text: raw_match is
        empty and the span is None -- never (0, 0), which would anchor a redline
        to an empty slice at the top of the chunk."""
        from tools.doc_modernization.packs.evidence_currency import EvidenceCurrencyPack

        pack = EvidenceCurrencyPack(config={"pack_id": "evidence_currency"})
        for link in ("link-7", None):
            ents = pack.extract("TLS 1.1 and Catalyst 6500", _ref(link))
            assert len(ents) == 1
            e = ents[0]
            assert e.raw_match == ""
            assert e.span_start is None and e.span_end is None


# ── the default is None, never 0 ─────────────────────────────────────────────

def test_span_defaults_are_none_not_zero():
    e = CandidateEntity(label="x", entity_type="term", pack_id="p", chunk_ref=_ref())
    assert e.span_start is None and e.span_end is None


# ── structural: no construction site may drop the span again ─────────────────

def _construction_sites(source: str, where: str) -> list[tuple[int, set[str]]]:
    tree = ast.parse(source, filename=where)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "CandidateEntity":
                sites.append((node.lineno, {kw.arg for kw in node.keywords}))
    return sites


def _python_sources() -> list[tuple[str, str]]:
    out = [(str(p), p.read_text(encoding="utf-8")) for p in sorted(_PACKS_DIR.glob("*.py"))]
    out.append((str(_TEMPORAL), _TEMPORAL.read_text(encoding="utf-8")))
    return out


def test_every_construction_site_passes_a_span():
    checked = 0
    for where, src in _python_sources():
        for lineno, kws in _construction_sites(src, where):
            checked += 1
            assert {"span_start", "span_end"} <= kws, f"{where}:{lineno} builds a CandidateEntity without a span"
    assert checked >= 9, f"expected the known construction sites, found {checked}"


def test_catalog_scaffold_template_keeps_the_span():
    """Miss the template and every future scaffolded pack silently drops spans
    again -- the exact way this defect survived. Render it and read the AST of
    the RESULT, not the .j2 text."""
    from jinja2 import Template

    rendered = Template(_CATALOG_TEMPLATE.read_text(encoding="utf-8")).render(
        key="demo_ctl", display_name="Demo Ctl", class_name="DemoCtlPack",
        entity_type="term", evidence_table="sc_controls",
    )
    sites = _construction_sites(rendered, str(_CATALOG_TEMPLATE))
    assert sites, "template renders no CandidateEntity construction"
    for lineno, kws in sites:
        assert {"span_start", "span_end"} <= kws, f"rendered template line {lineno} drops the span"
    assert "match_span" in rendered
