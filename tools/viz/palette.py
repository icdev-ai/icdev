# CUI // SP-CTI
"""Shared design tokens for the Viz Kernel.

Single source of truth: reuses ``THEME_PALETTES`` from
``tools/slides/constants.py`` (navy/gold midnight_executive et al.) and exposes
each colour in every form the renderers need:

  - ``.hex(key)``      → "#0A1628"           (SVG / HTML / matplotlib)
  - ``.rgb01(key)``    → (0.039, 0.086, …)   (matplotlib normalized)
  - ``.rgb255(key)``   → (10, 22, 40)        (raw tuple)
  - ``.rgbcolor(key)`` → pptx RGBColor        (python-pptx; lazy import)

Plus a categorical ``series_hex`` / ``series_rgb01`` colour cycle for
multi-series charts, anchored on the theme accent.
"""
from __future__ import annotations

from typing import Sequence

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

# Categorical cycle (accent first), tuned for dark navy backgrounds.
_SERIES_255: list[tuple[int, int, int]] = [
    (0xC8, 0xA9, 0x51),   # gold (accent)
    (0x4A, 0x90, 0xD9),   # blue
    (0x2E, 0xCC, 0x71),   # green
    (0xE6, 0x7E, 0x22),   # orange
    (0x9B, 0x59, 0xB6),   # purple
    (0x1A, 0xBC, 0x9C),   # teal
    (0xE7, 0x4C, 0x3C),   # red
    (0xF3, 0x9C, 0x12),   # amber
]


def _to_hex(rgb: Sequence[int]) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _to_01(rgb: Sequence[int]) -> tuple[float, float, float]:
    r, g, b = rgb
    return (r / 255.0, g / 255.0, b / 255.0)


class Palette:
    """Theme-aware colour accessor used by all renderers."""

    def __init__(self, theme: str = DEFAULT_THEME):
        self.theme = theme if theme in THEME_PALETTES else DEFAULT_THEME
        self._p: dict[str, tuple[int, int, int]] = THEME_PALETTES[self.theme]

    # ── single colour, multiple forms ───────────────────────────────────────
    def rgb255(self, key: str) -> tuple[int, int, int]:
        return tuple(self._p.get(key, self._p["text"]))  # type: ignore[return-value]

    def hex(self, key: str) -> str:
        return _to_hex(self.rgb255(key))

    def rgb01(self, key: str) -> tuple[float, float, float]:
        return _to_01(self.rgb255(key))

    def rgbcolor(self, key: str):
        """Return a python-pptx RGBColor (lazy import to keep this module light)."""
        from pptx.dml.color import RGBColor
        r, g, b = self.rgb255(key)
        return RGBColor(r, g, b)

    # ── categorical series cycle ─────────────────────────────────────────────
    def series_rgb255(self, i: int) -> tuple[int, int, int]:
        return _SERIES_255[i % len(_SERIES_255)]

    def series_hex(self, i: int) -> str:
        return _to_hex(self.series_rgb255(i))

    def series_rgb01(self, i: int) -> tuple[float, float, float]:
        return _to_01(self.series_rgb255(i))

    def series_rgbcolor(self, i: int):
        from pptx.dml.color import RGBColor
        r, g, b = self.series_rgb255(i)
        return RGBColor(r, g, b)


def get_palette(theme: str = DEFAULT_THEME) -> Palette:
    return Palette(theme)
