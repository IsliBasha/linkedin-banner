#!/usr/bin/env bash
# Launch a dedicated Chrome instance for the LinkedIn banner uploader.
#
# Uses a SEPARATE profile dir (~/.config/linkedin-banner-chrome) so it never
# touches your regular Chrome session.  Your regular Chrome keeps running
# undisturbed.  That separate profile is also what keeps CDP working at all:
# Chrome >= 136 refuses --remote-debugging-port when it is pointed at the
# default user-data-dir, and only honours it for a non-default profile.
#
# Cookie copy strategy:
#   Linux only, and only as a convenience for the retired lugat box: Python's
#   sqlite3 ".backup" takes a crash-safe snapshot of the regular profile's
#   Cookies database even while that Chrome is open, and the snapshot replaces
#   the dedicated profile's copy only when it really holds an unexpired
#   LinkedIn li_at.  On macOS nothing is copied at all — see the Darwin branch.
#
# Usage:
#   ./launch_chrome_for_upload.sh        # launch; then run upload_local.py
#
# Scheduled (automated daily):
#   macOS  : launchd/com.islibasha.linkedin-banner.plist (21:00 local)
#   Linux  : systemd/linkedin-banner.{service,timer}  — retired 2026-09-01

CDP_PORT=9222
TEMP_DIR="${HOME}/.config/linkedin-banner-chrome"
PROFILE_URL="https://www.linkedin.com/in/islibasha/"

# ── Platform defaults ─────────────────────────────────────────────────────────
# The dedicated profile dir is deliberately the SAME path on both platforms
# (Chrome accepts any --user-data-dir), so only the parts that genuinely differ
# branch here.
case "$(uname -s)" in
    Darwin)
        CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        # Deliberately empty: nothing is copied in from the regular Chrome.
        # This profile is the only session store here, which is what the README
        # and doctor already assume — and importing the regular jar would hand
        # every local uid-501 process the owner's session on every site it
        # holds, through the CDP port, with no Keychain prompt.
        REAL_COOKIES=""
        WAIT_FOR_X_DISPLAY=0
        # No --no-sandbox: macOS Chrome answers it with a permanent
        # "You are using an unsupported command-line flag" infobar.
        CHROME_PLATFORM_FLAGS=()
        ;;
    *)
        CHROME=/usr/bin/google-chrome
        REAL_COOKIES="${HOME}/.config/google-chrome/Default/Cookies"
        WAIT_FOR_X_DISPLAY=1
        CHROME_PLATFORM_FLAGS=(--no-sandbox)
        ;;
esac

# Matches only Chrome processes started against OUR profile. A bare
# "linkedin-banner-chrome" substring would also match an editor or a grep that
# happens to have the path on its command line.
BANNER_CHROME_PATTERN="--user-data-dir=${TEMP_DIR}"

port_owner() {
    lsof -nP "-iTCP:${CDP_PORT}" -sTCP:LISTEN 2>/dev/null \
        | awk 'NR == 2 { print $1 " (pid " $2 ")" }'
}

# ── Already running with debug port? ─────────────────────────────────────────
if curl -s --max-time 1 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    # Trusting any listener would hand the upload to whatever else owns 9222 —
    # a Playwright run, an MCP Chromium — and drive a browser we know nothing
    # about, nightly.
    if pgrep -f -- "${BANNER_CHROME_PATTERN}" >/dev/null 2>&1; then
        echo "  ✓ CDP Chrome already running on port ${CDP_PORT}."
        exit 0
    fi
    echo "  ✗ port ${CDP_PORT} is held by another process ($(port_owner)) — refusing to drive a foreign browser"
    exit 1
fi

# ── Kill only the banner-Chrome if it's running without the debug port ────────
# (Regular Chrome uses its own profile dir — we never touch it.)
if pgrep -f -- "${BANNER_CHROME_PATTERN}" >/dev/null 2>&1; then
    echo "  → Restarting stale banner-Chrome…"
    pkill -f -- "${BANNER_CHROME_PATTERN}" 2>/dev/null || true
    sleep 2
    pkill -9 -f -- "${BANNER_CHROME_PATTERN}" 2>/dev/null || true   # force-kill stragglers (e.g. crashpad handler ignoring SIGTERM)
fi

# ── Copy cookies via Python sqlite3 backup (safe while Chrome is open) ────────
# REAL_COOKIES is empty on macOS, and `-f ""` is false, so this whole block is
# Linux-only in practice.
mkdir -p "${TEMP_DIR}/Default"
chmod 700 "${TEMP_DIR}" "${TEMP_DIR}/Default" 2>/dev/null || true
if [[ -f "${REAL_COOKIES}" ]]; then
    # Paths come from the shell so this block follows the platform branch above
    # instead of hardcoding the Linux ones.
    SRC_COOKIES="${REAL_COOKIES}" DST_COOKIES="${TEMP_DIR}/Default/Cookies" python3 - <<'PYEOF'
import os
import sqlite3
import time

os.umask(0o077)   # nothing this block creates should be group/world readable

src = os.environ["SRC_COOKIES"]
dst = os.environ["DST_COOKIES"]
snapshot = dst + ".snapshot"

CHROME_EPOCH_OFFSET_S = 11644473600   # Chrome counts microseconds from 1601-01-01


def snapshot_source() -> None:
    """Copy the source Cookies DB aside, consistently, while Chrome runs."""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        bak = sqlite3.connect(snapshot)
        try:
            con.backup(bak)
        finally:
            bak.close()
    finally:
        con.close()


def has_live_linkedin_session(db: str) -> bool:
    """True when the SNAPSHOT holds an unexpired LinkedIn li_at row.

    Checked on the snapshot, never on the live DB: the live one may be locked
    or mid-write, and the snapshot is the exact bytes we would install anyway.
    The host match is anchored — '%linkedin.com' would also accept a cookie set
    by .evil-linkedin.com — and expiry is checked because installing an expired
    li_at would replace a working session with a dead one.
    """
    now_chrome_us = int((time.time() + CHROME_EPOCH_OFFSET_S) * 1_000_000)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        (found,) = con.execute(
            "SELECT COUNT(*) FROM cookies WHERE name = 'li_at' "
            "AND (host_key = '.linkedin.com' OR host_key LIKE '%.linkedin.com') "
            "AND expires_utc > ?",
            (now_chrome_us,),
        ).fetchone()
    finally:
        con.close()
    return bool(found)


try:
    try:
        snapshot_source()
    except Exception as exc:
        # No plain-copy fallback: copying a live DB can yield a torn file, and
        # installing that would trade a working session for garbage.
        print(f"  ⚠  Could not snapshot {src} ({exc}) — dedicated profile left untouched.")
        raise SystemExit(0)

    try:
        logged_in = has_live_linkedin_session(snapshot)
    except sqlite3.Error as exc:
        print(f"  ⚠  Snapshot of {src} is unreadable ({exc}) — dedicated profile left untouched.")
        raise SystemExit(0)

    if logged_in:
        os.chmod(snapshot, 0o600)
        os.replace(snapshot, dst)
        print("  ✓ Cookies copied from the regular Chrome profile (sqlite3 online backup).")
    else:
        print(f"  ⚠  {src} has no unexpired LinkedIn li_at cookie — dedicated profile left untouched.")
        print("     Log in to LinkedIn in the Chrome window that opens.")
finally:
    # A signal mid-copy would otherwise leave a full cookie jar lying around.
    if os.path.exists(snapshot):
        os.remove(snapshot)
PYEOF
else
    echo "  ⚠  No source Cookies file${REAL_COOKIES:+ at ${REAL_COOKIES}}."
    echo "     Log in to LinkedIn in the Chrome window that opens."
fi

# ── Wait for X display to be ready (race condition on login with Persistent=true) ─
# Linux/X11 only; on macOS the window server is up whenever a user is logged in
# and there is no xdpyinfo to ask.
if [[ "${WAIT_FOR_X_DISPLAY}" == "1" ]]; then
    _DISP="${DISPLAY:-:0}"
    for _i in $(seq 1 30); do
        xdpyinfo -display "${_DISP}" >/dev/null 2>&1 && break
        echo "  → Waiting for display ${_DISP} (${_i}/30)…"
        sleep 2
    done
fi

# ── Launch the banner-Chrome with remote debugging ────────────────────────────
echo "  → Launching banner-Chrome (port ${CDP_PORT})…"
# Chrome's own stderr is noisy and would land in the log doctor.py parses.
"${CHROME}" \
    --remote-debugging-port=${CDP_PORT} \
    --user-data-dir="${TEMP_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    "${CHROME_PLATFORM_FLAGS[@]}" \
    "${PROFILE_URL}" \
    >/dev/null 2>&1 &

CHROME_PID=$!
# A missing binary or a profile Chrome refuses to open dies within the second,
# and reporting "launched" for a process that is already gone would send the
# uploader into a CDP timeout with no explanation.
sleep 1
if ! kill -0 "${CHROME_PID}" 2>/dev/null; then
    echo "  ✗ Chrome exited immediately — check ${CHROME}"
    exit 1
fi
echo "  ✓ Chrome launched (PID ${CHROME_PID})."
echo "  → Wait ~10 s for LinkedIn to load, then run: python3 upload_local.py"
