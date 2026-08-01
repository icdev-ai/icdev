# CUI // SP-CTI
"""Schema + content guards for the ideation frame library (dvg-frames-01).

Pins the shipped args/ideation_frames.yaml against the loader's strict
validator, plus the DVG-specific invariants: a versioned library, orthogonal
generative frames, and the domain-relevant seed set the card requires.
"""
import textwrap

import pytest
import yaml

from tools.config.ideation_frames import (
    IdeationFrameError,
    VALID_MODES,
    get_frame_pairs,
    get_frames,
    get_version,
    validate_library,
)


class TestShippedLibrary:
    def test_shipped_library_is_valid(self):
        summary = validate_library()  # raises on any defect
        assert summary["version"]
        assert "generative" in summary["sets"]

    def test_versioned(self):
        assert get_version().strip()
        assert get_version() != "unversioned"

    def test_generative_set_present_and_generative(self):
        frames = get_frames("generative")
        assert frames, "generative frame set must exist and be non-empty"
        assert all(f.mode == "generative" for f in frames), "every frame in the generative set must be generative"

    def test_required_domain_frames_present(self):
        """The card requires at minimum these DoD/air-gap/accreditation-shaped
        generative frames (by stance, not upstream's consumer-software list)."""
        pairs = get_frame_pairs("generative")
        names = " ".join(n.lower() for n, _ in pairs)
        fragments = " ".join(f.lower() for _, f in pairs)
        blob = names + " " + fragments
        for concept in ("adversary", "accreditor", "sustainment", "air-gap", "invert", "budget"):
            assert concept in blob, f"expected a generative frame covering '{concept}'"
        # adjacent-industry transplant + analogy-from-nature
        assert "industry" in fragments or "transplant" in names
        assert "nature" in fragments or "biolog" in fragments

    def test_generative_frames_are_orthogonal(self):
        """Two frames with identical name or prompt_fragment are a wasted branch
        and wasted spend — validate_library enforces this, assert it holds."""
        frames = get_frames("generative")
        names = [f.name.lower() for f in frames]
        fragments = [f.prompt_fragment.lower() for f in frames]
        assert len(names) == len(set(names))
        assert len(fragments) == len(set(fragments))

    def test_pairs_shape_matches_orchestrator_contract(self):
        """get_frame_pairs returns (name, prompt_fragment) — the shape the chain
        orchestrator branch/advisor builders consume."""
        pairs = get_frame_pairs("generative")
        assert pairs and all(isinstance(p, tuple) and len(p) == 2 for p in pairs)
        assert all(isinstance(n, str) and isinstance(frag, str) and n and frag for n, frag in pairs)

    def test_valid_modes_constant(self):
        assert VALID_MODES == ("generative", "evaluative")


class TestValidatorRejectsBadInput:
    def _write(self, tmp_path, text):
        p = tmp_path / "frames.yaml"
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def test_missing_version_rejected(self, tmp_path):
        p = self._write(tmp_path, """
            frame_sets:
              g:
                frames:
                  - {key: a, name: A, stance: s, prompt_fragment: pf, mode: generative}
        """)
        with pytest.raises(IdeationFrameError, match="version"):
            validate_library(p)

    def test_duplicate_key_rejected(self, tmp_path):
        p = self._write(tmp_path, """
            version: t-1.0
            frame_sets:
              g:
                frames:
                  - {key: a, name: A, stance: s, prompt_fragment: pf1, mode: generative}
                  - {key: a, name: B, stance: s, prompt_fragment: pf2, mode: generative}
        """)
        with pytest.raises(IdeationFrameError, match="duplicate key"):
            validate_library(p)

    def test_duplicate_fragment_rejected(self, tmp_path):
        p = self._write(tmp_path, """
            version: t-1.0
            frame_sets:
              g:
                frames:
                  - {key: a, name: A, stance: s, prompt_fragment: same, mode: generative}
                  - {key: b, name: B, stance: s, prompt_fragment: same, mode: generative}
        """)
        with pytest.raises(IdeationFrameError, match="wasted branch"):
            validate_library(p)

    def test_invalid_mode_rejected(self, tmp_path):
        p = self._write(tmp_path, """
            version: t-1.0
            frame_sets:
              g:
                frames:
                  - {key: a, name: A, stance: s, prompt_fragment: pf, mode: sideways}
        """)
        with pytest.raises(IdeationFrameError, match="invalid mode"):
            validate_library(p)

    def test_missing_key_field_rejected(self, tmp_path):
        p = self._write(tmp_path, """
            version: t-1.0
            frame_sets:
              g:
                frames:
                  - {name: A, stance: s, prompt_fragment: pf, mode: generative}
        """)
        with pytest.raises(IdeationFrameError, match="missing"):
            validate_library(p)


class TestRuntimeGettersDegradeGracefully:
    def test_unknown_set_returns_empty(self):
        assert get_frames("does-not-exist") == []

    def test_malformed_frames_skipped_not_raised(self, tmp_path):
        p = tmp_path / "frames.yaml"
        p.write_text(
            yaml.safe_dump({
                "version": "t-1.0",
                "frame_sets": {"g": {"frames": [
                    {"key": "ok", "name": "OK", "stance": "s", "prompt_fragment": "pf", "mode": "generative"},
                    {"key": "bad"},  # malformed — must be skipped, not raised
                ]}},
            }),
            encoding="utf-8",
        )
        frames = get_frames("g", path=p)
        assert len(frames) == 1
        assert frames[0].key == "ok"

    def test_mode_filter(self, tmp_path):
        p = tmp_path / "frames.yaml"
        p.write_text(
            yaml.safe_dump({
                "version": "t-1.0",
                "frame_sets": {"mixed": {"frames": [
                    {"key": "g1", "name": "G1", "stance": "s", "prompt_fragment": "pf1", "mode": "generative"},
                    {"key": "e1", "name": "E1", "stance": "s", "prompt_fragment": "pf2", "mode": "evaluative"},
                ]}},
            }),
            encoding="utf-8",
        )
        assert len(get_frames("mixed", mode="generative", path=p)) == 1
        assert len(get_frames("mixed", mode="evaluative", path=p)) == 1
        assert len(get_frames("mixed", path=p)) == 2
