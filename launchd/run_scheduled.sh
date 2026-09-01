#!/usr/bin/env bash
# Scheduled daily upload, invoked by com.islibasha.linkedin-banner.plist.
#
# Runs the sequence the systemd service ran as three Exec directives:
#   1. launch / verify the dedicated CDP Chrome
#   2. give LinkedIn 12 s to finish loading in it
#   3. pull the day's banner and upload it
#
# Four things the plist cannot express are done here:
#   * dated run markers, so doctor.py can count failures over a window —
#     journalctl did that on Linux and macOS has no equivalent journal;
#   * a wall-clock cap, the TimeoutStartSec=8400 the systemd unit had. The
#     uploader's own poll budget bounds nothing on a laptop: it is measured
#     with time.monotonic(), which stops while the Mac sleeps, so a run whose
#     lid closes mid-poll can live for days while launchd skips every 21:00;
#   * a desktop notification on failure, the launchd stand-in for the service's
#     OnFailure=linkedin-banner-notify-failure.service. Without it a failed run
#     is a silent line in a log nobody reads (that is exactly how the Jul 13-15
#     2026 failures went unnoticed for three days);
#   * stopping the banner-Chrome afterwards. AbandonProcessGroup=true keeps
#     launchd from SIGKILLing it, so the polite shutdown is ours to send.
#
# stdout and stderr are appended to the log by the plist; run it by hand only
# if you want that output on your terminal instead.

# Derived, not hardcoded: a moved clone must run ITS OWN wrapper, not a stale
# copy of this one somewhere else.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${HOME}/.linkedin_banner.log"
# Must match TEMP_DIR in launch_chrome_for_upload.sh.
BANNER_PROFILE_DIR="${HOME}/.config/linkedin-banner-chrome"
NOTIFY_TITLE="LinkedIn Banner Uploader"
CHROME_LOAD_WAIT_S=12
CHROME_STOP_WAIT_S=20
# 5 s, not 30: bash services a trap only once the foreground command it is
# sitting in returns, so this interval IS the worst-case delay before the
# TERM/INT/HUP handler writes its marker. Measured at 29 s with a 30 s sleep —
# longer than the grace launchd gives at logout before SIGKILL, which would
# lose exactly the marker that handler exists to write.
BUDGET_CHECK_INTERVAL_S=5
# The budget systemd enforced with TimeoutStartSec=8400 (2 h 20 min): the
# uploader's 2 h poll, its git-pull retries, and the upload itself.
RUN_BUDGET_S="${RUN_BUDGET_S:-8400}"

# Keep the scheduled semantics even when this wrapper is started by hand: wait
# for GitHub's late generation cron rather than uploading yesterday's banner.
export POLL_FOR_NEW_BANNER="${POLL_FOR_NEW_BANNER:-1}"

# Bytes already in the log when this run started, so a notification quotes THIS
# run's error and never a leftover from days ago.
START_OFFSET=0

# PID of the backgrounded uploader, global so the signal handler can reach it.
UPLOADER_PID=""

timestamp() { date "+%Y-%m-%dT%H:%M:%S%z"; }

notify_failure() {
    local status="$1"
    local last_error
    last_error="$(tail -c "+$((START_OFFSET + 1))" "${LOG_FILE}" 2>/dev/null | grep '✗' | tail -1)"
    # The exit code is always in the body: a run that dies before printing any
    # ✗ (exit 127, a Python traceback) would otherwise notify about nothing.
    local body="${last_error:-Upload failed.} (exit ${status})"
    # Arguments reach AppleScript as argv rather than interpolated into the
    # script text: log lines carry quotes and Unicode that would otherwise
    # break the -e string, or worse be read as AppleScript.
    osascript \
        -e 'on run {message_text, heading}' \
        -e 'display notification message_text with title heading' \
        -e 'end run' \
        -- "${body}" "${NOTIFY_TITLE}" >/dev/null
    local notify_status=$?
    if [ "${notify_status}" -eq 0 ]; then
        echo "[$(timestamp)] failure notification posted (osascript exit ${notify_status})"
    else
        echo "[$(timestamp)] failure notification FAILED (osascript exit ${notify_status})"
    fi
}

# A run killed by logout, shutdown or a manual kill must still leave a marker;
# systemd's OnFailure= fired on signal death too.
# shellcheck disable=SC2329  # reached through the trap handlers below
on_signal() {
    local signal_name="$1"
    # Take the uploader with us. It runs in the background, so without this it
    # survives reparented to launchd - and AbandonProcessGroup=true means
    # nothing else will ever stop it. It would keep uploading after the log
    # said the run died, outlive the wall-clock cap, and still be holding the
    # CDP endpoint when the next 21:00 starts a second one.
    if [ -n "${UPLOADER_PID}" ]; then
        kill -TERM "${UPLOADER_PID}" 2>/dev/null
        sleep 2
        kill -KILL "${UPLOADER_PID}" 2>/dev/null
    fi
    echo "✗  run killed by SIG${signal_name} (logout, shutdown or manual kill)"
    echo "[$(timestamp)] run finish exit=143"
    notify_failure 143
    exit 143
}
trap 'on_signal TERM' TERM
trap 'on_signal INT' INT
trap 'on_signal HUP' HUP

stop_banner_chrome() {
    pkill -TERM -f -- "--user-data-dir=${BANNER_PROFILE_DIR}" 2>/dev/null
    local waited=0
    while pgrep -f -- "--user-data-dir=${BANNER_PROFILE_DIR}" >/dev/null 2>&1; do
        if [ "${waited}" -ge "${CHROME_STOP_WAIT_S}" ]; then
            echo "⚠  banner-Chrome ignored SIGTERM for ${CHROME_STOP_WAIT_S} s — left running"
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

# Runs the uploader under a wall-clock cap. macOS ships no GNU timeout, and
# `date +%s` is the point: unlike time.monotonic() it keeps counting while the
# machine sleeps, which is the whole failure this cap exists for.
run_uploader_with_budget() {
    "${REPO_DIR}/.venv/bin/python3" "${REPO_DIR}/upload_local.py" &
    UPLOADER_PID=$!
    local deadline=$(( $(date +%s) + RUN_BUDGET_S ))

    while kill -0 "${UPLOADER_PID}" 2>/dev/null; do
        if [ "$(date +%s)" -ge "${deadline}" ]; then
            echo "✗  run exceeded ${RUN_BUDGET_S} s wall clock (Mac asleep mid-poll?) — killed"
            kill -TERM "${UPLOADER_PID}" 2>/dev/null
            sleep 5
            kill -KILL "${UPLOADER_PID}" 2>/dev/null
            wait "${UPLOADER_PID}" 2>/dev/null
            UPLOADER_PID=""
            return 124
        fi
        sleep "${BUDGET_CHECK_INTERVAL_S}"
    done

    wait "${UPLOADER_PID}"
    local uploader_status=$?
    # Cleared so a later signal cannot signal a recycled PID.
    UPLOADER_PID=""
    return "${uploader_status}"
}

echo "[$(timestamp)] run start"
START_OFFSET=$(wc -c 2>/dev/null < "${LOG_FILE}" || echo 0)

# The plist already sets WorkingDirectory; this covers a manual invocation.
# The marker line stays exact — doctor.py counts failures by matching it, so a
# reason appended to it would make this failure invisible.
if ! cd "${REPO_DIR}"; then
    echo "✗  Repo not found at ${REPO_DIR} — nothing to upload."
    echo "[$(timestamp)] run finish exit=1"
    notify_failure 1
    exit 1
fi

# Every SCHEDULED run starts from a fresh browser. A failed run deliberately
# leaves its window open for inspection, but the launcher's fast path would
# then reuse that same window every night, wedged or not - systemd never
# carried Chrome into the next run. The session lives in the profile on disk,
# so a login done during the day survives this restart. Interactive runs
# (run_upload_now.sh) go through the launcher directly and still reuse an
# already-open window.
if pgrep -f -- "--user-data-dir=${BANNER_PROFILE_DIR}" >/dev/null 2>&1; then
    # Only claim it if it actually went; stop_banner_chrome logs its own
    # warning when Chrome ignored the signal, and both lines would contradict.
    if stop_banner_chrome; then
        echo "[$(timestamp)] stopped leftover banner-Chrome from a previous run"
    fi
fi

# Not `set -e`: the launcher's status is inspected here, so a failed launch is
# reported the way systemd reported a failed ExecStartPre.
"${REPO_DIR}/launch_chrome_for_upload.sh"
LAUNCHER_STATUS=$?
if [ "${LAUNCHER_STATUS}" -ne 0 ]; then
    echo "✗  launcher failed (exit ${LAUNCHER_STATUS}) — upload skipped"
    echo "[$(timestamp)] run finish exit=${LAUNCHER_STATUS}"
    notify_failure "${LAUNCHER_STATUS}"
    exit "${LAUNCHER_STATUS}"
fi

sleep "${CHROME_LOAD_WAIT_S}"

run_uploader_with_budget
STATUS=$?

# The outcome is decided; from here a signal must not rewrite it. A TERM
# arriving during the (up to 20 s) Chrome stop used to turn a finished,
# successful upload into "killed by SIGTERM", exit=143 and a failure
# notification.
trap - TERM INT HUP

# Record the outcome BEFORE the housekeeping below, which can take up to 20 s.
# Disarming restores the default disposition, so a signal in that window ends
# the script - and it must find the marker and the notification already
# written rather than erase a run that actually succeeded.
echo "[$(timestamp)] run finish exit=${STATUS}"

if [ "${STATUS}" -ne 0 ]; then
    notify_failure "${STATUS}"
fi

if [ "${STATUS}" -eq 0 ]; then
    stop_banner_chrome
else
    # The window stays up for a look at what LinkedIn actually showed — and it
    # is the same window the one-time login happens in.
    echo "   banner-Chrome left open for inspection"
fi

exit "${STATUS}"
