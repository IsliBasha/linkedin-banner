#!/usr/bin/env python3
"""
Standalone reproduction — NOT pytest-collected (needs a live logged-in Chrome
via ./launch_chrome_for_upload.sh; can't run unattended in CI).

Isolates why upload_ci.py's cookie-injected session renders LinkedIn's
logged-out view (2026-07-15 Tailscale-routed run 29402609598) even though
upload_local.py's default path — reusing the real browser context — works.
The one deliberate difference below is the independent variable: cookies
alone (what upload_ci.py injects today) vs. full storage_state, which also
carries localStorage. Both scenarios run on this machine, same IP, same
Chrome install — network is held constant to isolate the browser-context
variable from the IP variable already fixed by Tailscale.

RESULT (2026-07-15, run against the live account): Scenario A failed as
expected (soft "sign in to continue" modal — matches CI). Scenario B also
failed, and *worse*: a full "Join LinkedIn" signup wall rather than a
personalized soft nudge. This falsifies the localStorage theory as stated.

Two explanations remain live, and this script's back-to-back design can't
distinguish them:
  1. Order effect — two rapid automated session-continuation attempts on the
     same account may itself escalate LinkedIn's risk response, independent
     of what each context contained.
  2. LinkedIn's device-trust signal isn't (only) localStorage — likely
     canvas/WebGL/audio fingerprint or TLS-level signals tied to a specific
     browser binary + real interaction history, which cannot be captured and
     replayed into a fresh context by any export script. If so, an isolated
     Playwright context can probably never pass this check regardless of
     what state it's seeded with — only a persistently-used real profile
     (what upload_local.py's default, non-CI path already relies on) can.

CAUTION: do not re-run this against the live account casually. Two
back-to-back attempts already escalated the challenge tier once; repeated
automated probing risks tripping a harder verification/restriction on the
real account. If re-running, space attempts out and treat any escalation as
a stop signal, not noise to iterate past.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_DIR = Path(__file__).parent.resolve()
CDP_PORT = 9222
PROFILE_URL = "https://www.linkedin.com/in/islibasha/"
EDIT_BTN_SELECTOR = "button[aria-label*='background' i], button[aria-label*='cover' i]"


def _check_authenticated(ctx, label: str) -> bool:
    page = ctx.new_page()
    page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=45_000)
    try:
        page.wait_for_selector(EDIT_BTN_SELECTOR, state="visible", timeout=15_000)
        authed = True
    except Exception:
        authed = False
    page.screenshot(path=str(REPO_DIR / f"debug_repro_{label}.png"))
    page.close()
    return authed


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        real_ctx = browser.contexts[0]

        # ── Scenario A: exactly what upload_ci.py does today ──────────────────
        cookies_only = real_ctx.cookies()
        ctx_a = browser.new_context()
        ctx_a.add_cookies(cookies_only)
        authed_a = _check_authenticated(ctx_a, "cookies_only")
        ctx_a.close()
        print(
            f"[A: cookies only]            authenticated = {authed_a}  "
            f"({'RED (reproduces CI bug)' if not authed_a else 'unexpectedly GREEN'})"
        )

        # ── Scenario B: cookies + full storage_state (adds localStorage) ─────
        state = real_ctx.storage_state()
        ctx_b = browser.new_context(storage_state=state)
        authed_b = _check_authenticated(ctx_b, "storage_state")
        ctx_b.close()
        print(
            f"[B: cookies + localStorage]  authenticated = {authed_b}  "
            f"({'GREEN (fix confirmed)' if authed_b else 'still fails — theory wrong'})"
        )

        browser.close()

    if authed_a:
        sys.exit("✗  Scenario A did not reproduce the bug locally — investigate further before trusting Scenario B.")
    if not authed_b:
        sys.exit("✗  Scenario B still failed — localStorage alone isn't the fix; needs more investigation.")

    print("\n✓  Theory confirmed: localStorage (not IP, not cookie freshness) is the missing signal.")


if __name__ == "__main__":
    main()
