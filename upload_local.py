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
import hashlib
import json as _json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import ConsoleMessage, Page, Request, Response

REPO_DIR    = Path(__file__).parent.resolve()
BANNER_PATH = REPO_DIR / "banner.png"
PROFILE_URL = os.getenv("LINKEDIN_PROFILE_URL", "https://www.linkedin.com/in/islibasha/")
CDP_PORT    = 9222

# Hash of the last banner this machine successfully uploaded (untracked local
# state). Guards against re-uploading a pixel-identical banner: on 2026-07-16
# both daily runs "succeeded" while visibly changing nothing because GitHub
# ran the 06:00 UTC generation cron ~2 h late and banner.png was still
# yesterday's image at upload time.
LAST_UPLOADED_STATE = REPO_DIR / ".last_uploaded_banner.sha256"

# GitHub executes scheduled workflows late under load (observed ~2 h past the
# 06:00 UTC cron), so the systemd timer polls rather than racing the commit.
POLL_INTERVAL_S = 300     # git pull retry cadence while waiting
POLL_BUDGET_S   = 7200    # give the delayed cron up to 2 h to commit

# JS fetch interceptor — clamps floating-point crop coordinates before the
# saveProfileBackgroundImage request fires.  Lives in crop_patch.js so it can
# be edited and linted independently of this file.
_CROP_PATCH_JS = (REPO_DIR / "crop_patch.js").read_text()


# ─────────────────────────────────────────────────────────────────────────────

def pull_latest(attempts: int = 8, delay: int = 20, per_attempt_timeout: int = 15) -> None:
    """Retry budget sized for a post-suspend network reconnect window.

    Worst case: 8 * 15s (per-attempt timeout) + 7 * 20s (delay) =~ 260s (~4.3
    min), vs. real-world NetworkManager reconnects of 5-60s — roughly 4x
    headroom over the previous 45s budget, which produced unrecoverable
    instant failures whenever a scheduled run landed right after the laptop
    woke from suspend and DNS wasn't ready yet.
    """
    print("  → Pulling latest banner from GitHub…")
    # banner.png is a generated binary — discard local modifications before pulling
    subprocess.run(["git", "restore", "banner.png"], cwd=REPO_DIR, capture_output=True)
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=REPO_DIR, capture_output=True, text=True,
                timeout=per_attempt_timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"     ⚠  attempt {attempt}/{attempts} timed out after {per_attempt_timeout}s "
                  f"(network likely still down)")
            if attempt < attempts:
                time.sleep(delay)
            continue
        if result.returncode == 0:
            print(f"     {result.stdout.strip() or 'Already up to date.'}")
            return
        print(f"     ⚠  attempt {attempt}/{attempts} failed: {result.stderr.strip()}")
        if attempt < attempts:
            time.sleep(delay)
    budget = attempts * per_attempt_timeout + (attempts - 1) * delay
    sys.exit(f"✗  git pull failed after {attempts} attempts (~{budget}s budget) — upload skipped to avoid stale banner")


def banner_sha256(path: Path | None = None) -> str:
    """Content hash of the banner image (defaults resolved at call time)."""
    path = path if path is not None else BANNER_PATH
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_banner_unchanged(path: Path | None = None, state_file: Path | None = None) -> bool:
    """True when banner.png is byte-identical to the last uploaded banner."""
    state_file = state_file if state_file is not None else LAST_UPLOADED_STATE
    try:
        last = state_file.read_text().strip()
    except FileNotFoundError:
        return False
    return bool(last) and last == banner_sha256(path)


def record_uploaded_banner(path: Path | None = None, state_file: Path | None = None) -> None:
    """Persist the hash of a successfully uploaded banner."""
    state_file = state_file if state_file is not None else LAST_UPLOADED_STATE
    state_file.write_text(banner_sha256(path) + "\n")


def wait_for_new_banner(
    *,
    budget_s: int = POLL_BUDGET_S,
    interval_s: int = POLL_INTERVAL_S,
    pull_fn=pull_latest,
    unchanged_fn=is_banner_unchanged,
    sleep_fn=time.sleep,
    monotonic_fn=time.monotonic,
) -> bool:
    """Poll git until banner.png differs from the last uploaded banner.

    Returns True as soon as a new banner is present, False once the budget
    elapses without one.
    """
    deadline = monotonic_fn() + budget_s
    while monotonic_fn() < deadline:
        remaining = deadline - monotonic_fn()
        print(f"  ⏳ Banner unchanged — waiting {interval_s}s for GitHub Actions "
              f"to commit today's banner ({int(remaining)}s left in budget)…")
        sleep_fn(min(interval_s, remaining))
        pull_fn()
        if not unchanged_fn():
            print("  ✓ New banner arrived.")
            return True
    return False


def save_wait_warning(exc: BaseException) -> str:
    """Log line for a failed save-response wait.

    Only a genuine Playwright TimeoutError means the full 180 s elapsed; any
    other exception aborted the wait early and must surface its real cause
    (a bare "timed out" label hid the actual error on 2026-07-16).
    """
    from playwright.sync_api import TimeoutError as PWTimeout
    if isinstance(exc, PWTimeout):
        return "⚠  Save response not received within 180 s"
    return f"⚠  Save-response wait aborted early ({type(exc).__name__}: {exc})"


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


# ── Upload sub-steps ──────────────────────────────────────────────────────────

def _click_background_edit_btn(page: Page) -> None:
    """Click the background/cover-photo edit button on the LinkedIn profile page."""
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
            # Wait for the dropdown menu to appear instead of sleeping blindly.
            page.wait_for_selector("[role='menu']", state="visible", timeout=5_000)
            page.screenshot(path=str(REPO_DIR / "debug_02_after_edit_click.png"))
            clicked = True
            print(f"     ✓ Clicked: {sel}")
            break
        except Exception as e:
            print(f"     ↷ {sel!r} failed: {e}")
            continue

    if not clicked:
        page.screenshot(path=str(REPO_DIR / "debug_no_edit_btn.png"))
        sys.exit(
            "✗  Background-edit button not found.\n"
            "   Check debug_no_edit_btn.png — make sure you're on your own profile."
        )


def _select_edit_cover_image(page: Page) -> None:
    """Select 'Edit cover image' from the open dropdown via JS .click().

    Playwright's .click() fires pointer/focus events that dismiss the menu
    before the click lands.  JS element.click() fires immediately while the
    menu is still open.  By the time this function is called the menu is
    already confirmed visible by _click_background_edit_btn.
    """
    cover_result: dict = page.evaluate("""() => {
        const menu = document.querySelector("[role='menu']");
        if (!menu) return {ok: false, reason: 'no menu'};
        const items = Array.from(menu.querySelectorAll('a, button, [role="menuitem"]'));
        const target = items.find(el =>
            (el.getAttribute('aria-label') || '').toLowerCase().includes('edit cover') ||
            (el.innerText || '').toLowerCase().includes('edit cover image')
        );
        if (!target) return {ok: false, reason: 'item not found',
            found: items.map(el => el.getAttribute('aria-label') || el.innerText?.trim())};
        target.click();
        return {ok: true, clicked: target.tagName + ' / ' + (target.getAttribute('aria-label') || target.innerText?.trim())};
    }""")
    if not cover_result.get("ok"):
        page.screenshot(path=str(REPO_DIR / "debug_03_cover_not_found.png"))
        sys.exit(
            f"✗  'Edit cover image' option not found in dropdown: {cover_result}\n"
            "   Check debug_03_cover_not_found.png — LinkedIn may have changed their UI."
        )
    print(f"     ✓ JS-clicked: {cover_result.get('clicked')}")
    # Wait for whichever upload trigger LinkedIn shows — two known dialogs:
    #  • Simple upload dialog  → "Upload" / "Upload photo" / "Change photo" button
    #  • "Add a cover image"   → "Upload single photo" button + stock image grid
    try:
        page.wait_for_selector(
            "button:has-text('Upload single photo'), "
            "button:has-text('Upload'), "
            "text=Upload photo, text=Change photo",
            state="visible", timeout=10_000,
        )
    except Exception:
        pass  # _set_upload_file will handle not-found
    page.screenshot(path=str(REPO_DIR / "debug_03_after_cover_click.png"))


def _navigate_to_upload_dialog(page: Page) -> None:
    """Bridge the intermediate 'Cover photo' dialog (Edit/Change photo/Delete).

    When a cover photo already exists, clicking 'Edit cover image' lands on a
    'Cover photo' dialog instead of a direct upload dialog.  Clicking 'Change
    photo' there opens the 'Add a cover image' dialog where 'Upload single
    photo' triggers the file chooser.

    Uses JS .click() for the same reason as _select_edit_cover_image — Playwright
    pointer events can dismiss the dialog before the click lands.
    """
    # Primary: get_by_text is element-type agnostic — works even if LinkedIn
    # uses <div> or <li> for the option instead of <button>.
    try:
        el = page.get_by_text("Change photo", exact=True).first
        el.wait_for(state="visible", timeout=6_000)
        el.click(timeout=5_000)
        print("     ✓ Clicked 'Change photo' (Cover photo dialog → upload dialog)")
        try:
            page.wait_for_selector(
                "button:has-text('Upload single photo'), button:has-text('Upload')",
                state="visible", timeout=10_000,
            )
        except Exception:
            pass
        return
    except Exception as e:
        print(f"     ↷ get_by_text('Change photo') failed ({e!r})")

    # Fallback: JS evaluate over all common clickable element types
    result: bool = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll(
            'button, a, div[role="button"], li, span[tabindex]'
        ));
        const target = els.find(e =>
            (e.getAttribute('aria-label') || '').toLowerCase().includes('change photo') ||
            (e.textContent || '').toLowerCase().includes('change photo')
        );
        if (target) { target.click(); return true; }
        return false;
    }""")
    if result:
        print("     ✓ JS-clicked 'Change photo' via fallback selector")
        try:
            page.wait_for_selector(
                "button:has-text('Upload single photo'), button:has-text('Upload')",
                state="visible", timeout=10_000,
            )
        except Exception:
            pass
    else:
        print("     ↷ No 'Cover photo' intermediate dialog — already on upload surface")


def _set_upload_file(page: Page, banner_path: Path) -> None:
    """Trigger the file chooser and set banner_path as the upload."""
    upload_text_selectors = [
        # "Add a cover image" dialog — appears after _navigate_to_upload_dialog
        "button:has-text('Upload single photo')",
        # Simple upload dialog — appears when no cover photo exists yet
        "button:has-text('Upload')",
        "text=Upload photo",
        "text=Upload a photo",
        "text=Upload image",
        "li:has-text('Upload')",
    ]
    for sel in upload_text_selectors:
        try:
            with page.expect_file_chooser(timeout=6_000) as fc_info:
                page.locator(sel).first.click(timeout=5_000)
            fc_info.value.set_files(str(banner_path))
            print(f"     ✓ File chosen via: {sel}")
            return
        except Exception as e:
            print(f"     ↷ {sel!r} failed: {e}")
            continue

    # Fallback — try the hidden file input directly
    try:
        page.locator("input[type='file']").first.set_input_files(str(banner_path))
        print("     ✓ File set via hidden <input type='file'>")
        return
    except Exception:
        pass

    page.screenshot(path=str(REPO_DIR / "debug_upload_failed.png"))
    sys.exit("✗  Could not trigger file upload → debug_upload_failed.png")


def _click_apply_crop(page: Page) -> None:
    """Click the Apply/Save changes button in LinkedIn's crop editor."""
    # Strategy 1: scroll-into-view then Playwright click
    for sel in [
        "button:has-text('Save changes')",
        "button:has-text('Apply')",
        "button[aria-label='Apply']",
    ]:
        try:
            btn = page.locator(sel).last
            btn.wait_for(state="attached", timeout=8_000)
            btn.scroll_into_view_if_needed()
            page.wait_for_timeout(500)   # let scroll animation settle before click
            btn.click()
            print(f"     ✓ Clicked Apply via: {sel}")
            return
        except Exception as e:
            print(f"     ↷ {sel!r} failed: {e}")
            continue

    # Strategy 2: JS click (works even when Playwright thinks button is off-screen)
    result: str | None = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const applyBtn = btns.find(b =>
            (b.textContent.trim() === 'Save changes' ||
             b.textContent.trim() === 'Apply') &&
            b.offsetParent !== null
        );
        if (applyBtn) { applyBtn.click(); return applyBtn.textContent.trim(); }
        return null;
    }""")
    if result:
        print(f"     ✓ Clicked Apply via JS ({result})")
        return

    page.screenshot(path=str(REPO_DIR / "debug_apply_failed.png"))
    sys.exit("✗  Apply button not found in crop editor → debug_apply_failed.png")


# ─────────────────────────────────────────────────────────────────────────────

def upload_banner(inject_cookies: list | None = None) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    print("  → Connecting to Chrome via CDP…")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

        if inject_cookies:
            # CI mode: fresh Chrome session → create isolated context and inject cookies
            ctx = browser.new_context()
            ctx.add_cookies(inject_cookies)
        else:
            # Local mode: reuse the existing logged-in Chrome session, but
            # explicitly (re-)inject cookies read directly from the real
            # Chrome profile. li_at specifically does not reliably survive
            # being loaded by Chrome from launch_chrome_for_upload.sh's
            # copied Cookies database in the dedicated profile (confirmed:
            # byte-identical on disk right after copy, but silently evicted
            # from Chrome's live cookie store within seconds, while ~87 other
            # cookies from the same copy load and persist fine) — injecting
            # it directly over CDP sidesteps that instead of depending on
            # Chrome to load it from disk.
            contexts = browser.contexts
            if not contexts:
                browser.close()
                sys.exit("✗  No browser context found — is LinkedIn open in Chrome?")
            ctx = contexts[0]

            try:
                from export_linkedin_cookies import read_chrome_cookies
                real_cookies = read_chrome_cookies()
            except Exception as exc:
                real_cookies = []
                print(f"     ⚠  could not read real Chrome cookies for injection: {exc}")
            if real_cookies:
                ctx.add_cookies(real_cookies)

        # ── Verify authenticated LinkedIn session ─────────────────────────────
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

        # ── Open a new tab ────────────────────────────────────────────────────
        page = ctx.new_page()
        # Tall viewport so dialogs (esp. the crop editor's Apply button) don't
        # get clipped.  Must be set before navigation.
        page.set_viewport_size({"width": 1280, "height": 1100})

        # ── Crop-coordinate clamp — JS fetch interceptor ──────────────────────
        # LinkedIn's crop editor generates floating-point drift in the rotation
        # matrix (e.g. bottomLeft.x = -6.12e-17, bottomRight.y = 1.000000001).
        # The server rejects any coordinate outside [0, 1].
        # Injected at page-init time so it is active for every navigation on
        # this page, including the profile page.  See crop_patch.js for details.
        page.add_init_script(_CROP_PATCH_JS)

        save_intercepted: list[bool] = [False]

        def _on_console(msg: ConsoleMessage) -> None:
            if "BannerPatch" in msg.text:
                save_intercepted[0] = True
                print(f"     [JS] {msg.text}")

        page.on("console", _on_console)

        # ── Network capture — full req/resp for diagnosis ─────────────────────
        _key_reqs:  list[dict] = []
        _key_resps: list[dict] = []

        def _capture_req(req: Request) -> None:
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

        def _capture_resp(resp: Response) -> None:
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
            # ── Navigate to profile ───────────────────────────────────────────
            print(f"  → Opening LinkedIn profile…")
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=45_000)
            # Wait for the edit button rather than sleeping a fixed 5 s —
            # returns as soon as the profile header renders, typically 1-2 s.
            try:
                page.wait_for_selector(
                    "button[aria-label*='background' i], button[aria-label*='cover' i]",
                    state="visible", timeout=15_000,
                )
            except Exception:
                pass  # edit-button loop will handle not-found
            page.screenshot(path=str(REPO_DIR / "debug_01_profile.png"))

            current_url = page.url
            if any(k in current_url for k in ("/login", "/authwall", "/checkpoint", "/uas/")):
                page.screenshot(path=str(REPO_DIR / "debug_auth_fail.png"))
                sys.exit(
                    f"✗  Auth page: {current_url}\n"
                    "   Chrome session may have expired — log in to LinkedIn and retry."
                )
            print(f"     ✓ Profile loaded  ({current_url})")

            # ── Edit cover photo ──────────────────────────────────────────────
            print("  → Opening background-photo editor…")
            _click_background_edit_btn(page)

            print("  → Selecting 'Edit cover image'…")
            _select_edit_cover_image(page)

            print("  → Navigating to upload dialog…")
            _navigate_to_upload_dialog(page)

            # ── Upload file ───────────────────────────────────────────────────
            print("  → Uploading banner.png…")
            _set_upload_file(page, BANNER_PATH)

            # Attach CDN response listener for diagnostic logging.
            # NOTE: CDN upload is triggered by profileImageRegister when the user
            # clicks "Save changes" — NOT by the file-chooser.  We log it for
            # information only; it is not a gate for clicking Apply.
            cdn_done: list[bool] = [False]

            def _detect_cdn(resp: Response) -> None:
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

            # ── Wait for crop editor ──────────────────────────────────────────
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

            # ── Apply crop ────────────────────────────────────────────────────
            print("  → Applying crop…")
            _click_apply_crop(page)

            # ── Wait for save response ────────────────────────────────────────
            # The full flow (CDN upload for both assets + server processing) can
            # take 90–150 s after the Apply click, so use a generous timeout.
            print("  → Waiting for save response…")
            save_http_status: list[int] = [0]
            save_http_body:   list[str] = [""]

            def _on_save_response(resp: Response) -> None:
                if "saveProfileBackgroundImage" in resp.url:
                    save_http_status[0] = resp.status
                    try:
                        save_http_body[0] = resp.text()
                    except Exception:
                        save_http_body[0] = "<unreadable>"
                    print(f"     [SAVE-RESP] HTTP {resp.status}")
                    preview = save_http_body[0][:500].replace("\n", " ")
                    print(f"     [SAVE-RESP] {preview}")

            page.on("response", _on_save_response)

            try:
                page.wait_for_response(
                    lambda r: "saveProfileBackgroundImage" in r.url,
                    timeout=180_000,
                )
            except Exception as exc:
                print(f"     {save_wait_warning(exc)}")
                page.wait_for_timeout(5_000)

            page.screenshot(path=str(REPO_DIR / "debug_05_after_apply.png"))

            # ── Check for save-failed toast ───────────────────────────────────
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

            # ── Final Save step ───────────────────────────────────────────────
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

            page.screenshot(path=str(REPO_DIR / "debug_06_done.png"))
            print("  ✓ Banner applied (no extra Save step needed)")

        except PWTimeout as exc:
            page.screenshot(path=str(REPO_DIR / "debug_timeout.png"))
            sys.exit(f"✗  Timeout: {exc}")
        finally:
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
    force = "--force" in sys.argv[1:]
    print(f"\n🖼   LinkedIn Banner Uploader — {datetime.date.today()}")

    if not BANNER_PATH.exists():
        sys.exit(f"✗  banner.png not found at {BANNER_PATH}")

    pull_latest()

    if not force and is_banner_unchanged():
        if os.getenv("POLL_FOR_NEW_BANNER") == "1":
            # Timer path: GitHub's 06:00 UTC cron runs up to ~2 h late, so
            # wait for the day's commit instead of racing it.
            if not wait_for_new_banner():
                sys.exit(
                    "✗  banner.png is still identical to the last uploaded banner — "
                    f"no new commit arrived within {POLL_BUDGET_S // 60} min. "
                    "GitHub Actions has not generated today's banner; upload skipped "
                    "(re-uploading an identical image changes nothing). Check "
                    "https://github.com/IsliBasha/linkedin-banner/actions"
                )
        else:
            # Interactive path: fail fast with the real story instead of
            # "succeeding" without any visible effect.
            sys.exit(
                "✗  banner.png is identical to the last uploaded banner — GitHub "
                "Actions has not committed today's banner yet, so uploading would "
                "change nothing visible. Re-run later, or pass --force to upload anyway."
            )

    wait_for_cdp()
    upload_banner()
    record_uploaded_banner()
    print("\n✅  Done.")


if __name__ == "__main__":
    main()
