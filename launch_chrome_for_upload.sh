#!/usr/bin/env bash
# Launch a dedicated Chrome instance for the LinkedIn banner uploader.
#
# Uses a SEPARATE profile dir (~/.config/linkedin-banner-chrome) so it never
# touches your regular Chrome session.  Your regular Chrome keeps running
# undisturbed.
#
# Cookie copy strategy:
#   Uses Python's sqlite3 ".backup" to create a crash-safe snapshot of the
#   Cookies database even while regular Chrome is open.  No Chrome kill needed.
#
# Usage:
#   ./launch_chrome_for_upload.sh        # launch; then run upload_local.py
#
# Systemd (automated daily):
#   See: ~/.config/systemd/user/linkedin-banner.{service,timer}

CDP_PORT=9222
CHROME=/usr/bin/google-chrome
REAL_COOKIES="${HOME}/.config/google-chrome/Default/Cookies"
TEMP_DIR="${HOME}/.config/linkedin-banner-chrome"

# ── Already running with debug port? ─────────────────────────────────────────
if curl -s --max-time 1 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    echo "  ✓ CDP Chrome already running on port ${CDP_PORT}."
    exit 0
fi

# ── Kill only the banner-Chrome if it's running without the debug port ────────
# (Regular Chrome uses ~/.config/google-chrome — we never touch it.)
if pgrep -f "user-data-dir.*linkedin-banner-chrome" >/dev/null 2>&1; then
    echo "  → Restarting stale banner-Chrome…"
    pkill -f "user-data-dir.*linkedin-banner-chrome" 2>/dev/null || true
    sleep 2
fi

# ── Copy cookies via Python sqlite3 backup (safe while Chrome is open) ────────
mkdir -p "${TEMP_DIR}/Default"
if [[ -f "${REAL_COOKIES}" ]]; then
    python3 - <<'PYEOF'
import os, sys, shutil, sqlite3
src = os.path.expanduser("~/.config/google-chrome/Default/Cookies")
dst = os.path.expanduser("~/.config/linkedin-banner-chrome/Default/Cookies")
try:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    bak = sqlite3.connect(dst)
    con.backup(bak)
    bak.close(); con.close()
    print("  ✓ Cookies backed up (sqlite3 online backup).")
except Exception as e:
    # Fallback: plain copy (works when Chrome is fully closed)
    shutil.copy2(src, dst)
    print(f"  ✓ Cookies copied (plain copy; sqlite3 backup failed: {e})")
PYEOF
else
    echo "  ⚠  No Cookies file at ${REAL_COOKIES}."
    echo "     Log in to LinkedIn in the Chrome window that opens."
fi

# ── Launch the banner-Chrome with remote debugging ────────────────────────────
echo "  → Launching banner-Chrome (port ${CDP_PORT})…"
"${CHROME}" \
    --remote-debugging-port=${CDP_PORT} \
    --user-data-dir="${TEMP_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    "https://www.linkedin.com/in/islibasha/" \
    &

CHROME_PID=$!
echo "  ✓ Chrome launched (PID ${CHROME_PID})."
echo "  → Wait ~10 s for LinkedIn to load, then run: python3 upload_local.py"
