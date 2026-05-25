#!/usr/bin/env python3
"""
Local LinkedIn banner uploader.
Pulls the latest banner.png from git and uploads it to LinkedIn
using your real Chrome profile — no login needed, no bot detection.

Run daily via cron:
    0 7 * * * cd /home/lugat/Documents/linkedin-banner && python3 upload_local.py >> ~/.linkedin_banner.log 2>&1
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_DIR       = Path(__file__).parent.resolve()
BANNER_PATH    = REPO_DIR / "banner.png"
CHROME_PROFILE = Path.home() / ".config" / "google-chrome"


def pull_latest() -> None:
    print("  → Pulling latest banner from GitHub…")
    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"✗  git pull failed:\n{result.stderr}")
    print(f"     {result.stdout.strip() or 'Already up to date'}")


def upload_to_linkedin() -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    print("  → Launching Chrome with your profile…")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            executable_path="/usr/bin/google-chrome",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.new_page()

        try:
            print("  → Loading LinkedIn profile…")
            page.goto("https://www.linkedin.com/in/me/", wait_until="load", timeout=30000)
            page.wait_for_timeout(2000)

            if "/login" in page.url or "/authwall" in page.url:
                sys.exit(
                    "✗  Not logged in to LinkedIn in Chrome.\n"
                    "   Open Chrome, log in to LinkedIn, then re-run."
                )
            print("  ✓ Session valid")

            # Click the background edit button
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
                    break
                except Exception:
                    continue

            if not clicked:
                page.screenshot(path=str(REPO_DIR / "debug_profile.png"))
                sys.exit("✗  Could not find background edit button. Screenshot saved.")

            # Upload file
            print("  → Uploading banner.png…")
            uploaded = False
            for sel in ["text=Upload photo", "text=Upload a photo", "text=Change photo", "li:has-text('Upload')"]:
                try:
                    with page.expect_file_chooser(timeout=5000) as fc_info:
                        page.locator(sel).first.click()
                    fc_info.value.set_files(str(BANNER_PATH))
                    uploaded = True
                    break
                except Exception:
                    continue

            if not uploaded:
                try:
                    page.locator("input[type='file']").first.set_input_files(str(BANNER_PATH))
                except Exception:
                    page.screenshot(path=str(REPO_DIR / "debug_upload.png"))
                    sys.exit("✗  Could not upload file. Screenshot saved.")

            # Save
            print("  → Applying…")
            for sel in ["button:has-text('Apply')", "button:has-text('Save')"]:
                try:
                    btn = page.locator(sel).last
                    btn.wait_for(state="visible", timeout=8000)
                    btn.click()
                    page.wait_for_timeout(3000)
                    print("  ✓ Profile background updated!")
                    return
                except Exception:
                    continue

            page.screenshot(path=str(REPO_DIR / "debug_apply.png"))
            sys.exit("✗  Could not find Apply/Save button. Screenshot saved.")

        except PlaywrightTimeout as e:
            page.screenshot(path=str(REPO_DIR / "debug_error.png"))
            sys.exit(f"✗  Timeout: {e}")
        finally:
            context.close()


def main() -> None:
    print(f"\n🖼   LinkedIn Banner Uploader — {__import__('datetime').date.today()}")

    if not BANNER_PATH.exists():
        sys.exit(f"✗  banner.png not found at {BANNER_PATH}")

    pull_latest()
    upload_to_linkedin()
    print("\n✅  Done.")


if __name__ == "__main__":
    main()
