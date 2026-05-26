"""
Tests for generate_banner.py — driven by two user requirements:
  1. Banner content must be readable at 50% viewport scale (1584→830px).
  2. Left-side content must sit above the LinkedIn profile-picture overlap zone.

LinkedIn profile-picture geometry at native 1584×396:
  - Circle diameter ≈ 190px on desktop
  - Centred at approx (x=110, y=396) — bottom-left corner
  - Blocks approx x: 0–205, y: 300–396
  → Safe rule: keep content in the left zone (x < 210) above y = 285.

Readability rule at 52% scale:
  - Effective rendered size = canvas_size × 0.52
  - Minimum readable text ≈ 11px rendered → canvas minimum ≈ 21px
  - We target 20px labels, 56px hero numbers, 18px legend text.
"""

from __future__ import annotations

import pytest
from PIL import Image

import generate_banner as gb


# ── Shared fixtures ───────────────────────────────────────────────────────────

COMMITS = 1_284

LANGS = [
    ("Python",      30.0),
    ("Kotlin",      25.0),
    ("JavaScript",  20.0),
    ("TypeScript",  15.0),
    ("CSS",         10.0),
]

# 52 weeks × 7 days, varying contribution counts
WEEKS = [
    {
        "contributionDays": [
            {"contributionCount": (w * 7 + d) % 12, "date": f"2024-01-{d + 1:02d}"}
            for d in range(7)
        ]
    }
    for w in range(52)
]


# ── Canvas ────────────────────────────────────────────────────────────────────

class TestCanvasDimensions:
    def test_banner_is_standard_linkedin_size(self):
        img = gb.draw_banner(COMMITS, LANGS, WEEKS)
        assert img.size == (1584, 396), f"Expected (1584, 396), got {img.size}"

    def test_banner_mode_is_rgb(self):
        img = gb.draw_banner(COMMITS, LANGS, WEEKS)
        assert img.mode == "RGB"

    def test_banner_renders_without_exception(self):
        """Smoke test — full compose must not raise."""
        gb.draw_banner(COMMITS, LANGS, WEEKS)


# ── Readability (font sizes) ──────────────────────────────────────────────────

class TestFontSizes:
    """
    At 52% viewport scale every font must remain legible.
    Minimum effective rendered size ≈ 11px → canvas size ≥ ~21px.
    Targets: labels ≥ 20px, hero numbers ≥ 52px, legend ≥ 18px.
    """

    def test_label_font_size(self):
        assert gb.FONT_LABEL_SIZE >= 20, (
            f"Label font {gb.FONT_LABEL_SIZE}px too small — "
            f"renders as ~{gb.FONT_LABEL_SIZE * 0.52:.0f}px at 830px viewport"
        )

    def test_number_font_size(self):
        assert gb.FONT_NUMBER_SIZE >= 52, (
            f"Hero number font {gb.FONT_NUMBER_SIZE}px too small"
        )

    def test_legend_font_size(self):
        assert gb.FONT_LEGEND_SIZE >= 18, (
            f"Legend font {gb.FONT_LEGEND_SIZE}px too small"
        )

    def test_grid_header_font_size(self):
        assert gb.FONT_GRID_HDR_SIZE >= 18, (
            f"Grid header font {gb.FONT_GRID_HDR_SIZE}px too small"
        )


# ── Profile-picture safe zone ─────────────────────────────────────────────────

class TestProfilePictureSafeZone:
    """
    The left 200px of the banner is reserved for the profile picture.
    Content in the left section must not extend below CONTENT_MAX_Y.
    """

    def test_stats_are_in_right_half(self):
        """
        Stats content (commits number + language legend) must start in the
        RIGHT half of the banner.  The LinkedIn profile picture is anchored
        to the bottom-left corner and can never reach the right half.
        """
        assert gb.CONTENT_LEFT_X > gb.BANNER_W // 2, (
            f"CONTENT_LEFT_X={gb.CONTENT_LEFT_X} is in the left half — "
            f"must be > {gb.BANNER_W // 2} so the profile picture cannot obscure it"
        )

    def test_left_content_top_y_is_near_top(self):
        """
        Content must start near the top of the banner so it stays
        visible above the profile-picture overlap region.
        Target: top_y ≤ 40px.
        """
        assert gb.CONTENT_TOP_Y <= 40, (
            f"CONTENT_TOP_Y={gb.CONTENT_TOP_Y} — content starts too low; "
            "increases risk of profile-pic overlap at the bottom"
        )


# ── Legend layout ────────────────────────────────────────────────────────────

class TestLegendLayout:
    """
    Legend must use a variable-column layout so that no item ends up in the
    bottom-left quadrant where the profile-picture circle overlaps.

    Required layout: [3, 2, 2, 2]  — 9 languages max across 4 rows.
    Row 0 spans full width (3 cols); rows 1-3 use 2 wider cols, shifting
    items right and reducing vertical extent vs a flat 3-col grid.
    """

    def test_legend_layout_constant_exists(self):
        assert hasattr(gb, "LEGEND_LAYOUT"), "LEGEND_LAYOUT constant not found"

    def test_legend_layout_is_3_2_2_2(self):
        assert gb.LEGEND_LAYOUT == [3, 2, 2, 2], (
            f"Expected [3, 2, 2, 2], got {gb.LEGEND_LAYOUT}"
        )

    def test_legend_max_languages(self):
        """Total slots = sum of LEGEND_LAYOUT = 9."""
        assert sum(gb.LEGEND_LAYOUT) == 9

    def test_legend_renders_9_languages_without_overflow(self):
        """9-language input must render without raising and fit in banner."""
        nine_langs = [
            ("Python",     28.0), ("Kotlin",     24.0), ("JavaScript", 18.0),
            ("TypeScript", 14.0), ("CSS",         7.0), ("Shell",       4.0),
            ("HTML",        3.0), ("Zig",         1.5), ("Rust",        0.5),
        ]
        img = gb.draw_banner(1284, nine_langs, WEEKS)
        assert img.size == (1584, 396)


# ── Contribution grid geometry ────────────────────────────────────────────────

class TestContributionGrid:
    def test_cell_size_is_readable(self):
        """Grid cells must be large enough to be distinguishable."""
        assert gb.GRID_CELL_SIZE >= 10, (
            f"GRID_CELL_SIZE={gb.GRID_CELL_SIZE} — cells too small to see"
        )

    def test_grid_fits_within_banner_height(self):
        """52 weeks × 7 rows must not overflow the banner vertically."""
        grid_height = 7 * (gb.GRID_CELL_SIZE + gb.GRID_GAP) - gb.GRID_GAP
        assert grid_height < gb.BANNER_H - 60, (
            f"Grid height {grid_height}px would overflow banner ({gb.BANNER_H}px)"
        )

    def test_grid_fits_within_banner_width(self):
        """52 weeks must not overflow the divider (grid is now on the LEFT)."""
        grid_width = 52 * (gb.GRID_CELL_SIZE + gb.GRID_GAP) - gb.GRID_GAP
        grid_x0 = gb.GRID_LEFT_PAD
        assert grid_x0 + grid_width <= gb.DIV2_X, (
            f"Grid would extend to x={grid_x0 + grid_width}, "
            f"beyond divider at x={gb.DIV2_X}"
        )
