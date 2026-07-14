# CUI // SP-CTI
"""The model may name the buckets. It may not touch a number.

Naming a category is a judgement — "is a hypervisor licence Software or
Virtualisation?" has no deterministic answer, only a conventional one — and that is
what a model is genuinely good at. Pricing is not a judgement, and every test here
exists to keep the two apart.

The cage is CODE, not prompt. A prompt that says "do not invent categories" is a
request. `allowed.get(label.lower())` is a guarantee.

No network. The model is stubbed, and several of these stubs are deliberately
MALICIOUS — a well-behaved model proves nothing.

Invented content. ICDEV is a public repo.
"""
from __future__ import annotations

import importlib
import json
import types

import pytest

from tools.bom import taxonomy as T
from tools.bom.lines import ExtractedLine


def _line(desc, lid=None, uom=""):
    return ExtractedLine(
        line_id=lid or f"l-{desc[:8]}", line_hash="h", source_document="d.xlsx",
        source_sheet="BOM", source_locator="A1", raw_text=desc,
        description=desc, uom=uom,
        qty=2, unit_price=18000.0, extended_price=36000.0,
    )


@pytest.fixture
def lines():
    return [
        _line("Core Layer-3 switch, 48x25GbE", "l1"),
        _line("2U rack server, dual Xeon", "l2"),
        _line("VMware vSphere licence, per socket", "l3"),
    ]


def _stub(monkeypatch, payload, *, schema_valid=True, boom=None):
    """Replace the model.

    `taxonomy` does ``from tools.cortex import api``, which reads the ATTRIBUTE off
    the package — so patching ``sys.modules["tools.cortex.api"]`` misses it
    entirely and the test quietly calls a real LLM over the network. Patch the
    attribute on the package object.
    """
    def extract(prompt, schema, ctx=None):
        if boom:
            raise boom
        _stub.last_prompt = prompt
        return types.SimpleNamespace(
            text=json.dumps(payload) if not isinstance(payload, str) else payload,
            metadata={"schema_valid": schema_valid},
        )

    fake = types.SimpleNamespace(extract=extract)
    monkeypatch.setattr(importlib.import_module("tools.cortex"), "api", fake)
    return fake


_GOOD = {"categories": [
    {"label": "Network Hardware", "definition": "Switches, routers, firewalls."},
    {"label": "Compute Hardware", "definition": "Servers and their components."},
    {"label": "Software Licences", "definition": "Per-socket and subscription."},
]}


class TestTheModelIsNeverShownAPrice:
    """Cheaper to guarantee than to detect. It cannot anchor on a number it was
    never given."""

    def test_no_price_reaches_the_prompt(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        T.propose(lines)
        prompt = _stub.last_prompt
        assert "18000" not in prompt
        assert "36000" not in prompt
        assert "$" not in prompt

    def test_no_quantity_reaches_the_prompt(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        T.propose(lines)
        # The descriptions carry "48x25GbE" and "2U"; the QTY field (2) is not sent.
        assert "qty" not in _stub.last_prompt.lower()

    def test_the_schema_has_no_numeric_field_at_all(self):
        """A schema that lets the model emit a number is a schema that invites it
        to."""
        for schema in (T.PROPOSE_SCHEMA, T.ASSIGN_SCHEMA):
            blob = json.dumps(schema)
            assert '"number"' not in blob
            assert '"integer"' not in blob


class TestTheIntentSteersTheScheme:
    """Item descriptions say what each line IS. They do not say how the person
    paying for it wants to see it grouped — and that is the entire question. The
    same lines are a compute/network/software split to an engineer and a
    capital/subscription/services split to a budget owner. Neither is wrong."""

    def test_the_intent_reaches_the_prompt(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        T.propose(lines, intent="Earmark multi-year capital for a research lab.")
        assert "multi-year capital" in _stub.last_prompt

    def test_no_intent_is_fine_and_says_nothing_extra(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        T.propose(lines)
        assert "WHAT THIS BILL OF MATERIALS IS FOR" not in _stub.last_prompt

    def test_the_intent_still_cannot_smuggle_a_price_into_the_answer(
        self, monkeypatch, lines,
    ):
        """The cage does not care where the money came from."""
        _stub(monkeypatch, {"categories": [
            {"label": "Budget", "definition": "Everything under $150,000."},
        ]})
        assert T.propose(lines, intent="Stay under $150,000.").categories == []


class TestAModelThatTalksAboutMoneyIsVoided:
    """It has stopped doing the naming task, and there is no way to know what else
    it did instead."""

    def test_a_price_in_a_label_voids_the_whole_response(self, monkeypatch, lines):
        _stub(monkeypatch, {"categories": [
            {"label": "Network Hardware", "definition": "Switches."},
            {"label": "Big ticket ($200,000+)", "definition": "Expensive."},
        ]})
        tax = T.propose(lines)
        assert tax.categories == []
        assert "money" in tax.note

    def test_a_price_in_a_definition_voids_it_too(self, monkeypatch, lines):
        _stub(monkeypatch, {"categories": [
            {"label": "Compute", "definition": "Servers, typically $25,000 each."},
        ]})
        assert T.propose(lines).categories == []

    def test_the_good_labels_are_NOT_salvaged(self, monkeypatch, lines):
        """Keeping the clean half of a contaminated response is how you ship the
        contaminated half you did not notice."""
        _stub(monkeypatch, {"categories": [
            {"label": "Network Hardware", "definition": "Switches."},
            {"label": "Priced at $10,000", "definition": "x"},
        ]})
        assert "Network Hardware" not in T.propose(lines).labels


class TestItCannotGrowTheTaxonomyByClassifyingIntoIt:
    """SELECT-only. A category that appears at assignment time was never approved
    by anybody."""

    def _tax(self):
        return T.Taxonomy(categories=[
            T.Category("Network Hardware"), T.Category("Compute Hardware"),
        ], status="approved")

    def test_an_invented_label_is_discarded(self, monkeypatch, lines):
        _stub(monkeypatch, {"assignments": [
            {"line_id": "t0", "label": "Network Hardware"},
            {"line_id": "t1", "label": "Quantum Cryogenics"},   # not on the menu
        ]})
        got = T.classify_lines(lines, self._tax())
        assert got["l1"] == "Network Hardware"
        assert got["l2"] != "Quantum Cryogenics"

    def test_an_unassigned_line_lands_in_the_fallback_NOT_a_new_label(
        self, monkeypatch, lines,
    ):
        """An approved taxonomy is a CLOSED set — it is what every rollup, pivot and
        leadership slide is grouped by.

        The bug: the backstop used to run the keyword classifier here, which
        cheerfully returned "Peripherals" and "Computers" — labels nobody had
        approved. The taxonomy grew through the back door, and the workbook then
        had category tabs that were not in the scheme the human signed off.
        """
        _stub(monkeypatch, {"assignments": [
            {"line_id": "t1", "label": "Quantum Cryogenics"},
        ]})
        got = T.classify_lines(lines, self._tax())
        assert set(got.values()) <= set(self._tax().labels) | {T.FALLBACK}, got

    def test_every_line_gets_a_category_even_if_the_model_ignores_it(
        self, monkeypatch, lines,
    ):
        """Silence is not an answer."""
        _stub(monkeypatch, {"assignments": []})
        got = T.classify_lines(lines, self._tax())
        assert set(got) == {"l1", "l2", "l3"}
        assert all(v.strip() for v in got.values())

    def test_case_is_forgiven_but_the_label_is_canonicalised(self, monkeypatch, lines):
        _stub(monkeypatch, {"assignments": [
            {"line_id": "t0", "label": "network hardware"},
        ]})
        assert T.classify_lines(lines, self._tax())["l1"] == "Network Hardware"

    def test_a_model_that_prices_a_line_is_ignored_for_that_line(
        self, monkeypatch, lines,
    ):
        _stub(monkeypatch, {"assignments": [
            {"line_id": "t0", "label": "Network Hardware $36,000"},
        ]})
        assert T.classify_lines(lines, self._tax())["l1"] != "Network Hardware $36,000"


class TestTheModelIsNeverToldWhoTheCustomerIs:
    """A line_id is "<filename>:<sheet>:<row>". Sending it ships the customer's
    document names to a third party for no benefit — an opaque token does the same
    job and discloses nothing."""

    def test_no_filename_reaches_the_prompt(self, monkeypatch):
        real = ExtractedLine(
            line_id="Q3 Acquisition Budget CONFIDENTIAL.xlsx:Pricing:14",
            line_hash="h", source_document="Q3 Acquisition Budget CONFIDENTIAL.xlsx",
            source_sheet="Pricing", source_locator="A14",
            raw_text="Core switch", description="Core switch",
        )
        _stub(monkeypatch, {"assignments": [{"line_id": "t0",
                                             "label": "Network Hardware"}]})
        got = T.classify_lines(
            [real], T.Taxonomy(categories=[T.Category("Network Hardware")],
                               status="approved"),
        )
        assert "CONFIDENTIAL" not in _stub.last_prompt
        assert ".xlsx" not in _stub.last_prompt
        # ...and it still classified correctly through the opaque key.
        assert got[real.line_id] == "Network Hardware"

    def test_a_mangled_key_does_not_silently_lose_the_line(self, monkeypatch, lines):
        """The bug this catches: the prompt read "- id=<key> ::" and the model
        copied "id=..." INTO the field, so not one assignment matched and every
        line fell to the backstop. It looked exactly like a working classifier
        producing poor results."""
        _stub(monkeypatch, {"assignments": [
            {"line_id": "id=t0", "label": "Network Hardware"},
        ]})
        got = T.classify_lines(lines, T.Taxonomy(
            categories=[T.Category("Network Hardware")], status="approved"))
        # It is not assigned — but it is NOT silently given a plausible label
        # either. It lands in the fallback, where somebody can see it.
        assert got["l1"] == T.FALLBACK


class TestItCannotApproveItself:
    def test_a_proposal_binds_nothing(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        tax = T.propose(lines)
        assert tax.status == "proposed"
        assert tax.binds is False

    def test_a_human_approval_binds_and_moves_the_version(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        tax = T.approve(T.propose(lines), note="reviewed")
        assert tax.binds is True
        assert tax.version == 2
        assert tax.derived_by == "human"

    def test_approval_does_not_change_the_labels(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD)
        p = T.propose(lines)
        assert T.approve(p).labels == p.labels


class TestItDegradesRatherThanGuesses:
    def test_no_model_means_no_taxonomy_not_a_fabricated_one(self, monkeypatch, lines):
        _stub(monkeypatch, {}, boom=RuntimeError("air-gapped"))
        tax = T.propose(lines)
        assert tax.categories == []
        assert tax.derived_by == "deterministic"
        assert "air-gapped" in tax.note

    def test_an_invalid_schema_is_refused(self, monkeypatch, lines):
        _stub(monkeypatch, _GOOD, schema_valid=False)
        assert T.propose(lines).categories == []

    def test_unparseable_output_is_refused(self, monkeypatch, lines):
        _stub(monkeypatch, "not json at all")
        assert T.propose(lines).categories == []

    def test_an_empty_taxonomy_still_classifies_every_line(self, monkeypatch, lines):
        """With no categories there is nothing to SELECT from, so the deterministic
        path carries it. The workbook still builds."""
        _stub(monkeypatch, {"assignments": []})
        got = T.classify_lines(lines, T.Taxonomy(categories=[]))
        assert len(got) == 3
        assert all(v.strip() for v in got.values())


class TestRoundTrip:
    def test_a_taxonomy_survives_save_and_load(self, monkeypatch, lines, tmp_path):
        _stub(monkeypatch, _GOOD)
        tax = T.approve(T.propose(lines))
        p = tmp_path / "tax.json"
        T.save(tax, p)
        back = T.load(p)
        assert back.labels == tax.labels
        assert back.binds is True
        assert back.version == 2
