#!/usr/bin/env bash
# One-click manual trigger — launches Chrome with your real LinkedIn session
# and runs the upload, combining the two normally-separate manual steps into
# one double-clickable action. Run this any time (e.g. right after logging
# into LinkedIn in your regular Chrome) rather than waiting for the daily timer.
cd "$(dirname "${BASH_SOURCE[0]}")"

./launch_chrome_for_upload.sh
sleep 10
.venv/bin/python3 upload_local.py
STATUS=$?

echo
if [ "$STATUS" -ne 0 ]; then
    echo "⚠  Upload failed (exit $STATUS). LinkedIn's UI sometimes needs a"
    echo "   moment to finish loading on a cold Chrome launch — if that's"
    echo "   what happened, just click the icon again."
fi
read -rp "Press Enter to close..."
