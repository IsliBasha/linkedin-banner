#!/usr/bin/env bash
# Launch Chrome with remote debugging enabled for the LinkedIn banner uploader.
#
# Chrome REFUSES --remote-debugging-port when pointed at its default profile
# directory (~/.config/google-chrome).  We work around this by:
#   1. Copying your real cookies to a separate profile dir (NOT the default).
#   2. Launching Chrome against that dir with the debug port enabled.
#
# The GNOME Keyring "Chrome Safe Storage" key is system-wide, so Chrome can
# decrypt the copied cookie file perfectly — no new login required.
#
# Run once before upload_local.py (Chrome must stay open while the script runs):
#   ./launch_chrome_for_upload.sh
#   python3 upload_local.py
#
# Cron (Chrome opened automatically before upload):
#   0 7 * * * cd /home/lugat/Documents/linkedin-banner && \
#             ./launch_chrome_for_upload.sh && sleep 8 && \
#             .venv/bin/python3 upload_local.py >> ~/.linkedin_banner.log 2>&1

CDP_PORT=9222
CHROME=/usr/bin/google-chrome
REAL_COOKIES=~/.config/google-chrome/Default/Cookies
TEMP_DIR=~/.config/linkedin-banner-chrome   # non-default path → debug port allowed

# ── Already running with debug port ──────────────────────────────────────────
if curl -s --max-time 1 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "Chrome already running with debug port ${CDP_PORT} — OK."
    exit 0
fi

# ── Close any Chrome that is running without the debug port ──────────────────
if pgrep -x "chrome" >/dev/null 2>&1 || pgrep -x "google-chrome" >/dev/null 2>&1; then
    echo "Closing existing Chrome so we can copy the cookie file safely…"
    pkill -x "chrome"        2>/dev/null || true
    pkill -x "google-chrome" 2>/dev/null || true
    sleep 3
fi

# ── Copy cookies into the temp profile ───────────────────────────────────────
mkdir -p "${TEMP_DIR}/Default"
if [[ -f "${REAL_COOKIES}" ]]; then
    cp "${REAL_COOKIES}" "${TEMP_DIR}/Default/Cookies"
    # Copy WAL/SHM if present (usually absent after a clean Chrome exit)
    cp "${REAL_COOKIES}-wal" "${TEMP_DIR}/Default/Cookies-wal" 2>/dev/null || true
    cp "${REAL_COOKIES}-shm" "${TEMP_DIR}/Default/Cookies-shm" 2>/dev/null || true
    echo "  ✓ Cookies copied from real Chrome profile."
else
    echo "  ⚠  No Cookies file found at ${REAL_COOKIES}."
    echo "     You will need to log in to LinkedIn in the Chrome window that opens."
fi

# ── Launch Chrome with the temp profile + debug port ─────────────────────────
echo "Launching Chrome with --remote-debugging-port=${CDP_PORT}…"
"$CHROME" \
    --remote-debugging-port=${CDP_PORT} \
    --user-data-dir="${TEMP_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    "https://www.linkedin.com/in/islibasha/" \
    &

CHROME_PID=$!
echo "Done (PID ${CHROME_PID})."
echo "Wait ~5 s for LinkedIn to finish loading, then run:  python3 upload_local.py"
