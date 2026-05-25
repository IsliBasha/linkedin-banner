#!/usr/bin/env python3
"""
Run this LOCALLY (once) to export your LinkedIn session cookies.
A real browser window will open — log in, then come back here and press Enter.
The script prints a base64 string to store as the LINKEDIN_COOKIES GitHub secret.

Usage:
    pip install playwright
    playwright install chromium
    python3 export_linkedin_cookies.py
"""

import base64
import json

from playwright.sync_api import sync_playwright


def main() -> None:
    print("\n── LinkedIn Cookie Exporter ───────────────────────────")
    print("A browser window will open. Log in to LinkedIn (use")
    print("'Continue with Google' as normal), then come back here")
    print("and press Enter.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page    = context.new_page()
        page.goto("https://www.linkedin.com/login")

        input("Press Enter once you are fully logged in… ")

        cookies     = context.cookies("https://www.linkedin.com")
        cookies_b64 = base64.b64encode(json.dumps(cookies).encode()).decode()

        browser.close()

    print("\n══════════════════════════════════════════════════════")
    print("  Add this as the LINKEDIN_COOKIES secret in GitHub:")
    print("══════════════════════════════════════════════════════\n")
    print(cookies_b64)
    print("\n══════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
