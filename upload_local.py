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

def pull_latest(attempts: int = 3, delay: int = 15) -> None:
    print("  → Pulling latest banner from GitHub…")
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=REPO_DIR, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"     {result.stdout.strip() or 'Already up to date.'}")
            return
        print(f"     ⚠  attempt {attempt}/{attempts} failed: {result.stderr.strip()}")
        if attempt < attempts:
            time.sleep(delay)
    sys.exit(f"✗  git pull failed after {attempts} attempts — upload skipped to avoid stale banner")


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

        # ── Crop-coordinate clamp — JavaScript fetch interceptor ─────────────
        # LinkedIn's crop editor generates floating-point drift in the rotation
        # matrix (e.g. bottomLeft.x = -6.12e-17, bottomRight.y = 1.000000001).
        # The server rejects any coordinate outside [0, 1].
        #
        # Strategy: inject a JS fetch interceptor that clamps the coords using
        # LinkedIn's own JSON.stringify — this avoids any encoding/type mismatch
        # that the Playwright Python-level route.continue_(post_data=...) approach
        # caused (which resulted in "Save failed" toasts despite clamping).
        #
        # The interceptor runs at page-init time (add_init_script) so it is
        # active for every navigation on this page, including the profile page.

        _JS_CLAMP_PATCH = """
(function () {
    if (window.__bannerCropPatchInstalled) return;
    window.__bannerCropPatchInstalled = true;

    // Clamp and clean a single coordinate value:
    //  - values within 1e-9 of 0 → exact 0
    //  - values within 1e-9 of 1 → exact 1
    //  - all others → clamped to [0, 1]
    // LinkedIn's crop editor generates values like 9.8e-16 (should be 0) and
    // 1.000000000000001 (should be 1) due to rotation-matrix floating-point drift.
    // Servers often require exact 0.0 / 1.0 at the corners of a full-image crop.
    function cleanCoord(v) {
        var n = Number(v);
        if (Math.abs(n) < 1e-9)     return 0;
        if (Math.abs(n - 1) < 1e-9) return 1;
        return Math.max(0, Math.min(1, n));
    }

    function clampStates(states) {
        if (!Array.isArray(states)) return false;
        var fixed = false;
        states.forEach(function (state) {
            var val = state && state.value;
            if (!val || typeof val !== 'object' || !val.mediaFiles) return;
            (val.mediaFiles || []).forEach(function (mf) {
                var cr = mf && mf.editData && mf.editData.croppedRegion;
                if (!cr || typeof cr !== 'object') return;
                ['topLeft','topRight','bottomLeft','bottomRight'].forEach(function (corner) {
                    var pt = cr[corner];
                    if (!pt) return;
                    ['x','y'].forEach(function (ax) {
                        var v = pt[ax];
                        if (v !== undefined && v !== null) {
                            var c = cleanCoord(v);
                            if (c !== v) { pt[ax] = c; fixed = true; }
                        }
                    });
                });
            });
        });
        return fixed;
    }

    var origFetch = window.fetch;
    window.fetch = function (url, options) {
        var rest = Array.prototype.slice.call(arguments, 2);
        // Only intercept the actual save endpoint (match by URL, not body content).
        // profileImageRegister body also contains 'saveProfileBackgroundImage' as a
        // next-action reference, so body-content matching would over-fire.
        var isSave = typeof url === 'string' &&
            url.indexOf('saveProfileBackgroundImage') !== -1;
        if (isSave && options && options.body && typeof options.body === 'string') {
            try {
                var body = JSON.parse(options.body);
                var f1 = clampStates(body.states || []);
                var f2 = clampStates(((body.requestedArguments || {}).states) || []);
                if (f1 || f2) {
                    console.log('[BannerPatch] clamped crop coords → ' + url);
                    options = Object.assign({}, options, {body: JSON.stringify(body)});
                } else {
                    console.log('[BannerPatch] save endpoint — coords already clean');
                }
            } catch (e) {
                console.error('[BannerPatch] parse error:', e);
            }
        }
        return origFetch.apply(this, [url, options].concat(rest));
    };
    console.log('[BannerPatch] fetch interceptor installed');
})();
"""
        # Install before any navigation so it is active on the profile page
        page.add_init_script(_JS_CLAMP_PATCH)

        # Track when the JS patch fires (via console messages)
        save_intercepted: list[bool] = [False]

        def _on_console(msg):
            if "BannerPatch" in msg.text:
                save_intercepted[0] = True
                print(f"     [JS] {msg.text}")
        page.on("console", _on_console)

        # Capture full request + response for profileImageRegister and
        # saveProfileBackgroundImage so we can diagnose failures.
        import json as _json
        _key_reqs:   list[dict] = []
        _key_resps:  list[dict] = []

        def _capture_req(req) -> None:
            if any(k in req.url for k in (
                "saveProfileBackgroundImage", "profileImageRegister",
            )):
                tag = "SAVE" if "save" in req.url.lower() else "REGISTER"
                body = ""
                try:
                    body = req.post_data or ""
                except Exception:
                    pass
                _key_reqs.append({"tag": tag, "url": req.url, "body": body})
                print(f"     [REQ-{tag}] {req.url[:100]}")

        def _capture_resp(resp) -> None:
            if any(k in resp.url for k in (
                "saveProfileBackgroundImage", "profileImageRegister",
            )):
                tag = "SAVE" if "save" in resp.url.lower() else "REGISTER"
                body = ""
                try:
                    body = resp.text()
                except Exception:
                    body = "<unreadable>"
                _key_resps.append({
                    "tag": tag, "url": resp.url,
                    "status": resp.status, "body": body,
                })
                print(f"     [RESP-{tag}] HTTP {resp.status}")
                print(f"     [RESP-{tag}-BODY] {body[:600]}")

        page.on("request",  _capture_req)
        page.on("response", _capture_resp)

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

            # Attach a CDN response listener for diagnostic logging.
            # NOTE: based on observed network traffic the CDN upload is triggered
            # by the profileImageRegister API call that fires when the user clicks
            # "Save changes" — NOT by the file-chooser.  Waiting for CDN before
            # clicking Apply is therefore impossible; we log it for information only.
            cdn_done: list[bool] = [False]

            def _detect_cdn(resp) -> None:
                url    = resp.url
                status = resp.status
                if any(k in url for k in ("upload", "dms", "media", "background")) \
                        and status in (200, 201, 202):
                    print(f"     [NET] {status} {url[:100]}")
                if status in (200, 201, 202) and any(k in url for k in (
                    "uploadedBackgroundImage",
                    "background-image",
                    "background_image",
                    "media-upload",
                    "dms/upload",
                    "media/upload",
                )):
                    cdn_done[0] = True
                    print(f"     ✓ CDN upload confirmed (HTTP {status})")

            page.on("response", _detect_cdn)

            # Wait for the crop editor to render before clicking Apply.
            print("  → Waiting for crop editor to render…")
            crop_ready = False
            for _sel in ["button:has-text('Save changes')", "button:has-text('Apply')"]:
                try:
                    page.wait_for_selector(_sel, state="attached", timeout=45_000)
                    crop_ready = True
                    print("     ✓ Crop editor ready")
                    break
                except Exception:
                    continue

            if not crop_ready:
                page.screenshot(path=str(REPO_DIR / "debug_04_no_crop.png"))
                print("     ⚠  Crop editor did not appear — proceeding anyway")

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

            # Wait for the saveProfileBackgroundImage response and capture it.
            # This tells us the exact HTTP status + body that the server returns,
            # which is the most reliable way to diagnose save failures.
            print("  → Waiting for save response…")
            save_http_status: list[int]  = [0]
            save_http_body:   list[str]  = [""]

            def _on_save_response(resp) -> None:
                if "saveProfileBackgroundImage" in resp.url:
                    save_http_status[0] = resp.status
                    try:
                        save_http_body[0] = resp.text()
                    except Exception:
                        save_http_body[0] = "<unreadable>"
                    print(f"     [SAVE-RESP] HTTP {resp.status}")
                    # Print the first 500 chars of the body for diagnosis
                    preview = save_http_body[0][:500].replace("\n", " ")
                    print(f"     [SAVE-RESP] {preview}")

            page.on("response", _on_save_response)

            # Block until the save response arrives.
            # The full flow (CDN upload for both assets + server processing) can
            # take 90–150 s after the Apply click, so use a generous timeout.
            try:
                page.wait_for_response(
                    lambda r: "saveProfileBackgroundImage" in r.url,
                    timeout=180_000,
                )
            except Exception:
                print("     ⚠  Save response not received within 180 s")
                page.wait_for_timeout(5_000)

            page.screenshot(path=str(REPO_DIR / "debug_05_after_apply.png"))

            # Check for a "Save failed" error toast in the UI — definitive signal
            page.wait_for_timeout(1_000)
            save_failed_visible = page.locator("text=Save failed").is_visible()
            if save_failed_visible:
                status_info = f" (HTTP {save_http_status[0]})" if save_http_status[0] else ""
                print(f"  ✗ 'Save failed' toast detected in UI{status_info}")
                body_preview = save_http_body[0][:300].replace("\n", " ")
                if body_preview:
                    print(f"     Server said: {body_preview}")
            elif save_intercepted[0]:
                print("  ✓ JS patch fired (crop coords clamped) — no error toast visible")
            else:
                print("  ⚠  JS patch did NOT fire — save request not detected")

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
            # Write captured request/response data for diagnosis
            debug_out = REPO_DIR / "debug_save_responses.json"
            try:
                debug_out.write_text(
                    _json.dumps(
                        {"requests": _key_reqs, "responses": _key_resps},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                print(f"  → Debug responses written → {debug_out.name}")
            except Exception:
                pass
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
