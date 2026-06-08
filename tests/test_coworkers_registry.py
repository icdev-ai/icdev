"""CoWorker registry tests — reference YAML loading + mode propagation."""
from __future__ import annotations

from icdev.tools.coworkers.context_factory import build_chat_link
from icdev.tools.coworkers.registry import get_coworker, list_coworkers


def test_strategos_reference_coworker_loaded():
    """args/coworkers/strategos.yaml is parsed with mode=bespoke."""
    cw = get_coworker("ref:strategos")
    assert cw is not None, "Strategos reference co-worker not found in registry"
    assert cw.name == "Strategos"
    assert cw.mode == "bespoke"
    assert "sg_conflict_events" in cw.rag_tables
    assert "strategos.md" in cw.manifest_shards


def test_strategos_chat_link_includes_mode():
    """build_chat_link encodes mode=bespoke for bespoke co-workers."""
    cw = get_coworker("ref:strategos")
    assert cw is not None
    link = build_chat_link(cw)
    assert "mode=bespoke" in link


def test_generic_reference_coworker_has_mode_generic():
    """Non-bespoke reference co-workers default to mode=generic."""
    cw = get_coworker("ref:security")
    assert cw is not None
    assert cw.mode == "generic"
    link = build_chat_link(cw)
    assert "mode=" not in link  # generic mode is omitted from URL


def test_registry_counts_include_strategos():
    """list_coworkers() includes the new Strategos card."""
    all_cw = list_coworkers()
    ids = {c.id for c in all_cw}
    assert "ref:strategos" in ids
    # At minimum: 7 personas + 8 ace roles + 6 refs = 21
    assert len(all_cw) >= 21
