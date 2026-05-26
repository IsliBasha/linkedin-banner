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

    def test_left_zone_is_visually_clear(self):
        """
        Pixels at x=100 (deep in the profile-pic zone) should be the
        background colour throughout the full banner height — no content
        drawn there.
        """
        img = gb.draw_banner(COMMITS, LANGS, WEEKS)
        BG = gb.BG  # (13, 17, 23)

        non_bg = []
        for y in range(img.height):
            px = img.getpixel((100, y))
            if any(abs(px[c] - BG[c]) > 30 for c in range(3)):
                non_bg.append((100, y, px))

        assert not non_bg, (
            f"Found {len(non_bg)} non-background pixels in profile-pic zone "
            f"(x=100): {non_bg[:5]}"
        )

    def test_content_start_x_clears_profile_pic(self):
        """Content in the center section must start at x ≥ 210."""
        assert gb.CONTENT_LEFT_X >= 210, (
            f"CONTENT_LEFT_X={gb.CONTENT_LEFT_X} overlaps with profile picture "
            "(profile pic right edge ≈ 205px)"
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
        """52 weeks must not overflow the right side of the banner."""
        grid_width = 52 * (gb.GRID_CELL_SIZE + gb.GRID_GAP) - gb.GRID_GAP
        grid_x0 = gb.DIV2_X + 50   # approx start x
        assert grid_x0 + grid_width <= gb.BANNER_W, (
            f"Grid would extend to x={grid_x0 + grid_width}, "
            f"beyond banner width {gb.BANNER_W}"
        )
