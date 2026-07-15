#!/usr/bin/env bash
# One-click manual trigger — launches Chrome with your real LinkedIn session
# and runs the upload, combining the two normally-separate manual steps into
# one double-clickable action. Run this any time (e.g. right after logging
# into LinkedIn in your regular Chrome) rather than waiting for the daily timer.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

./launch_chrome_for_upload.sh
sleep 10
.venv/bin/python3 upload_local.py

echo
read -rp "Press Enter to close..."
