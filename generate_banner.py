#!/usr/bin/env python3
"""
LinkedIn Banner Auto-Generator
================================
Generates a 1584×396 cover image from live GitHub data and
uploads it as a LinkedIn profile background photo.

Usage (local):
    export LINKEDIN_ACCESS_TOKEN="AQV..."
    export LINKEDIN_PERSON_URN="urn:li:person:XXXXX"
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

# Canvas size (LinkedIn cover photo)
BANNER_W, BANNER_H = 1584, 396

# Layout — single divider between stats and contribution grid.
# Left ~200 px is intentionally clear for the LinkedIn profile-picture circle
# (≈140 px Ø, centred at the bottom-left corner of the cover photo).
DIV2_X = 860   # stats | contribution grid

# ── Colour palette — terminal dark (GitHub dark mode) ─────────
BG          = ( 13,  17,  23)   # #0d1117  canvas
SURFACE     = ( 22,  27,  34)   # #161b22  elevated surface
TEXT_BRIGHT = (230, 237, 243)   # #e6edf3  primary text
TEXT_DIM    = (125, 133, 144)   # #7d8590  labels / captions
DIVIDER     = ( 48,  54,  61)   # #30363d  borders
ACCENT_GRN  = ( 57, 211,  83)   # #39d353  GitHub green (commits)
ACCENT_BLU  = ( 88, 166, 255)   # #58a6ff  GitHub blue

# GitHub dark-mode contribution grid (exact GitHub.com values)
GH_COLORS = [
    ( 22,  27,  34),   # #161b22 — 0 contributions (surface)
    ( 14,  68,  41),   # #0e4429 — 1–3
    (  0, 109,  50),   # #006d32 — 4–6
    ( 38, 166,  65),   # #26a641 — 7–9
    ( 57, 211,  83),   # #39d353 — 10+
]

# GitHub Linguist exact hex colours (matches github.com language bars)
LINGUIST_COLORS: dict[str, str] = {
    "Python":             "#3572A5",
    "Kotlin":             "#A97BFF",
    "JavaScript":         "#f1e05a",
    "TypeScript":         "#3178c6",
    "CSS":                "#563d7c",
    "SCSS":               "#c6538c",
    "Sass":               "#a53b70",
    "Rust":               "#dea584",
    "Go":                 "#00ADD8",
    "Java":               "#b07219",
    "C++":                "#f34b7d",
    "C":                  "#555555",
    "C#":                 "#178600",
    "Swift":              "#F05138",
    "Ruby":               "#701516",
    "Shell":              "#89e051",
    "Dart":               "#00B4AB",
    "HTML":               "#e34c26",
    "Vue":                "#41b883",
    "PHP":                "#4F5D95",
    "Scala":              "#c22d40",
    "R":                  "#198CE7",
    "Haskell":            "#5e5086",
    "Elixir":             "#6a40fd",
    "Lua":                "#000080",
    "Perl":               "#0298c3",
    "Jupyter Notebook":   "#DA5B0B",
}

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' hex string to (r, g, b) integer tuple."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

# ── Font: JetBrains Mono (monospace — terminal aesthetic) ─────
_FONTSOURCE = "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest"
FONT_URLS: dict[str, str] = {
    "JetBrainsMono-Regular.ttf": f"{_FONTSOURCE}/latin-400-normal.ttf",
    "JetBrainsMono-Bold.ttf":    f"{_FONTSOURCE}/latin-700-normal.ttf",
}

# ── Secrets from environment ───────────────────────────────────
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_URN   = os.environ.get("LINKEDIN_PERSON_URN", "")   # urn:li:person:XXXX
GITHUB_USERNAME       = os.environ.get("GITHUB_USERNAME", "")
GH_TOKEN              = os.environ.get("GH_TOKEN", "")


# ══════════════════════════════════════════════════════════════
# Font helpers
# ══════════════════════════════════════════════════════════════

def ensure_fonts() -> None:
    """Download Inter TTF files if they are not already present locally."""
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
            print("     Pillow's built-in font will be used as fallback.")


def load_font(variant: str = "Regular", size: int = 16) -> ImageFont.FreeTypeFont:
    """Load a JetBrains Mono variant; fall back to Pillow's default."""
    path = FONT_DIR / f"JetBrainsMono-{variant}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        # Pillow ≥ 10: load_default accepts a size kwarg
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
    """
    Query the GitHub GraphQL API for the past 365 days.

    Returns:
        total_commits  – totalCommitContributions (commits only)
        weeks          – list of week dicts each containing ``contributionDays``
    """
    today     = datetime.date.today()
    from_date = today - datetime.timedelta(days=364)

    gql = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    resp = requests.post(
        "https://api.github.com/graphql",
        headers={**_gh_headers(), "Content-Type": "application/json"},
        json={
            "query": gql,
            "variables": {
                "login": GITHUB_USERNAME,
                "from":  f"{from_date.isoformat()}T00:00:00Z",
                "to":    f"{today.isoformat()}T23:59:59Z",
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GitHub GraphQL error: {body['errors']}")

    collection = body["data"]["user"]["contributionsCollection"]
    total = collection["totalCommitContributions"]
    weeks = collection["contributionCalendar"]["weeks"]
    return total, weeks


def fetch_top_languages(max_repos: int = 25) -> list[tuple[str, float]]:
    """
    Fetch accurate byte-level language breakdown using /repos/{owner}/{repo}/languages.

    Queries each repo individually (up to max_repos largest repos) to get the
    actual byte counts per language — matching what github-stats SVG cards show.
    All languages with >= 0.3% share are returned (no hard top-N cap).

    Returns:
        List of (language_name, percentage) sorted descending.
    """
    h = _gh_headers()

    # Get all owned repos, sorted by size descending
    r = requests.get(
        f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
        headers=h,
        params={"per_page": 100, "type": "owner"},
        timeout=20,
    )
    r.raise_for_status()
    all_repos = sorted(
        r.json(),
        key=lambda repo: repo.get("size", 0) or 0,
        reverse=True,
    )[:max_repos]

    lang_bytes: dict[str, int] = {}
    for repo in all_repos:
        try:
            lr = requests.get(
                f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo['name']}/languages",
                headers=h,
                timeout=10,
            )
            if lr.ok:
                for lang, nbytes in lr.json().items():
                    lang_bytes[lang] = lang_bytes.get(lang, 0) + nbytes
        except Exception:
            pass   # skip unreachable repos silently

    total  = sum(lang_bytes.values()) or 1
    ranked = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)
    return [
        (name, round(b / total * 100, 1))
        for name, b in ranked
        if b / total * 100 >= 0.3     # drop languages with < 0.3% share
    ]


# ══════════════════════════════════════════════════════════════
# Drawing helpers
# ══════════════════════════════════════════════════════════════

def _rounded_rect(
    draw: ImageDraw.Draw,
    x0: int, y0: int, x1: int, y1: int,
    fill: tuple[int, int, int],
    radius: int = 4,
) -> None:
    """Draw a filled rectangle with rounded corners (Pillow ≥ 8.2)."""
    draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=radius, fill=fill)


def _contribution_level(count: int) -> int:
    """Map contribution count → 0–4 heat level."""
    if count == 0: return 0
    if count <= 3: return 1
    if count <= 6: return 2
    if count <= 9: return 3
    return 4


# ══════════════════════════════════════════════════════════════
# Banner sections
# ══════════════════════════════════════════════════════════════


def _draw_segmented_bar(
    img: Image.Image,
    x0: int, y0: int, bar_w: int, bar_h: int,
    segments: list[tuple[tuple[int, int, int], float]],
    radius: int = 4,
) -> None:
    """
    Draw a horizontally segmented colour bar with properly rounded ends.

    Uses RGBA compositing + a rounded-rectangle mask so internal segment
    borders stay sharp while the overall pill shape is correctly rounded —
    identical to GitHub's linguist language bar.
    """
    total_weight = sum(w for _, w in segments) or 1

    # 1. Paint all segments onto a transparent RGBA canvas
    bar = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    bd  = ImageDraw.Draw(bar)
    x   = 0
    for i, (color, weight) in enumerate(segments):
        seg_px = int(bar_w * weight / total_weight)
        if i == len(segments) - 1:
            seg_px = bar_w - x          # fill any rounding remainder
        bd.rectangle([(x, 0), (x + seg_px, bar_h - 1)], fill=(*color, 255))
        x += seg_px

    # 2. Rounded rectangle mask (white = keep, black = discard)
    mask = Image.new("L", (bar_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (bar_w - 1, bar_h - 1)], radius=radius, fill=255
    )
    bar.putalpha(mask)

    # 3. Composite onto the main (RGB) image
    img.paste(bar, (x0, y0), bar)


def _draw_center(
    img: Image.Image,
    draw: ImageDraw.Draw,
    total_commits: int,
    top_langs: list[tuple[str, float]],
) -> None:
    """
    Center section — terminal dark style:

      // commits · 12mo
      1,284                          ← bright green number

      ──────────────────────────────
      // languages
      [██Kotlin██Python██JS██TS█CSS]  ← GitHub linguist bar
      ● Kotlin  24.4%  ● Python  22.8%  ● JS  21.2%
      ● TS      14.3%  ● CSS      5.6%  …
    """
    left_x  = 200
    right_x = DIV2_X - 1 - 40
    bar_w   = right_x - left_x

    cmt_font  = load_font("Regular", 11)
    num_font  = load_font("Bold",    34)
    lang_font = load_font("Regular", 11)

    top_y = 68

    # Comment-style label + big commit number in GitHub green
    draw.text((left_x, top_y), "// commits · 12mo", font=cmt_font, fill=TEXT_DIM)
    draw.text((left_x, top_y + 16), f"{total_commits:,}", font=num_font, fill=ACCENT_GRN)

    # Thin horizontal rule separator
    rule_y = top_y + 66
    draw.line([(left_x, rule_y), (right_x, rule_y)], fill=DIVIDER, width=1)

    # Languages header
    lbl_y = rule_y + 14
    draw.text((left_x, lbl_y), "// languages", font=cmt_font, fill=TEXT_DIM)

    # ── Segmented linguist bar ────────────────────────────────
    bar_y = lbl_y + 18
    bar_h = 6
    segments = [
        (_hex_to_rgb(LINGUIST_COLORS.get(lang, "#555555")), pct)
        for lang, pct in top_langs
    ]
    _draw_segmented_bar(img, left_x, bar_y, bar_w, bar_h, segments, radius=bar_h // 2)

    # ── 3-column dot legend (all languages) ──────────────────
    legend_y = bar_y + bar_h + 14
    COLS  = 3
    col_w = bar_w // COLS
    row_h = 18
    dot_r = 3

    for i, (lang, pct) in enumerate(top_langs):
        col    = i % COLS
        row    = i // COLS
        item_x = left_x + col * col_w
        item_y = legend_y + row * row_h
        color  = _hex_to_rgb(LINGUIST_COLORS.get(lang, "#555555"))

        # Coloured dot (vertically centred with text)
        draw.ellipse(
            [(item_x, item_y + 4), (item_x + dot_r * 2, item_y + 4 + dot_r * 2)],
            fill=color,
        )

        # "name  xx.x%" — truncate long language names to fit 3-column grid
        name_str = (lang[:11] + "…") if len(lang) > 12 else lang
        label    = f" {name_str}  {pct:.1f}%"
        draw.text((item_x + dot_r * 2, item_y), label, font=lang_font, fill=TEXT_BRIGHT)


def _draw_right(draw: ImageDraw.Draw, weeks: list[dict]) -> None:
    """Right section: 52-week × 7-day contribution grid with timestamp caption."""
    pad     = 44
    grid_x0 = DIV2_X + 1 + pad

    # Cell geometry
    CELL = 9
    GAP  = 2
    STEP = CELL + GAP

    grid_h  = 7 * STEP - GAP          # 75 px tall

    # Vertically centre the grid inside the profile-pic safe zone (y=0–330).
    # safe_zone_h=330, content_block=grid_h+32(header)+22(caption) ≈ 129 px
    # → top = (330 - 129) / 2 = 100 → grid starts at 100 + 32 = 132
    SAFE_H  = 330
    BLOCK_H = 22 + 8 + grid_h + 22   # header + gap + cells + caption
    grid_y0 = (SAFE_H - BLOCK_H) // 2 + 22 + 8

    # Section header
    hdr_font = load_font("Regular", 11)
    draw.text(
        (grid_x0, grid_y0 - 22),
        "CONTRIBUTIONS  ·  PAST 12 MONTHS",
        font=hdr_font,
        fill=TEXT_DIM,
    )

    # Grid cells (week-major, day-minor)
    for week_idx, week in enumerate(weeks):
        for day_idx, day in enumerate(week["contributionDays"]):
            count = day["contributionCount"]
            lvl   = _contribution_level(count)
            color = GH_COLORS[lvl]
            x     = grid_x0 + week_idx * STEP
            y     = grid_y0 + day_idx * STEP
            _rounded_rect(draw, x, y, x + CELL, y + CELL, fill=color, radius=2)

    # "Updated:" caption below the grid
    cap_font = load_font("Regular", 11)
    today    = datetime.date.today()
    caption  = f"Updated: {today.strftime('%b %d, %Y')}"
    draw.text(
        (grid_x0, grid_y0 + grid_h + 10),
        caption,
        font=cap_font,
        fill=TEXT_DIM,
    )


def draw_banner(
    total_commits: int,
    top_langs: list[tuple[str, float]],
    weeks: list[dict],
) -> Image.Image:
    """Compose the full 1584×396 banner and return the Pillow Image object."""
    img  = Image.new("RGB", (BANNER_W, BANNER_H), BG)
    draw = ImageDraw.Draw(img)

    # Single vertical divider between stats and contribution grid
    pad_y = 44
    draw.line(
        [(DIV2_X, pad_y), (DIV2_X, BANNER_H - pad_y)],
        fill=DIVIDER,
        width=1,
    )

    _draw_center(img, draw, total_commits, top_langs)
    _draw_right(draw, weeks)

    return img


# ══════════════════════════════════════════════════════════════
# LinkedIn upload  (3-step flow)
# ══════════════════════════════════════════════════════════════

_LI_VERSION = "202412"


def _li_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization":             f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "LinkedIn-Version":          _LI_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if extra:
        h.update(extra)
    return h


def _register_upload() -> tuple[str, str]:
    """
    Step 1 — ask LinkedIn for a pre-signed upload URL.

    Endpoint:  POST /v2/assets?action=registerUpload  (v2 Assets API)
    Required scope: w_member_social

    Returns:
        upload_url – where to PUT the binary
        image_urn  – asset URN to set on the profile
    """
    resp = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers={
            "Authorization":             f"Bearer {LINKEDIN_ACCESS_TOKEN}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type":              "application/json",
        },
        json={
            "registerUploadRequest": {
                "recipes":  ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner":    LINKEDIN_PERSON_URN,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier":       "urn:li:userGeneratedContent",
                    }
                ],
            }
        },
        timeout=20,
    )
    resp.raise_for_status()
    val      = resp.json()["value"]
    upload_url = (
        val["uploadMechanism"]
           ["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]
           ["uploadUrl"]
    )
    asset_urn = val["asset"]
    return upload_url, asset_urn


def _put_image_binary(upload_url: str, image_path: str) -> None:
    """Step 2 — binary PUT of the PNG to the pre-signed S3-style URL."""
    with open(image_path, "rb") as fh:
        data = fh.read()
    resp = requests.put(
        upload_url,
        headers={"Content-Type": "image/png"},
        data=data,
        timeout=60,
    )
    resp.raise_for_status()


def _patch_profile_background(image_urn: str) -> None:
    """
    Step 3 — PATCH /v2/me to set the profile background cover image.

    Uses the backgroundPicture field on the v2 Member Profile API.
    Requires w_member_social scope.
    """
    payload = {
        "patch": {
            "$set": {
                "backgroundPicture": {
                    "com.linkedin.digitalmedia.mediaartifact.StillImage": {
                        "storageSize":              {"width": 1584, "height": 396},
                        "storageAspectRatio":       {"formatted": "4.00:1"},
                        "mediaAvailabilityStatus":  "READY",
                        "originalImage":            image_urn,
                    }
                }
            }
        }
    }

    resp = requests.patch(
        "https://api.linkedin.com/v2/me",
        headers={
            "Authorization":             f"Bearer {LINKEDIN_ACCESS_TOKEN}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type":              "application/json",
        },
        json=payload,
        timeout=20,
    )

    if not resp.ok:
        print(
            f"\n  ⚠  Profile PATCH returned HTTP {resp.status_code}."
            f"\n     Body: {resp.text[:300]}"
            f"\n     The image was uploaded as: {image_urn}"
            f"\n     ➜  Set it manually in LinkedIn: Me → View Profile → "
            f"Edit background photo → upload banner.png"
        )
        return

    print(f"  ✓  Profile background updated  ({image_urn})")


def upload_to_linkedin(image_path: str) -> None:
    """Orchestrate the full 3-step LinkedIn cover photo upload."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        print(
            "  ⚠  LINKEDIN_ACCESS_TOKEN / LINKEDIN_PERSON_URN are not set — "
            "skipping LinkedIn upload.  Banner saved to banner.png."
        )
        return

    print("  ↑  Step 1/3 — Registering upload with LinkedIn …")
    upload_url, image_urn = _register_upload()

    print("  ↑  Step 2/3 — Uploading image binary …")
    _put_image_binary(upload_url, image_path)
    print(f"     ✓ Binary uploaded  (URN: {image_urn})")

    print("  ↑  Step 3/3 — Patching profile background …")
    _patch_profile_background(image_urn)


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

def main() -> None:
    if not GITHUB_USERNAME:
        sys.exit("✗  GITHUB_USERNAME env var is not set.")

    # 1. Ensure fonts are available
    ensure_fonts()

    # 2. Fetch GitHub data
    print(f"\n📊  Fetching GitHub data for @{GITHUB_USERNAME} …")
    total_commits, weeks = fetch_contributions()
    top_langs            = fetch_top_languages()

    print(f"    Commits (12 mo):           {total_commits:,}")
    print(f"    Top languages:             {', '.join(n for n, _ in top_langs)}")
    print(f"    Contribution weeks loaded: {len(weeks)}")

    # 3. Draw and save
    print("\n🖼   Drawing banner …")
    banner = draw_banner(total_commits, top_langs, weeks)
    banner.save(OUTPUT_PATH, "PNG", optimize=True)
    print(f"    ✓ Saved → {OUTPUT_PATH}")

    # 4. Upload to LinkedIn
    print("\n🔗  LinkedIn upload …")
    upload_to_linkedin(OUTPUT_PATH)

    print("\n✅  Done.")


if __name__ == "__main__":
    main()
