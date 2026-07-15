#!/usr/bin/env bash
# Invoked via linkedin-banner.service's OnFailure= — surfaces a failed
# scheduled run as a desktop notification instead of a silent log-file entry.
# Without this, the last three scheduled runs (Jul 13-15) all failed with
# nobody finding out until the log was checked manually.

LOG=~/.linkedin_banner.log
LAST_ERROR="$(grep '✗' "$LOG" 2>/dev/null | tail -1)"

notify-send \
    --urgency=critical \
    --app-name="LinkedIn Banner Uploader" \
    "LinkedIn banner upload failed" \
    "${LAST_ERROR:-Check ~/.linkedin_banner.log for details.}"
