# CUI // SP-CTI
"""Temporal validity for standards references (dmx-packs-03).

Frozen-clock proofs that a rulebook entry's optional effective/sunset/review_by
dates drive proactive, time-bounded findings:

  * past sunset_date            -> stale_reference,   severity HIGH
  * within N days of sunset      -> expiring_reference, severity WARNING (medium)
  * before effective / dateless  -> NO temporal finding
  * dedupe stability across two sweeps at one frozen clock
  * phase flip (warning -> high) yields DISTINCT dedupe keys (sane escalation)

Pure temporal logic is a function of (rule dates, clock) — no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.doc_modernization import temporal
from tools.doc_modernization.base_pack import ChunkRef
from tools.doc_modernization.packs.policy_refs import PolicyRefsPack
from tools.doc_modernization.scanner import dedupe_key

_REF = ChunkRef(doc_id="doc-t", version_id="doc-t_v1", section="Security")


def _at(y, m, d) -> datetime:
    """A frozen, timezone-aware UTC instant."""
    return datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)


def _rule(**kw) -> dict:
    base = {"id": "r-test", "pattern": r"(?i)\bTEST-STD\b"}
    base.update(kw)
    return base


# ── pure temporal_verdict ────────────────────────────────────────────────────

def test_past_sunset_is_high_stale_reference():
    v = temporal.temporal_verdict(
        _rule(effective_date="2001-01-01", sunset_date="2021-09-23"),
        now=_at(2026, 7, 24), window=90,
    )
    assert v is not None
    assert v.finding_type == "stale_reference"
    assert v.severity == "high"
    assert v.currency_verdict == "retired"
    assert v.is_finding is True
    assert v.evidence[0]["source"] == "rule:r-test"
    assert v.evidence[0]["date"] == "2021-09-23"


def test_within_window_is_warning_expiring_reference():
    # sunset 60 days out with a 90-day window -> proactive warning.
    v = temporal.temporal_verdict(
        _rule(sunset_date="2026-09-22"), now=_at(2026, 7, 24), window=90,
    )
    assert v is not None
    assert v.finding_type == "expiring_reference"
    assert v.severity == "medium"          # WARNING tier
    assert v.currency_verdict == "deprecated"
    assert v.is_finding is True


def test_far_future_sunset_is_no_finding():
    # 264 days out, outside the 90-day window -> the window is not open yet.
    assert temporal.temporal_verdict(
        _rule(sunset_date="2026-09-22"), now=_at(2026, 1, 1), window=90,
    ) is None


def test_before_effective_is_no_finding():
    # Past its sunset in absolute terms, but the standard was not yet in force.
    assert temporal.temporal_verdict(
        _rule(effective_date="2001-05-25", sunset_date="2026-09-22"),
        now=_at(1999, 1, 1), window=90,
    ) is None


def test_dateless_rule_is_no_finding():
    assert temporal.temporal_verdict(_rule(), now=_at(2026, 7, 24)) is None
    assert temporal.temporal_verdict(
        _rule(effective_date="2001-01-01"), now=_at(2026, 7, 24),
    ) is None  # effective only, no sunset


def test_review_by_is_surfaced_in_evidence():
    v = temporal.temporal_verdict(
        _rule(sunset_date="2021-09-23", review_by="2025-01-01"),
        now=_at(2026, 7, 24),
    )
    assert "review_by 2025-01-01" in v.evidence[0]["detail"]


def test_naive_now_is_coerced_and_utcnow_is_aware():
    # No naive datetimes anywhere: the module's clock is tz-aware, and a naive
    # `now` is coerced rather than raising on comparison.
    assert temporal.utcnow().tzinfo is not None
    naive = datetime(2026, 7, 24, 12, 0, 0)  # noqa: DTZ001 - deliberate for the test
    v = temporal.temporal_verdict(_rule(sunset_date="2021-09-23"), now=naive)
    assert v is not None and v.severity == "high"


# ── config-driven window ─────────────────────────────────────────────────────

def test_window_days_from_config_and_default():
    assert temporal.window_days({"sunset_warning_window_days": 30}) == 30
    assert temporal.window_days({}) == temporal.DEFAULT_WINDOW_DAYS
    assert temporal.window_days({"sunset_warning_window_days": 0}) == temporal.DEFAULT_WINDOW_DAYS
    assert temporal.window_days({"sunset_warning_window_days": "bad"}) == temporal.DEFAULT_WINDOW_DAYS


def test_shipped_config_declares_the_window():
    from tools.doc_modernization.pack_loader import load_config
    assert int(load_config()["sunset_warning_window_days"]) > 0


# ── pack integration against the REAL rulebook_policy.yaml ────────────────────

def _temporal_entity(pack, text):
    ents = pack.extract(text, _REF)
    return next(e for e in ents if (e.attributes or {}).get("kind") == "temporal")


def test_policy_pack_flags_past_sunset_high():
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    pack.clock = _at(2026, 10, 1)  # after FIPS 140-2 sunset 2026-09-22
    ent = _temporal_entity(pack, "All modules must hold FIPS 140-2 validation.")
    v = pack.evaluate(ent, None)  # temporal branch never touches the DB
    assert v.finding_type == "stale_reference" and v.severity == "high"


def test_policy_pack_warns_within_window():
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    pack.clock = _at(2026, 8, 1)  # ~52 days before FIPS 140-2 sunset
    ent = _temporal_entity(pack, "All modules must hold FIPS 140-2 validation.")
    v = pack.evaluate(ent, None)
    assert v.finding_type == "expiring_reference" and v.severity == "medium"


def test_policy_pack_no_temporal_finding_before_window():
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    pack.clock = _at(2026, 1, 1)  # FIPS sunset 264 days out, window is 90
    ent = _temporal_entity(pack, "All modules must hold FIPS 140-2 validation.")
    v = pack.evaluate(ent, None)
    assert v.is_finding is False  # emitted but evaluates to a non-finding


def test_policy_pack_dateless_standard_emits_no_temporal_entity():
    # NIST SP 800-52 Rev 1 carries no date fields -> supersession entity only.
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    ents = pack.extract("Use NIST SP 800-52 Rev 1 for TLS configuration.", _REF)
    assert ents and all((e.attributes or {}).get("kind") != "temporal" for e in ents)


# ── dedupe stability + escalation (finding_id scheme) ────────────────────────

def _key_for(pack, text, frozen):
    pack.clock = frozen
    ent = _temporal_entity(pack, text)
    v = pack.evaluate(ent, None)
    return dedupe_key(_REF.doc_id, pack.pack_id, ent.label, v.finding_type)


def test_dedupe_key_stable_across_two_sweeps_same_clock():
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    text = "All modules must hold FIPS 140-2 validation."
    k1 = _key_for(pack, text, _at(2026, 10, 1))
    k2 = _key_for(pack, text, _at(2026, 10, 1))
    assert k1 == k2  # re-sweep at one clock -> identical key -> no duplicate


def test_phase_flip_warning_to_high_changes_dedupe_key():
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    text = "All modules must hold FIPS 140-2 validation."
    warn = _key_for(pack, text, _at(2026, 8, 1))    # expiring_reference
    past = _key_for(pack, text, _at(2026, 10, 1))   # stale_reference
    # Distinct keys => the old warning is superseded and one HIGH finding opens;
    # never a lingering stale "warning" and never a duplicate.
    assert warn != past


def test_temporal_is_distinct_from_supersession_finding():
    # A standard that is both superseded AND past sunset yields two independent,
    # non-colliding dedupe keys.
    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    pack.clock = _at(2026, 10, 1)
    ents = pack.extract("Controls follow NIST SP 800-53 Rev 4.", _REF)
    keys = set()
    for e in ents:
        v = pack.evaluate(e, None)
        if v.is_finding:
            keys.add(dedupe_key(_REF.doc_id, pack.pack_id, e.label, v.finding_type))
    assert len(keys) == 2  # superseded_standard + stale_reference


# ── reuse: the shared RulebookPack evaluator gains the same capability ────────

def test_rulebook_pack_reuses_temporal_logic(tmp_path):
    import yaml

    from tools.doc_modernization.packs.rulebook_pack import RulebookPack

    rb = tmp_path / "rulebook_x.yaml"
    rb.write_text(yaml.safe_dump({"rules": [{
        "id": "x-legacy-proto",
        "pattern": r"(?i)\bLEGACYPROTO\b",
        "sunset_date": "2020-01-01",
    }]}), encoding="utf-8")
    pack = RulebookPack(config={"pack_id": "x", "rulebook_path": str(rb),
                                "entity_types": ["protocol"]})
    pack.clock = _at(2026, 7, 24)
    ent = _temporal_entity(pack, "The device still speaks LEGACYPROTO on the wire.")
    v = pack.evaluate(ent, None)
    assert v.finding_type == "stale_reference" and v.severity == "high"


def test_dateless_rulebook_pack_emits_no_temporal_entities():
    # Regression guard: a dateless RulebookPack (the sop_workflows rules carry no
    # date fields) still emits only its primary entities -> unchanged behaviour.
    from tools.doc_modernization.packs.rulebook_pack import RulebookPack

    pack = RulebookPack(config={
        "pack_id": "sop_workflows",
        "rulebook_path": "args/docmod/rulebook_sop_workflows.yaml",
        "entity_types": ["tool_reference"],
    })
    ents = pack.extract("Trigger the build in Travis CI as described.", _REF)
    assert ents and all((e.attributes or {}).get("kind") != "temporal" for e in ents)
