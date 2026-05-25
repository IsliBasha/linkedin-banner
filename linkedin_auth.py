#!/usr/bin/env python3
"""
LinkedIn OAuth helper — run once to get LINKEDIN_ACCESS_TOKEN + LINKEDIN_PERSON_URN.

Usage:
    python3 linkedin_auth.py --client-id YOUR_ID --client-secret YOUR_SECRET
"""

import argparse
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

REDIRECT_URI = "http://localhost:8080/callback"
SCOPE        = "w_member_social"


class _CallbackHandler(BaseHTTPRequestHandler):
    code  = None
    error = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _CallbackHandler.error = params["error"][0]
        elif "code" in params:
            _CallbackHandler.code = params["code"][0]

        body = b"<html><body><h2>Done! You can close this tab.</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):   # suppress request log noise
        pass


def _exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     client_id,
        "client_secret": client_secret,
    }).encode()
    req  = urllib.request.Request(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _fetch_userinfo(token: str) -> dict:
    req = urllib.request.Request(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id",     required=True)
    parser.add_argument("--client-secret", required=True)
    args = parser.parse_args()

    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={urllib.parse.quote(args.client_id)}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={urllib.parse.quote(SCOPE)}"
        f"&state={state}"
    )

    print("\n── Step 1: Authorize ──────────────────────────────────")
    print("Opening your browser. Log in and click 'Allow'.\n")
    print(f"  {auth_url}\n")
    print("(If the browser doesn't open, paste the URL above manually.)")
    webbrowser.open(auth_url)

    print("\n── Step 2: Waiting for callback on localhost:8080 … ───")
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()   # blocks until one request arrives

    if _CallbackHandler.error:
        sys.exit(f"✗  LinkedIn returned an error: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        sys.exit("✗  No code received. Did you authorise the app?")

    print("  ✓ Authorisation code received")

    print("\n── Step 3: Exchanging code for access token … ─────────")
    token_data = _exchange_code(
        _CallbackHandler.code, args.client_id, args.client_secret
    )
    access_token = token_data.get("access_token")
    expires_in   = token_data.get("expires_in", 0)
    if not access_token:
        sys.exit(f"✗  Token exchange failed: {token_data}")
    print(f"  ✓ Access token obtained (expires in {expires_in // 86400} days)")

    print("\n══════════════════════════════════════════════════════")
    print("  Update this GitHub Actions secret:")
    print("══════════════════════════════════════════════════════")
    print(f"\n  LINKEDIN_ACCESS_TOKEN = {access_token}")
    print("\n══════════════════════════════════════════════════════")
    print("  Token lifetime: ~60 days. Rotate before it expires.")
    print("══════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
