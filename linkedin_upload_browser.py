#!/usr/bin/env python3
"""
LinkedIn banner uploader — Playwright browser automation.
Logs into LinkedIn and sets the profile background cover photo.

Usage:
    export LINKEDIN_EMAIL="you@email.com"
    export LINKEDIN_PASSWORD="yourpassword"
    python3 linkedin_upload_browser.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

LINKEDIN_EMAIL    = os.environ.get("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.environ.get("LINKEDIN_PASSWORD", "")
BANNER_PATH       = str(Path("banner.png").resolve())


def login(page) -> None:
    print("  → Navigating to LinkedIn login…")
    page.goto("https://www.linkedin.com/login", wait_until="networkidle", timeout=30000)
    page.screenshot(path="debug_login_page.png")

    # Try multiple selector strategies for the email field
    email_selectors = [
        'input[name="session_key"]',
        'input[id="username"]',
        'input[autocomplete="username"]',
        'input[type="email"]',
    ]
    filled = False
    for sel in email_selectors:
        try:
            page.wait_for_selector(sel, timeout=5000)
            page.fill(sel, LINKEDIN_EMAIL)
            filled = True
            print(f"     ✓ Email field found via: {sel}")
            break
        except Exception:
            continue

    if not filled:
        page.screenshot(path="debug_login.png")
        sys.exit(
            "✗  Could not find email input on login page.\n"
            "   Screenshot saved to debug_login.png"
        )

    # Password
    pass_selectors = [
        'input[name="session_password"]',
        'input[id="password"]',
        'input[type="password"]',
    ]
    for sel in pass_selectors:
        try:
            page.fill(sel, LINKEDIN_PASSWORD)
            break
        except Exception:
            continue

    page.click('button[type="submit"]')
    page.wait_for_timeout(3000)

    try:
        page.wait_for_url(
            lambda url: "/login" not in url and "/checkpoint" not in url,
            timeout=20000,
        )
    except PlaywrightTimeout:
        page.screenshot(path="debug_login.png")
        sys.exit(
            "✗  Login failed or requires manual verification.\n"
            "   Check LINKEDIN_EMAIL / LINKEDIN_PASSWORD secrets,\n"
            "   or complete a manual login once to clear the checkpoint."
        )
    print("  ✓ Logged in")


def set_background(page) -> None:
    print("  → Loading profile page…")
    page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=20000)

    # ── Click the background edit button ────────────────────────
    print("  → Opening background photo editor…")
    edit_selectors = [
        "button[aria-label*='background' i]",
        "button[aria-label*='cover' i]",
        "button[aria-label*='Edit background' i]",
        ".profile-topcard-background-image-upload-btn",
        "button:has-text('Edit background')",
    ]
    clicked = False
    for sel in edit_selectors:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=3000)
            btn.click()
            clicked = True
            print(f"     ✓ Found edit button via: {sel}")
            break
        except Exception:
            continue

    if not clicked:
        page.screenshot(path="debug_profile.png")
        sys.exit(
            "✗  Could not find the background photo edit button.\n"
            "   Screenshot saved to debug_profile.png"
        )

    # ── Select the file ──────────────────────────────────────────
    print("  → Uploading banner.png…")
    upload_selectors = [
        "text=Upload photo",
        "text=Upload a photo",
        "text=Change photo",
        "li:has-text('Upload')",
    ]
    uploaded = False
    for sel in upload_selectors:
        try:
            with page.expect_file_chooser(timeout=5000) as fc_info:
                page.locator(sel).first.click()
            fc_info.value.set_files(BANNER_PATH)
            uploaded = True
            print("     ✓ File selected")
            break
        except Exception:
            continue

    if not uploaded:
        # Fallback: direct file input
        try:
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(BANNER_PATH)
            print("     ✓ File set via direct input")
        except Exception:
            page.screenshot(path="debug_upload.png")
            sys.exit(
                "✗  Could not trigger file upload dialog.\n"
                "   Screenshot saved to debug_upload.png"
            )

    # ── Apply / Save ─────────────────────────────────────────────
    print("  → Saving…")
    save_selectors = [
        "button:has-text('Apply')",
        "button:has-text('Save')",
        "button[aria-label*='Save' i]",
        "button[aria-label*='Apply' i]",
    ]
    for sel in save_selectors:
        try:
            btn = page.locator(sel).last
            btn.wait_for(state="visible", timeout=8000)
            btn.click()
            page.wait_for_load_state("networkidle", timeout=20000)
            print("  ✓ Profile background updated!")
            return
        except Exception:
            continue

    page.screenshot(path="debug_apply.png")
    sys.exit(
        "✗  Could not find Apply/Save button.\n"
        "   Screenshot saved to debug_apply.png"
    )


def main() -> None:
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        sys.exit("✗  LINKEDIN_EMAIL and LINKEDIN_PASSWORD env vars must be set.")

    if not Path(BANNER_PATH).exists():
        sys.exit(f"✗  Banner not found: {BANNER_PATH}")

    print("\n🤖  LinkedIn Browser Upload…")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            login(page)
            set_background(page)
        except SystemExit:
            raise
        except Exception as e:
            page.screenshot(path="debug_error.png")
            sys.exit(f"✗  Unexpected error: {e}")
        finally:
            browser.close()

    print("\n✅  Done.")


if __name__ == "__main__":
    main()
