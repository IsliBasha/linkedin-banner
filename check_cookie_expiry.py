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
from enum import Enum

import requests


class SessionStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    AMBIGUOUS = "ambiguous"


def probe_session(jar: dict[str, str]) -> tuple[SessionStatus, str]:
    """Read-only probe of a LinkedIn cookie jar's session validity.

    Pure function (no env var, no sys.exit) so both this script's CLI and
    doctor.py can reuse the exact same check against different cookie sources
    (a base64 GitHub secret here, a live local Chrome profile in doctor.py).
    """
    li_at = jar.get("li_at")
    jsessionid = jar.get("JSESSIONID", "")
    csrf_token = jsessionid.strip('"')

    if not li_at:
        return SessionStatus.EXPIRED, "li_at cookie not found in cookie jar."

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
        return SessionStatus.AMBIGUOUS, f"Network error during cookie check: {exc}"

    if resp.status_code == 200:
        return SessionStatus.VALID, f"LinkedIn session is valid (HTTP {resp.status_code})"
    if resp.status_code in (401, 403):
        return SessionStatus.EXPIRED, f"LinkedIn session EXPIRED (HTTP {resp.status_code})"
    if resp.status_code in (301, 302):
        location = resp.headers.get("location", "")
        if any(k in location for k in ("/login", "/authwall", "/uas/")):
            return SessionStatus.EXPIRED, f"LinkedIn session EXPIRED — redirected to {location}"
        return SessionStatus.AMBIGUOUS, f"Redirect to {location} — assuming cookies still valid."
    return SessionStatus.AMBIGUOUS, f"Unexpected HTTP {resp.status_code} — assuming cookies still valid."


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

    if "li_at" not in jar:
        print("✗  li_at cookie not found in LINKEDIN_COOKIES.")
        print("   ACTION REQUIRED: re-run export_linkedin_cookies.py and update the GitHub secret.")
        sys.exit(1)

    status, msg = probe_session(jar)
    icon = {SessionStatus.VALID: "✓", SessionStatus.EXPIRED: "✗", SessionStatus.AMBIGUOUS: "⚠"}[status]
    print(f"{icon}  {msg}")

    if status == SessionStatus.EXPIRED:
        print("   ACTION REQUIRED: log in to LinkedIn, re-run export_linkedin_cookies.py,")
        print("   and update the LINKEDIN_COOKIES secret at:")
        print("   https://github.com/IsliBasha/linkedin-banner/settings/secrets/actions")
        sys.exit(1)


if __name__ == "__main__":
    main()
