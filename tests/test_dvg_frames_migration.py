# CUI // SP-CTI
"""frames-02: council advisors migrated to the frame library, behavior identical.

The load-bearing test is exact equality between the YAML-loaded `council_default`
set and the historical hardcoded `_COUNCIL_ADVISORS` — same advisors, same order,
same text. Plus: the code-level fallback still yields the hardcoded list when the
library is unavailable (invoke_council is reachable from the cross-repo
council_query MCP tool and must never fail because a config file moved), and the
divergence loader now reads its generative frames from the same single source.
"""
import importlib

from tools.config.ideation_frames import get_frame_pairs
from tools.llm.chain_orchestrator import (
    _COUNCIL_ADVISORS,
    _DIVERGENCE_FRAMES,
    ChainOrchestrator,
)


class TestCouncilMigration:
    def test_yaml_council_default_matches_hardcoded_exactly(self):
        """One source of truth: the YAML `council_default` (evaluative) set must
        equal the historical _COUNCIL_ADVISORS list byte-for-byte, in order."""
        pairs = get_frame_pairs("council_default", mode="evaluative")
        assert list(pairs) == list(_COUNCIL_ADVISORS)

    def test_orchestrator_loads_advisors_from_library(self):
        orch = ChainOrchestrator.__new__(ChainOrchestrator)  # no router needed
        advisors = orch._load_council_advisors()
        assert list(advisors) == list(_COUNCIL_ADVISORS)

    def test_council_fallback_when_library_unavailable(self, monkeypatch):
        """If the loader yields nothing (missing/broken YAML), the orchestrator
        falls back to the hardcoded constant rather than returning an empty panel."""
        # Force the loader import inside _load_council_advisors to return [].
        mod = importlib.import_module("tools.config.ideation_frames")
        monkeypatch.setattr(mod, "get_frame_pairs", lambda *a, **k: [])
        orch = ChainOrchestrator.__new__(ChainOrchestrator)
        advisors = orch._load_council_advisors()
        assert list(advisors) == list(_COUNCIL_ADVISORS)

    def test_advisor_count_and_names_unchanged(self):
        pairs = get_frame_pairs("council_default", mode="evaluative")
        names = [n for n, _ in pairs]
        assert names == [
            "The Contrarian", "The First Principles Thinker", "The Expansionist",
            "The Outsider", "The Executor",
        ]


class TestDivergenceReadsLibrary:
    def test_divergence_loader_reads_generative_set_from_library(self):
        orch = ChainOrchestrator.__new__(ChainOrchestrator)
        frames = orch._load_divergence_frames("generative")
        # The shipped library has 8 generative frames (dvg-frames-01), more than
        # the 6 inline defaults — proving the loader reads the library, not inline.
        assert len(frames) == 8
        names = {n for n, _ in frames}
        assert "The Adversary" in names and "The Accreditor" in names

    def test_divergence_falls_back_to_inline_when_set_missing(self, monkeypatch):
        mod = importlib.import_module("tools.config.ideation_frames")
        monkeypatch.setattr(mod, "get_frame_pairs", lambda *a, **k: [])
        orch = ChainOrchestrator.__new__(ChainOrchestrator)
        frames = orch._load_divergence_frames("generative")
        assert list(frames) == list(_DIVERGENCE_FRAMES)
