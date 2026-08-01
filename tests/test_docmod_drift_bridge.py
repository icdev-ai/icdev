# CUI // SP-CTI
"""docmod findings -> DocDrift (impact / HITL regen / NIST re-map).

Before this bridge, ACOIC's only producer was network topology drift, which
guesses the affected document from a collection *tag*. A docmod finding knows
the real doc_id, section and chunk — so this is what makes the compliance
response domain-agnostic and document-anchored.

conftest forces ICDEV_STORAGE_BACKEND=sqlite; no network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _finding(**over):
    base = {
        "finding_id": "find-1",
        "doc_id": "doc-42",
        "version_id": "ver-1",
        "pack_id": "crypto_protocols",
        "entity_label": "TLS 1.1",
        "entity_type": "protocol",
        "finding_type": "deprecated_tech",
        "currency_verdict": "deprecated",
        "severity": "high",
        "state": "open",
        "confidence": 1.0,
        "section_heading": "3.2 Transport Security",
        "page": 7,
        "chunk_link_id": "link-9",
        "rationale": "TLS 1.1 deprecated by RFC 8996",
        "classification": "CUI",
        "tenant_id": None,
    }
    base.update(over)
    return base


@pytest.fixture
def bridge_env(monkeypatch):
    """Stub findings + config; capture handle_drift calls."""
    import tools.doc_modernization.drift_bridge as db

    monkeypatch.setattr(db, "_connect", lambda: None)
    monkeypatch.setattr(db, "_pack_controls", lambda pack_id: [])
    return db


def _run(bridge, findings, threshold=0.7, controls=None):
    from tools.document_intelligence import acoic

    with patch("tools.doc_modernization.get_findings", return_value=findings), \
         patch("tools.doc_modernization.pack_loader.load_config", return_value={"confidence_threshold": threshold}), \
         patch.object(acoic, "handle_drift", return_value={"event_id": "evt-1", "enqueued": [{"item_id": "i1"}], "controls": controls or {}}) as hd:
        out = bridge.emit_drift(conn=object())
    return out, hd


class TestBridgeEmitsRealDocumentAnchors:
    def test_finding_becomes_drift_with_the_real_doc_id(self, bridge_env):
        out, hd = _run(bridge_env, [_finding()])

        assert out["emitted"] == 1
        ev = hd.call_args.args[0]
        # The whole point: a real doc_id, not a collection-tag guess.
        assert ev["document_id"] == "doc-42"
        assert ev["source"] == "docmod.crypto_protocols"
        assert ev["entity"] == "TLS 1.1"
        assert ev["severity"] == "high"

    def test_document_anchor_is_carried_for_the_reviewer(self, bridge_env):
        """A reviewer must see WHICH paragraph without re-running the scan."""
        _, hd = _run(bridge_env, [_finding()])
        ev = hd.call_args.args[0]
        assert ev["section_heading"] == "3.2 Transport Security"
        assert ev["page"] == 7
        assert ev["chunk_link_id"] == "link-9"
        assert ev["finding_id"] == "find-1"

    def test_source_is_namespaced_per_pack(self, bridge_env):
        """pack_id is the domain axis — per-domain views filter on it."""
        _, hd = _run(bridge_env, [_finding(pack_id="network_hardware")])
        assert hd.call_args.args[0]["source"] == "docmod.network_hardware"


class TestIdempotency:
    def test_dedup_key_is_the_stable_finding_id(self, bridge_env):
        """Without this, a scheduled sweep re-inserts the same drift every run."""
        _, hd = _run(bridge_env, [_finding()])
        assert hd.call_args.args[0]["dedup_key"] == "docmod:find-1"

    def test_a_new_finding_gets_a_new_key(self, bridge_env):
        """docmod is append-only: superseding yields a NEW finding_id, which must
        produce genuinely new drift rather than collapse onto the old one."""
        _, hd = _run(bridge_env, [_finding(finding_id="find-2")])
        assert hd.call_args.args[0]["dedup_key"] == "docmod:find-2"


class TestGating:
    def test_subthreshold_findings_never_reach_an_ssp(self, bridge_env):
        out, hd = _run(bridge_env, [_finding(confidence=0.4)], threshold=0.7)
        assert out["emitted"] == 0
        assert out["skipped_subthreshold"] == 1
        assert not hd.called

    def test_resolved_findings_do_not_re_emit(self, bridge_env):
        out, hd = _run(bridge_env, [_finding(state="superseded")])
        assert out["emitted"] == 0
        assert out["skipped_state"] == 1
        assert not hd.called

    def test_redline_drafted_still_counts_as_live_drift(self, bridge_env):
        """A pending redline still needs a human — the drift is not resolved."""
        out, _ = _run(bridge_env, [_finding(state="redline_drafted")])
        assert out["emitted"] == 1

    def test_unparseable_confidence_is_treated_as_zero(self, bridge_env):
        out, _ = _run(bridge_env, [_finding(confidence=None)], threshold=0.7)
        assert out["skipped_subthreshold"] == 1


class TestNistControls:
    def test_controls_come_from_pack_yaml_only(self, monkeypatch):
        """Controls are declared, never inferred — an invented control mapping in
        an SSP is worse than none (TRUST rule 1)."""
        import tools.doc_modernization.drift_bridge as db

        monkeypatch.setattr(db, "_connect", lambda: None)
        monkeypatch.setattr(db, "_pack_controls", lambda pack_id: ["SC-8", "SC-13"])
        _, hd = _run(db, [_finding()])
        assert hd.call_args.args[0]["control_ids"] == ["SC-8", "SC-13"]

    def test_pack_without_declared_controls_maps_none(self, bridge_env):
        _, hd = _run(bridge_env, [_finding()])
        assert hd.call_args.args[0]["control_ids"] == []

    def test_pack_controls_reads_nist_controls_key(self):
        import tools.doc_modernization.drift_bridge as db

        class _Pack:
            config = {"nist_controls": ["AC-2", " SC-8 "]}

        with patch("tools.doc_modernization.pack_loader.load_packs", return_value={"p": _Pack()}):
            assert db._pack_controls("p") == ["AC-2", "SC-8"]

    def test_missing_pack_yields_no_controls(self):
        import tools.doc_modernization.drift_bridge as db

        with patch("tools.doc_modernization.pack_loader.load_packs", return_value={}):
            assert db._pack_controls("nope") == []


class TestResilience:
    def test_one_bad_finding_does_not_kill_the_sweep(self, bridge_env):
        from tools.document_intelligence import acoic

        findings = [_finding(finding_id="bad"), _finding(finding_id="good", doc_id="doc-9")]
        with patch("tools.doc_modernization.get_findings", return_value=findings), \
             patch("tools.doc_modernization.pack_loader.load_config", return_value={"confidence_threshold": 0.7}), \
             patch.object(acoic, "handle_drift", side_effect=[RuntimeError("boom"), {"event_id": "e", "enqueued": []}]):
            out = bridge_env.emit_drift(conn=object())
        assert out["emitted"] == 1
        assert len(out["errors"]) == 1


class TestHandleDriftIdempotency:
    """acoic.handle_drift previously dropped dedup_key, so every scheduled
    producer duplicated its drift on each run."""

    def test_handle_drift_forwards_dedup_key(self, tmp_path, monkeypatch):
        from tools.db.storage import get_connection as _real
        import tools.document_intelligence.acoic as acoic_mod

        db_path = str(tmp_path / "hd.db")
        monkeypatch.setattr(acoic_mod, "get_connection", lambda *a, **k: _real(db_path=db_path))

        a = acoic_mod.handle_drift({"source": "docmod.x", "entity": "E", "severity": "high",
                                    "dedup_key": "docmod:find-1"})
        b = acoic_mod.handle_drift({"source": "docmod.x", "entity": "E", "severity": "high",
                                    "dedup_key": "docmod:find-1"})
        assert a["event_id"] == b["event_id"], "same finding must not duplicate"

        conn = _real(db_path=db_path)
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM dic_drift_events").fetchone()
            assert (dict(n)["n"] if not isinstance(n, (list, tuple)) else n[0]) == 1
        finally:
            conn.close()

    def test_without_dedup_key_legacy_behaviour_is_unchanged(self, tmp_path, monkeypatch):
        from tools.db.storage import get_connection as _real
        import tools.document_intelligence.acoic as acoic_mod

        db_path = str(tmp_path / "hd2.db")
        monkeypatch.setattr(acoic_mod, "get_connection", lambda *a, **k: _real(db_path=db_path))
        out = acoic_mod.handle_drift({"source": "ndc", "entity": "E", "severity": "low"})
        assert out["event_id"]
