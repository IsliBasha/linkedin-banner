#!/usr/bin/env python3
"""
LinkedIn Banner Auto-Generator
================================
Generates a 1584×396 cover image from live GitHub data.

Usage (local):
    export GITHUB_USERNAME="yourname"
    export GH_TOKEN="ghp_..."
    export PORTFOLIO_URL="yoursite.dev"   # optional
    python generate_banner.py
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ══════════════════════════════════════════════════════════════
# CONSTANTS  — edit these to customise the banner
# ══════════════════════════════════════════════════════════════
PORTFOLIO_URL: str = os.getenv("PORTFOLIO_URL", "yourportfolio.dev")
OUTPUT_PATH:   str = "banner.png"
FONT_DIR:      Path = Path("fonts")

# Canvas size (LinkedIn cover photo standard)
BANNER_W, BANNER_H = 1584, 396

# ── Layout ─────────────────────────────────────────────────────
# The LinkedIn profile picture is ~190px Ø, centred at the
# bottom-left corner of the cover photo.  Keep x < 215 empty.
# Content starts at CONTENT_LEFT_X; push CONTENT_TOP_Y to the
# top so nothing is hidden behind the profile-picture circle.
CONTENT_LEFT_X: int = 220    # first pixel where text may appear (left section)
CONTENT_TOP_Y:  int = 24     # top of the stats block — near banner top

DIV2_X: int = 880            # divider x: stats | contribution grid

# ── Font sizes — calibrated for readability at ~52% viewport scale ──
# At 1584→830px render scale, multiply by 0.52 for effective px.
# Targets: labels ≥ 11px effective → 20px canvas; numbers ≥ 27px effective.
FONT_LABEL_SIZE:    int = 20   # section labels / captions (// commits · 12mo)
FONT_NUMBER_SIZE:   int = 56   # hero stat numbers  (e.g. 1,284)
FONT_LEGEND_SIZE:   int = 18   # language legend items
FONT_GRID_HDR_SIZE: int = 18   # contribution grid header

# ── Contribution grid geometry ──────────────────────────────────
GRID_CELL_SIZE: int = 10   # each cell square side (px)
GRID_GAP:       int = 2    # gap between cells (px)

# ── Legend row layout ───────────────────────────────────────────
# Number of language items per row. Variable width so lower rows
# use wider columns, keeping items away from the profile-pic circle.
# [3, 2, 2, 2] = 9 languages max across 4 rows.
LEGEND_LAYOUT: list[int] = [3, 2, 2, 2]

# ── Colour palette — terminal dark (GitHub dark mode) ──────────
BG          = ( 13,  17,  23)   # #0d1117  canvas
SURFACE     = ( 22,  27,  34)   # #161b22  elevated surface
TEXT_BRIGHT = (230, 237, 243)   # #e6edf3  primary text
TEXT_DIM    = (125, 133, 144)   # #7d8590  labels / captions
DIVIDER     = ( 48,  54,  61)   # #30363d  borders
ACCENT_GRN  = ( 57, 211,  83)   # #39d353  GitHub green (commits)
ACCENT_BLU  = ( 88, 166, 255)   # #58a6ff  GitHub blue

# GitHub dark-mode contribution grid colours (exact GitHub.com values)
GH_COLORS = [
    ( 22,  27,  34),   # #161b22 — 0 contributions
    ( 14,  68,  41),   # #0e4429 — 1–3
    (  0, 109,  50),   # #006d32 — 4–6
    ( 38, 166,  65),   # #26a641 — 7–9
    ( 57, 211,  83),   # #39d353 — 10+
]

# GitHub Linguist exact hex colours
LINGUIST_COLORS: dict[str, str] = {
    "Python":           "#3572A5",
    "Kotlin":           "#A97BFF",
    "JavaScript":       "#f1e05a",
    "TypeScript":       "#3178c6",
    "CSS":              "#563d7c",
    "SCSS":             "#c6538c",
    "Sass":             "#a53b70",
    "Rust":             "#dea584",
    "Go":               "#00ADD8",
    "Java":             "#b07219",
    "C++":              "#f34b7d",
    "C":                "#555555",
    "C#":               "#178600",
    "Swift":            "#F05138",
    "Ruby":             "#701516",
    "Shell":            "#89e051",
    "Dart":             "#00B4AB",
    "HTML":             "#e34c26",
    "Vue":              "#41b883",
    "PHP":              "#4F5D95",
    "Scala":            "#c22d40",
    "R":                "#198CE7",
    "Haskell":          "#5e5086",
    "Elixir":           "#6a40fd",
    "Lua":              "#000080",
    "Perl":             "#0298c3",
    "Jupyter Notebook": "#DA5B0B",
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ── Fonts: JetBrains Mono ──────────────────────────────────────
_FONTSOURCE = "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest"
FONT_URLS: dict[str, str] = {
    "JetBrainsMono-Regular.ttf": f"{_FONTSOURCE}/latin-400-normal.ttf",
    "JetBrainsMono-Bold.ttf":    f"{_FONTSOURCE}/latin-700-normal.ttf",
}

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
GH_TOKEN        = os.environ.get("GH_TOKEN", "")


# ══════════════════════════════════════════════════════════════
# Font helpers
# ══════════════════════════════════════════════════════════════

def ensure_fonts() -> None:
    FONT_DIR.mkdir(exist_ok=True)
    for filename, url in FONT_URLS.items():
        dest = FONT_DIR / filename
        if dest.exists():
            continue
        print(f"  ⬇  Downloading {filename} …")
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            print(f"     ✓ saved → {dest}")
        except Exception as exc:
            print(f"  ⚠  Could not download {filename}: {exc}")


def load_font(variant: str = "Regular", size: int = 16) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"JetBrainsMono-{variant}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


# ══════════════════════════════════════════════════════════════
# GitHub data fetching
# ══════════════════════════════════════════════════════════════

def _gh_headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h


def fetch_contributions() -> tuple[int, list[dict]]:
    today     = datetime.date.today()
    from_date = today - datetime.timedelta(days=364)
    gql = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          contributionCalendar { weeks { contributionDays { contributionCount date } } }
        }
      }
    }
    """
    resp = requests.post(
        "https://api.github.com/graphql",
        headers={**_gh_headers(), "Content-Type": "application/json"},
        json={"query": gql, "variables": {
            "login": GITHUB_USERNAME,
            "from":  f"{from_date.isoformat()}T00:00:00Z",
            "to":    f"{today.isoformat()}T23:59:59Z",
        }},
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GitHub GraphQL error: {body['errors']}")
    collection = body["data"]["user"]["contributionsCollection"]
    return collection["totalCommitContributions"], collection["contributionCalendar"]["weeks"]


def fetch_top_languages(max_repos: int = 25) -> list[tuple[str, float]]:
    h = _gh_headers()
    r = requests.get(
        f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
        headers=h, params={"per_page": 100, "type": "owner"}, timeout=20,
    )
    r.raise_for_status()
    all_repos = sorted(r.json(), key=lambda repo: repo.get("size", 0) or 0, reverse=True)[:max_repos]

    lang_bytes: dict[str, int] = {}
    for repo in all_repos:
        try:
            lr = requests.get(
                f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo['name']}/languages",
                headers=h, timeout=10,
            )
            if lr.ok:
                for lang, nbytes in lr.json().items():
                    lang_bytes[lang] = lang_bytes.get(lang, 0) + nbytes
        except Exception:
            pass

    total  = sum(lang_bytes.values()) or 1
    ranked = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)
    return [(name, round(b / total * 100, 1)) for name, b in ranked if b / total * 100 >= 0.3]


# ══════════════════════════════════════════════════════════════
# Drawing helpers
# ══════════════════════════════════════════════════════════════

def _rounded_rect(
    draw: ImageDraw.Draw,
    x0: int, y0: int, x1: int, y1: int,
    fill: tuple[int, int, int],
    radius: int = 4,
) -> None:
    draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=radius, fill=fill)


def _contribution_level(count: int) -> int:
    if count == 0: return 0
    if count <= 3: return 1
    if count <= 6: return 2
    if count <= 9: return 3
    return 4


def _draw_segmented_bar(
    img: Image.Image,
    x0: int, y0: int, bar_w: int, bar_h: int,
    segments: list[tuple[tuple[int, int, int], float]],
    radius: int = 4,
) -> None:
    """Draw a pill-shaped linguist language bar with RGBA compositing."""
    total_weight = sum(w for _, w in segments) or 1
    bar  = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    bd   = ImageDraw.Draw(bar)
    x    = 0
    for i, (color, weight) in enumerate(segments):
        seg_px = int(bar_w * weight / total_weight)
        if i == len(segments) - 1:
            seg_px = bar_w - x
        bd.rectangle([(x, 0), (x + seg_px, bar_h - 1)], fill=(*color, 255))
        x += seg_px
    mask = Image.new("L", (bar_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (bar_w - 1, bar_h - 1)], radius=radius, fill=255)
    bar.putalpha(mask)
    img.paste(bar, (x0, y0), bar)


# ══════════════════════════════════════════════════════════════
# Banner sections
# ══════════════════════════════════════════════════════════════

def _draw_center(
    img: Image.Image,
    draw: ImageDraw.Draw,
    total_commits: int,
    top_langs: list[tuple[str, float]],
) -> None:
    """
    Center section (x: CONTENT_LEFT_X → DIV2_X):

      // commits · 12mo
      1,284                 ← large green number

      ─────────────────
      // languages
      [████ linguist bar]
      ● Kotlin  24.4%  ● Python  22.8%  …
    """
    left_x  = CONTENT_LEFT_X
    right_x = DIV2_X - 40
    bar_w   = right_x - left_x

    top_y = CONTENT_TOP_Y

    lbl_font = load_font("Regular", FONT_LABEL_SIZE)
    num_font = load_font("Bold",    FONT_NUMBER_SIZE)
    leg_font = load_font("Regular", FONT_LEGEND_SIZE)

    # Section label + hero number
    draw.text((left_x, top_y), "// commits · 12mo", font=lbl_font, fill=TEXT_DIM)
    draw.text((left_x, top_y + FONT_LABEL_SIZE + 4), f"{total_commits:,}", font=num_font, fill=ACCENT_GRN)

    # Horizontal rule
    rule_y = top_y + FONT_LABEL_SIZE + 6 + FONT_NUMBER_SIZE + 14
    draw.line([(left_x, rule_y), (right_x, rule_y)], fill=DIVIDER, width=1)

    # Languages label
    lbl2_y = rule_y + 12
    draw.text((left_x, lbl2_y), "// languages", font=lbl_font, fill=TEXT_DIM)

    # Segmented linguist bar
    bar_y = lbl2_y + FONT_LABEL_SIZE + 6
    bar_h = 8
    segments = [
        (_hex_to_rgb(LINGUIST_COLORS.get(lang, "#555555")), pct)
        for lang, pct in top_langs
    ]
    _draw_segmented_bar(img, left_x, bar_y, bar_w, bar_h, segments, radius=bar_h // 2)

    # Variable-column dot legend (layout defined by LEGEND_LAYOUT).
    # Each row can have a different number of columns so lower rows
    # use wider slots, reducing visual clutter near the profile-picture zone.
    legend_y = bar_y + bar_h + 12
    row_h    = FONT_LEGEND_SIZE + 6
    dot_r    = 4
    lang_idx = 0

    for row_idx, cols_in_row in enumerate(LEGEND_LAYOUT):
        if lang_idx >= len(top_langs):
            break
        item_y = legend_y + row_idx * row_h
        if item_y + FONT_LEGEND_SIZE > BANNER_H - 10:
            break
        col_w = bar_w // cols_in_row
        for col_idx in range(cols_in_row):
            if lang_idx >= len(top_langs):
                break
            lang, pct = top_langs[lang_idx]
            item_x = left_x + col_idx * col_w
            color  = _hex_to_rgb(LINGUIST_COLORS.get(lang, "#555555"))
            draw.ellipse(
                [(item_x, item_y + 5), (item_x + dot_r * 2, item_y + 5 + dot_r * 2)],
                fill=color,
            )
            name_str = (lang[:10] + "…") if len(lang) > 11 else lang
            label    = f" {name_str}  {pct:.1f}%"
            draw.text((item_x + dot_r * 2, item_y), label, font=leg_font, fill=TEXT_BRIGHT)
            lang_idx += 1


def _draw_right(draw: ImageDraw.Draw, weeks: list[dict]) -> None:
    """Right section: 52-week contribution grid."""
    STEP   = GRID_CELL_SIZE + GRID_GAP
    grid_h = 7 * STEP - GRID_GAP

    pad     = 48
    grid_x0 = DIV2_X + pad

    hdr_font = load_font("Regular", FONT_GRID_HDR_SIZE)
    cap_font = load_font("Regular", FONT_LABEL_SIZE)

    # Vertically centre within banner height
    BLOCK_H = FONT_GRID_HDR_SIZE + 10 + grid_h + 10 + FONT_LABEL_SIZE
    grid_y0 = (BANNER_H - BLOCK_H) // 2 + FONT_GRID_HDR_SIZE + 10

    draw.text(
        (grid_x0, grid_y0 - FONT_GRID_HDR_SIZE - 10),
        "CONTRIBUTIONS  ·  PAST 12 MONTHS",
        font=hdr_font,
        fill=TEXT_DIM,
    )

    for week_idx, week in enumerate(weeks):
        for day_idx, day in enumerate(week["contributionDays"]):
            lvl   = _contribution_level(day["contributionCount"])
            color = GH_COLORS[lvl]
            x     = grid_x0 + week_idx * STEP
            y     = grid_y0 + day_idx * STEP
            _rounded_rect(draw, x, y, x + GRID_CELL_SIZE, y + GRID_CELL_SIZE, fill=color, radius=2)

    today = datetime.date.today()
    draw.text(
        (grid_x0, grid_y0 + grid_h + 10),
        f"Updated: {today.strftime('%b %d, %Y')}",
        font=cap_font,
        fill=TEXT_DIM,
    )


def draw_banner(
    total_commits: int,
    top_langs: list[tuple[str, float]],
    weeks: list[dict],
) -> Image.Image:
    """Compose the full 1584×396 banner and return the Pillow Image."""
    img  = Image.new("RGB", (BANNER_W, BANNER_H), BG)
    draw = ImageDraw.Draw(img)

    pad_y = 40
    draw.line([(DIV2_X, pad_y), (DIV2_X, BANNER_H - pad_y)], fill=DIVIDER, width=1)

    _draw_center(img, draw, total_commits, top_langs)
    _draw_right(draw, weeks)

    return img


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

def main() -> None:
    if not GITHUB_USERNAME:
        sys.exit("✗  GITHUB_USERNAME env var is not set.")

    ensure_fonts()

    print(f"\n📊  Fetching GitHub data for @{GITHUB_USERNAME} …")
    total_commits, weeks = fetch_contributions()
    top_langs            = fetch_top_languages()

    print(f"    Commits (12 mo):           {total_commits:,}")
    print(f"    Top languages:             {', '.join(n for n, _ in top_langs)}")
    print(f"    Contribution weeks loaded: {len(weeks)}")

    print("\n🖼   Drawing banner …")
    banner = draw_banner(total_commits, top_langs, weeks)
    banner.save(OUTPUT_PATH, "PNG", optimize=True)
    print(f"    ✓ Saved → {OUTPUT_PATH}")
    print("\n✅  Done.")


if __name__ == "__main__":
    main()
