#!/usr/bin/env bash
# Finder double-click entry point: .command opens in Terminal, .sh does not.
# Resolve through the symlink install.sh drops on the Desktop — there $0 is the
# symlink, and its dirname is the Desktop, where run_upload_now.sh is not.
cd "$(dirname "$(readlink "$0" || echo "$0")")" || exit 1
exec ./run_upload_now.sh
