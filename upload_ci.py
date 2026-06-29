#!/usr/bin/env python3
"""
LinkedIn Banner Uploader — CI mode (GitHub Actions).

Connects to a local Chrome instance (started by the workflow under Xvfb)
via CDP and uploads banner.png using LinkedIn cookies stored as the
LINKEDIN_COOKIES GitHub secret (base64-encoded JSON).

Usage (GitHub Actions step):
    export LINKEDIN_COOKIES="${{ secrets.LINKEDIN_COOKIES }}"
    python3 upload_ci.py

Local testing:
    export LINKEDIN_COOKIES="$(python3 export_linkedin_cookies.py | grep -A1 'Add this' | tail -1)"
    python3 upload_ci.py
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import sys
from pathlib import Path

from upload_local import upload_banner, wait_for_cdp

BANNER_PATH = Path(__file__).parent / "banner.png"


def _load_cookies() -> list[dict]:
    raw = os.environ.get("LINKEDIN_COOKIES", "").strip()
    if not raw:
        sys.exit(
            "✗  LINKEDIN_COOKIES env var is not set.\n"
            "   Run: python3 export_linkedin_cookies.py\n"
            "   Then add the output as LINKEDIN_COOKIES in GitHub → Settings → Secrets."
        )
    try:
        decoded = base64.b64decode(raw)
        cookies = json.loads(decoded)
        if not isinstance(cookies, list):
            raise ValueError("expected a JSON list of cookie dicts")
        return cookies
    except Exception as exc:
        sys.exit(f"✗  Failed to decode LINKEDIN_COOKIES: {exc}")


def main() -> None:
    print(f"\n🖼   LinkedIn Banner Uploader CI — {datetime.date.today()}")

    if not BANNER_PATH.exists():
        sys.exit(
            f"✗  banner.png not found at {BANNER_PATH}\n"
            "   The generate-banner job should have placed it here."
        )

    cookies = _load_cookies()
    li_at = next((c for c in cookies if c.get("name") == "li_at"), None)
    if not li_at:
        sys.exit(
            "✗  li_at cookie missing from LINKEDIN_COOKIES.\n"
            "   Re-run export_linkedin_cookies.py while logged in to LinkedIn, then update the secret."
        )

    print(f"  → Loaded {len(cookies)} cookies  (li_at: ✓)")
    wait_for_cdp()
    upload_banner(inject_cookies=cookies)
    print("\n✅  Done.")


if __name__ == "__main__":
    main()
