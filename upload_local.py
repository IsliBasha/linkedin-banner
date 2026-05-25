#!/usr/bin/env python3
"""
Local LinkedIn banner uploader — CDP direct approach.

Connects to the running Chrome (started by launch_chrome_for_upload.sh) via
Chrome DevTools Protocol (CDP), opens a new tab IN that real browser, and
performs the upload entirely inside the live authenticated session.

No headless browser.  No cookie extraction.  No bot detection.

WORKFLOW
  1. Run:  ./launch_chrome_for_upload.sh   (once; keep Chrome open)
  2. Wait until LinkedIn loads in Chrome (~5 s)
  3. Run:  python3 upload_local.py

CRON (daily at 07:00 — Chrome opened automatically)
  0 7 * * * cd /home/lugat/Documents/linkedin-banner && \\
            ./launch_chrome_for_upload.sh && sleep 10 && \\
            .venv/bin/python3 upload_local.py >> ~/.linkedin_banner.log 2>&1
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_DIR    = Path(__file__).parent.resolve()
BANNER_PATH = REPO_DIR / "banner.png"
PROFILE_URL = "https://www.linkedin.com/in/islibasha/"
CDP_PORT    = 9222


# ─────────────────────────────────────────────────────────────────────────────

def pull_latest() -> None:
    print("  → Pulling latest banner from GitHub…")
    result = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=REPO_DIR, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"✗  git pull failed:\n{result.stderr}")
    print(f"     {result.stdout.strip() or 'Already up to date.'}")


def wait_for_cdp(timeout: int = 30) -> None:
    """Block until Chrome's CDP port is reachable (or time out)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"http://localhost:{CDP_PORT}/json/version", timeout=2
            )
            return          # port is up
        except Exception:
            time.sleep(1)
    sys.exit(
        f"✗  Chrome debug port {CDP_PORT} not reachable after {timeout} s.\n"
        f"   Run:  ./launch_chrome_for_upload.sh\n"
        f"   Then retry."
    )


# ─────────────────────────────────────────────────────────────────────────────

def upload_banner() -> None:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    print("  → Connecting to Chrome via CDP…")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        # Use the first existing browser context (the real session)
        contexts = browser.contexts
        if not contexts:
            browser.close()
            sys.exit("✗  No browser context found — is LinkedIn open in Chrome?")

        ctx = contexts[0]

        # ── Verify we have an authenticated LinkedIn session ──────────────────
        li_at  = next((c for c in ctx.cookies() if c["name"] == "li_at"),  None)
        jsid   = next((c for c in ctx.cookies() if c["name"] == "JSESSIONID"), None)

        if not li_at:
            browser.close()
            sys.exit(
                "✗  li_at cookie not found — you are not logged in to LinkedIn.\n"
                "   1. Close Chrome\n"
                "   2. Re-run ./launch_chrome_for_upload.sh\n"
                "   3. Log in to LinkedIn in the Chrome window\n"
                "   4. Retry upload_local.py"
            )

        jsid_preview = jsid["value"][:25] + "…" if jsid else "✗ missing"
        print(f"     li_at:     ✓")
        print(f"     JSESSIONID: {jsid_preview}")

        # ── Open a new tab and navigate to the profile ────────────────────────
        page = ctx.new_page()
        # Tall viewport so dialogs (esp. the crop editor's Apply button) don't
        # get clipped.  Must be set before navigation.
        page.set_viewport_size({"width": 1280, "height": 1100})

        # Intercept the saveProfileBackgroundImage request and clamp crop
        # coordinates to the valid [0, 1] range.  LinkedIn's server rejects
        # the save if any coordinate is even slightly outside [0, 1] (e.g.
        # -6.12e-17 or 1.0000000000000001) due to floating-point drift in
        # the rotation-matrix used by the crop editor.
        def _clamp_and_forward(route):
            req = route.request
            try:
                raw = req.post_data or ""
            except Exception:
                try:
                    raw = req.post_data_buffer.decode("utf-8", errors="replace")
                except Exception:
                    raw = ""

            if raw:
                try:
                    import json as _json
                    body = _json.loads(raw)
                    fixed = False
                    for state in body.get("states", []):
                        val = state.get("value", {})
                        if not isinstance(val, dict):
                            continue
                        for mf in val.get("mediaFiles", []):
                            ed = mf.get("editData", {})
                            cr = ed.get("croppedRegion", {})
                            for corner in ("topLeft", "topRight", "bottomLeft", "bottomRight"):
                                pt = cr.get(corner, {})
                                for ax in ("x", "y"):
                                    v = pt.get(ax)
                                    if v is not None:
                                        clamped = max(0.0, min(1.0, v))
                                        if clamped != v:
                                            pt[ax] = clamped
                                            fixed = True
                    if fixed:
                        raw = _json.dumps(body)
                        print("     ✓ Clamped crop coordinates to [0, 1]")
                except Exception as exc:
                    print(f"     ⚠  crop-clamp error: {exc}")

            try:
                route.continue_(post_data=raw) if raw else route.continue_()
            except Exception:
                try:
                    route.continue_()
                except Exception:
                    pass

        page.route("**/server-request*saveProfileBackgroundImage*", _clamp_and_forward)

        # Capture save response body
        save_resp_body: list[str] = []
        def _on_save_resp(resp):
            if "saveProfileBackgroundImage" in resp.url:
                try:
                    save_resp_body.append(resp.text()[:1200])
                except Exception:
                    pass
        page.on("response", _on_save_resp)

        try:
            print(f"  → Opening LinkedIn profile…")
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(5_000)
            page.screenshot(path=str(REPO_DIR / "debug_01_profile.png"))

            current_url = page.url
            if any(k in current_url for k in ("/login", "/authwall", "/checkpoint", "/uas/")):
                page.screenshot(path=str(REPO_DIR / "debug_auth_fail.png"))
                sys.exit(
                    f"✗  Auth page: {current_url}\n"
                    "   Chrome session may have expired — log in to LinkedIn and retry."
                )
            print(f"     ✓ Profile loaded  ({current_url})")

            # ── Click the background / cover-photo edit button ────────────────
            print("  → Opening background-photo editor…")
            edit_selectors = [
                "button[aria-label*='background' i]",
                "button[aria-label*='cover' i]",
                "button[aria-label*='Edit background' i]",
                ".profile-topcard-background-image-upload-btn",
                "button:has-text('Edit background')",
                "button:has-text('Edit cover')",
            ]
            clicked = False
            for sel in edit_selectors:
                try:
                    btn = page.locator(sel).first
                    btn.wait_for(state="visible", timeout=4_000)
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    page.wait_for_timeout(1_500)
                    page.screenshot(path=str(REPO_DIR / "debug_02_after_edit_click.png"))
                    clicked = True
                    print(f"     ✓ Clicked: {sel}")
                    break
                except Exception:
                    continue

            if not clicked:
                page.screenshot(path=str(REPO_DIR / "debug_no_edit_btn.png"))
                sys.exit(
                    "✗  Background-edit button not found.\n"
                    "   Check debug_no_edit_btn.png — make sure you're on your own profile."
                )

            # ── Click "Edit cover image" in the dropdown ──────────────────────
            print("  → Selecting 'Edit cover image'…")
            for sel in [
                "text=Edit cover image",
                "text=Edit cover",
                "text=Change cover",
                "li:has-text('Edit cover')",
            ]:
                try:
                    page.locator(sel).first.click(timeout=5_000)
                    page.wait_for_timeout(2_000)
                    page.screenshot(path=str(REPO_DIR / "debug_03_after_cover_click.png"))
                    print(f"     ✓ Clicked: {sel}")
                    break
                except Exception:
                    continue

            # ── Upload the banner file ────────────────────────────────────────
            print("  → Uploading banner.png…")
            uploaded = False
            upload_text_selectors = [
                "text=Upload photo",
                "text=Upload a photo",
                "text=Change photo",
                "text=Upload image",
                "li:has-text('Upload')",
                "button:has-text('Upload')",
            ]
            for sel in upload_text_selectors:
                try:
                    with page.expect_file_chooser(timeout=6_000) as fc_info:
                        page.locator(sel).first.click()
                    fc_info.value.set_files(str(BANNER_PATH))
                    uploaded = True
                    print(f"     ✓ File chosen via: {sel}")
                    break
                except Exception:
                    continue

            if not uploaded:
                # Fallback — try the hidden file input directly
                try:
                    page.locator("input[type='file']").first.set_input_files(
                        str(BANNER_PATH)
                    )
                    uploaded = True
                    print("     ✓ File set via hidden <input type='file'>")
                except Exception:
                    pass

            if not uploaded:
                page.screenshot(path=str(REPO_DIR / "debug_upload_failed.png"))
                sys.exit("✗  Could not trigger file upload → debug_upload_failed.png")

            # Wait for LinkedIn's upload XHR to complete (HTTP 201 back from CDN)
            # before trying to capture any debug screenshot or save.
            print("  → Waiting for CDN upload to complete…")
            try:
                page.wait_for_response(
                    lambda r: "profile-uploadedBackgroundImage" in r.url and r.status == 201,
                    timeout=30_000,
                )
                print("     ✓ Display image upload confirmed (201)")
            except Exception:
                print("     ⚠  Upload response wait timed out — proceeding anyway")

            # Additional settle: wait for network to go idle so the second
            # upload (profile-original-uploadedBackgroundImage) also finishes.
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            page.wait_for_timeout(1_000)
            page.screenshot(path=str(REPO_DIR / "debug_04_after_upload.png"))

            # ── Click Apply in the crop editor ────────────────────────────────
            print("  → Applying crop…")
            page.wait_for_timeout(1_000)

            applied = False

            # Strategy 1: scroll-into-view then click, trying specific selectors
            for sel in [
                "button:has-text('Save changes')",
                "button:has-text('Apply')",
                "button[aria-label='Apply']",
            ]:
                try:
                    btn = page.locator(sel).last
                    btn.wait_for(state="attached", timeout=8_000)
                    btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    btn.click()
                    applied = True
                    print(f"     ✓ Clicked Apply via: {sel}")
                    break
                except Exception:
                    continue

            # Strategy 2: JavaScript click on the Apply button (works even if
            # Playwright thinks it's off-screen)
            if not applied:
                result = page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    // Prefer exact text match inside a modal/dialog
                    const applyBtn = btns.find(b =>
                        (b.textContent.trim() === 'Save changes' ||
                         b.textContent.trim() === 'Apply') &&
                        b.offsetParent !== null
                    );
                    if (applyBtn) { applyBtn.click(); return applyBtn.textContent.trim(); }
                    return null;
                }""")
                if result:
                    applied = True
                    print(f"     ✓ Clicked Apply via JS ({result})")

            if not applied:
                page.screenshot(path=str(REPO_DIR / "debug_apply_failed.png"))
                sys.exit("✗  Apply button not found in crop editor → debug_apply_failed.png")

            page.wait_for_timeout(4_000)
            page.screenshot(path=str(REPO_DIR / "debug_05_after_apply.png"))

            if save_resp_body:
                body = save_resp_body[0]
                has_error = "Save failed" in body or '"errors"' in body
                print(f"  {'✗ Save FAILED' if has_error else '✓ Save OK'} — server response snippet:")
                # Print just the completionAction part
                import re as _re
                m = _re.search(r'"completionAction":\{.*?\}(?=,"errors")', body, _re.DOTALL)
                print(f"     {(m.group()[:400] if m else body[:400])}")
            else:
                print("  ⚠  No save response captured")

            # ── After crop Apply there may be a final Save/Confirm step ───────
            # LinkedIn sometimes shows a "Cover photo" dialog again where you
            # must click Save to commit the change.
            print("  → Checking for final Save step…")
            for sel in [
                "button:has-text('Save')",
                "button[data-control-name*='save' i]",
            ]:
                try:
                    btn = page.locator(sel).last
                    btn.wait_for(state="visible", timeout=5_000)
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    page.wait_for_timeout(3_000)
                    page.screenshot(path=str(REPO_DIR / "debug_06_done.png"))
                    print("  ✓ Banner saved!")
                    return
                except Exception:
                    continue

            # If no Save dialog appeared the Apply already committed the change
            page.screenshot(path=str(REPO_DIR / "debug_06_done.png"))
            print("  ✓ Banner applied (no extra Save step needed)")

        except PWTimeout as exc:
            page.screenshot(path=str(REPO_DIR / "debug_timeout.png"))
            sys.exit(f"✗  Timeout: {exc}")
        finally:
            page.close()
            # close() on a CDP-connected browser only disconnects — does NOT
            # quit the Chrome process the user still needs open.
            browser.close()


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n🖼   LinkedIn Banner Uploader — {datetime.date.today()}")

    if not BANNER_PATH.exists():
        sys.exit(f"✗  banner.png not found at {BANNER_PATH}")

    pull_latest()
    wait_for_cdp()
    upload_banner()
    print("\n✅  Done.")


if __name__ == "__main__":
    main()
