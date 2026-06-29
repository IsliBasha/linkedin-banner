#!/usr/bin/env python3
"""
Checks whether LinkedIn cookies stored in LINKEDIN_COOKIES are still valid.
Exits with code 1 (and prints an action-required message) if the cookies
have been invalidated — LinkedIn can do this on password change, suspicious
activity, or long inactivity.

Used as a GitHub Actions step so the job summary shows cookie health
without blocking the upload if cookies were still good.
"""

from __future__ import annotations

import base64
import json
import os
import sys

import requests


def main() -> None:
    raw = os.environ.get("LINKEDIN_COOKIES", "").strip()
    if not raw:
        print("⚠  LINKEDIN_COOKIES not set — skipping cookie validity check.")
        return

    try:
        cookies_list: list[dict] = json.loads(base64.b64decode(raw))
    except Exception as exc:
        print(f"⚠  Could not decode LINKEDIN_COOKIES: {exc} — skipping check.")
        return

    jar = {c["name"]: c["value"] for c in cookies_list}

    li_at = jar.get("li_at")
    jsessionid = jar.get("JSESSIONID", "")
    csrf_token = jsessionid.strip('"')

    if not li_at:
        print("✗  li_at cookie not found in LINKEDIN_COOKIES.")
        print("   ACTION REQUIRED: re-run export_linkedin_cookies.py and update the GitHub secret.")
        sys.exit(1)

    # Lightweight probe: LinkedIn's Voyager API returns 200 for valid sessions,
    # 401/403 for expired/invalid ones.  This is a read-only call with no side effects.
    try:
        resp = requests.get(
            "https://www.linkedin.com/voyager/api/me",
            cookies=jar,
            headers={
                "csrf-token": csrf_token,
                "x-restli-protocol-version": "2.0.0",
                "accept": "application/json",
                "user-agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            },
            allow_redirects=False,
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"⚠  Network error during cookie check: {exc} — assuming cookies still valid.")
        return

    if resp.status_code == 200:
        print(f"✓  LinkedIn session is valid (HTTP {resp.status_code})")
    elif resp.status_code in (401, 403):
        print(f"✗  LinkedIn session EXPIRED (HTTP {resp.status_code})")
        print("   ACTION REQUIRED: log in to LinkedIn, re-run export_linkedin_cookies.py,")
        print("   and update the LINKEDIN_COOKIES secret at:")
        print("   https://github.com/IsliBasha/linkedin-banner/settings/secrets/actions")
        sys.exit(1)
    elif resp.status_code in (301, 302):
        location = resp.headers.get("location", "")
        if any(k in location for k in ("/login", "/authwall", "/uas/")):
            print(f"✗  LinkedIn session EXPIRED — redirected to {location}")
            print("   ACTION REQUIRED: re-run export_linkedin_cookies.py and update the secret.")
            sys.exit(1)
        print(f"⚠  Redirect to {location} — assuming cookies still valid.")
    else:
        print(f"⚠  Unexpected HTTP {resp.status_code} — assuming cookies still valid.")


if __name__ == "__main__":
    main()
