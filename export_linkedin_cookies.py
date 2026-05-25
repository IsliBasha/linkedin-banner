#!/usr/bin/env python3
"""
Reads LinkedIn cookies directly from your Chrome installation.
No browser window needed — Chrome just needs to be closed first.

Usage:
    pip install browser-cookie3
    python3 export_linkedin_cookies.py
"""

import base64
import json
import sys


def main() -> None:
    try:
        import browser_cookie3
    except ImportError:
        sys.exit("✗  Run: pip install browser-cookie3")

    print("Reading LinkedIn cookies from Chrome…")
    print("(Make sure Chrome is closed first)\n")

    try:
        jar = browser_cookie3.chrome(domain_name=".linkedin.com")
    except Exception as e:
        sys.exit(f"✗  Could not read Chrome cookies: {e}")

    cookies = [
        {
            "name":     c.name,
            "value":    c.value,
            "domain":   c.domain if c.domain.startswith(".") else f".{c.domain}",
            "path":     c.path or "/",
            "secure":   bool(c.secure),
            "httpOnly": False,
        }
        for c in jar
        if c.value
    ]

    if not cookies:
        sys.exit(
            "✗  No LinkedIn cookies found in Chrome.\n"
            "   Make sure you are logged in to LinkedIn in Chrome."
        )

    # Check li_at is present
    names = {c["name"] for c in cookies}
    if "li_at" not in names:
        sys.exit(
            "✗  li_at cookie not found — are you logged in to LinkedIn in Chrome?"
        )

    cookies_b64 = base64.b64encode(json.dumps(cookies).encode()).decode()

    print(f"✓  Found {len(cookies)} LinkedIn cookies (including li_at)\n")
    print("══════════════════════════════════════════════════════")
    print("  Add this as the LINKEDIN_COOKIES secret in GitHub:")
    print("══════════════════════════════════════════════════════\n")
    print(cookies_b64)
    print("\n══════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
