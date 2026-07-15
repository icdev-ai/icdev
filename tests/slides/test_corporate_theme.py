# CUI // SP-CTI
"""The light corporate status theme, and the fallbacks that keep it from breaking
the dark themes it lives beside.

The whole risk of adding the first LIGHT theme to a builder written for dark ones
is that a colour the dark themes never set — a card fill, the text on the header
band — silently defaults to something that renders black-on-black or blue-on-navy.
So these tests assert two things: the light theme is actually legible, and every
dark theme still resolves the new keys to its old behaviour.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.slides import pptx_builder as B
from tools.slides.constants import THEME_PALETTES


def _lum(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _contrast(fg, bg) -> float:
    def chan(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def L(rgb):
        return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2])

    a, b = L(fg), L(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


class TestTheThemeExists:
    def test_it_is_registered(self):
        assert "corporate_status" in THEME_PALETTES

    def test_it_is_actually_light(self):
        """A 'light corporate' theme with a dark page would be neither."""
        assert _lum(THEME_PALETTES["corporate_status"]["bg"]) >= 200


class TestLegibility:
    """Nothing the theme puts on a surface may be unreadable on it. WCAG AA is
    4.5:1; the loud failure this guards against is black-on-black."""

    def _p(self):
        return THEME_PALETTES["corporate_status"]

    def test_body_text_reads_on_the_card(self):
        p = self._p()
        card = p.get("card", p["dark"])
        assert _contrast(B._on_card_text(p), card) >= 4.5

    def test_the_band_title_reads_on_the_navy_band(self):
        """This is the trap: the builder used to hardcode the blue accent as the
        title colour, and blue on navy is unreadable. The light theme must override
        to white."""
        p = self._p()
        assert _contrast(B._band_text(p), p["dark"]) >= 4.5

    def test_body_and_subtext_read_on_the_page(self):
        p = self._p()
        assert _contrast(p["text"], p["bg"]) >= 4.5
        assert _contrast(p["subtext"], p["bg"]) >= 4.5


class TestDarkThemesAreUnchanged:
    """The point of the _opt() fallback: a dark theme sets none of the new keys and
    must behave exactly as before."""

    DARK = [t for t in THEME_PALETTES if t != "corporate_status"]

    def test_card_fill_falls_back_to_dark(self):
        for name in self.DARK:
            p = THEME_PALETTES[name]
            assert tuple(B._card_fill(p)) == tuple(B._rgb(p, "dark")), name

    def test_band_text_falls_back_to_accent(self):
        for name in self.DARK:
            p = THEME_PALETTES[name]
            assert tuple(B._band_text(p)) == tuple(B._rgb(p, "accent")), name

    def test_a_theme_without_a_rotation_reuses_its_accent(self):
        for name in self.DARK:
            p = THEME_PALETTES[name]
            if "rotation" not in p:
                rot = B._rotation(p)
                assert len(rot) == 1
                assert tuple(rot[0]) == tuple(B._rgb(p, "accent")), name


class TestRotation:
    def test_the_corporate_theme_cycles_four_accents(self):
        rot = B._rotation(THEME_PALETTES["corporate_status"])
        assert len(rot) == 4
        assert len({tuple(c) for c in rot}) == 4   # all distinct


class TestItRendersEndToEnd:
    """A palette that passes every unit check can still throw at draw time. Build a
    real deck and confirm each slide type produces shapes."""

    def _deck(self):
        return [
            {"slide_type": "title", "title": "Status Update",
             "subtitle": "July 2026", "speaker_notes": "sub"},
            {"slide_type": "card_grid", "title": "Focus Areas",
             "bullets": [
                 {"label": str(i), "title": f"Area {i}",
                  "body": "body text here", "meta": "SME: name"}
                 for i in range(1, 7)
             ]},
            {"slide_type": "content", "title": "Overview",
             "bullets": ["one", "two", "three"]},
            {"slide_type": "outro", "title": "Close", "bullets": ["done"]},
        ]

    def test_it_builds_a_file_with_every_slide(self):
        from pptx import Presentation

        path = B.build(self._deck(), theme="corporate_status", title="T")
        prs = Presentation(path)
        assert len(prs.slides) == 4
        for s in prs.slides:
            assert len(s.shapes) > 0

    def test_the_card_grid_draws_six_distinct_accent_stripes(self):
        """The signature. Six cards should not all wear the same colour."""
        from pptx import Presentation

        path = B.build(self._deck(), theme="corporate_status", title="T")
        prs = Presentation(path)
        grid = prs.slides[1]
        fills = set()
        for sh in grid.shapes:
            try:
                if sh.fill.type is not None and sh.height < B.Inches(0.12):
                    fills.add(str(sh.fill.fore_color.rgb))
            except Exception:
                pass
        # At least the four rotation colours appear among the thin stripes.
        assert len(fills) >= 4
