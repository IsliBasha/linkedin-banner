#!/usr/bin/env bash
# Install (or re-install) the daily banner-upload LaunchAgent.
#
# Idempotent: re-running it replaces the installed plist and re-bootstraps the
# job. Safe to run after every pull — that is also how you pick up an edit to
# the tracked plist, since launchd caches what it loaded.
#
# Usage:  launchd/install.sh
set -euo pipefail

LABEL="com.islibasha.linkedin-banner"
NOTIFY_TITLE="LinkedIn Banner Uploader"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACKED_PLIST="${REPO_DIR}/launchd/${LABEL}.plist"
AGENT_DIR="${HOME}/Library/LaunchAgents"
INSTALLED_PLIST="${AGENT_DIR}/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "✗  launchd is macOS-only. On Linux use systemd/ instead." >&2
    exit 1
fi

if [[ ! -f "${TRACKED_PLIST}" ]]; then
    echo "✗  ${TRACKED_PLIST} not found." >&2
    exit 1
fi

# The plist hardcodes absolute paths (launchd expands no tilde and reads no
# shell), so a checkout somewhere else would silently schedule a DIFFERENT copy
# of this repo. -F because a path is a literal, not a pattern.
if ! grep -qF "<string>${REPO_DIR}</string>" "${TRACKED_PLIST}"; then
    echo "✗  ${TRACKED_PLIST} does not point at this checkout (${REPO_DIR})." >&2
    echo "   Edit its WorkingDirectory and ProgramArguments paths first." >&2
    exit 1
fi

# macOS TCC withholds ~/Documents, ~/Desktop and ~/Downloads from a LaunchAgent:
# a run from there dies on the first read with "Operation not permitted"
# (measured 2026-09-01 through a throwaway agent).
case "${REPO_DIR}" in
    "${HOME}/Documents"/*|"${HOME}/Desktop"/*|"${HOME}/Downloads"/*)
        echo "✗  ${REPO_DIR} is inside a TCC-protected folder — a LaunchAgent cannot read it." >&2
        echo "   Move the checkout somewhere like ${HOME}/src/linkedin-banner and re-run." >&2
        exit 1
        ;;
esac

if [[ ! -x "${REPO_DIR}/.venv/bin/python3" ]]; then
    echo "✗  ${REPO_DIR}/.venv/bin/python3 is missing — create the venv first:" >&2
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

plutil -lint "${TRACKED_PLIST}"

# ── Install ───────────────────────────────────────────────────────────────────
mkdir -p "${AGENT_DIR}"
# A COPY, never a symlink: launchd does not reliably reload a symlinked plist,
# and a stale load is invisible until the job runs the wrong thing. doctor.py's
# launchd_parity check is what catches the copy drifting from the repo.
# Removed first so cp cannot write through a symlink someone left behind.
rm -f "${INSTALLED_PLIST}"
cp "${TRACKED_PLIST}" "${INSTALLED_PLIST}"
chmod 644 "${INSTALLED_PLIST}"
chmod +x "${REPO_DIR}/launchd/run_scheduled.sh" "${REPO_DIR}/launch_chrome_for_upload.sh"

# bootout first so re-running picks up plist edits; it fails when nothing is
# loaded yet, which is the normal first-install case.
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
if ! launchctl bootstrap "${DOMAIN}" "${INSTALLED_PLIST}"; then
    # bootout is asynchronous: the old job can still hold the label for a
    # moment, and bootstrap then fails with "service already loaded".
    echo "  ⚠  bootstrap failed — retrying in 2 s…"
    sleep 2
    launchctl bootstrap "${DOMAIN}" "${INSTALLED_PLIST}"
fi

# ── One-click manual trigger on the Desktop ───────────────────────────────────
if [[ -d "${HOME}/Desktop" ]]; then
    ln -sfn "${REPO_DIR}/run_upload_now.command" "${HOME}/Desktop/Upload LinkedIn Banner.command"
    echo "  ✓ Desktop shortcut → run_upload_now.command"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo "  ✓ Installed ${INSTALLED_PLIST}"
echo "  ✓ Bootstrapped ${DOMAIN}/${LABEL}"
echo
launchctl print "${DOMAIN}/${LABEL}" \
    | grep -E '^[[:space:]]*(state|path|last exit code|runs) =' || true

# The failure notification is the only thing that tells you a scheduled run
# died, so prove the channel works now rather than on the first bad night.
# Same argv construction the wrapper uses.
echo
# `if`, not `|| true`: set -e must not abort here, but a notifier that failed
# is exactly the thing this step exists to surface.
if osascript \
    -e 'on run {message_text, heading}' \
    -e 'display notification message_text with title heading' \
    -e 'end run' \
    -- "Installed — daily upload at 21:00. This is a test notification." \
       "${NOTIFY_TITLE}" >/dev/null; then
    echo "  ✓ test notification posted (osascript exit 0)"
else
    echo "  ⚠  test notification FAILED (osascript exit $?)"
fi
echo "  → If no notification appeared, enable \"Script Editor\" under"
echo "    System Settings → Notifications, then re-run this script."
echo
echo "Next fire: 21:00 local. Verify with: .venv/bin/python3 doctor.py"
