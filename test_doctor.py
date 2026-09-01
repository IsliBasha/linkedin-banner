"""
Tests for doctor.py's macOS/launchd checks.

Driven by the 2026-09-01 port: the Linux box that ran the daily upload under a
systemd timer is retired, so every scheduler-side check had to grow a launchd
twin. The Linux versions leaned on systemctl/journalctl, which do not exist
here; these tests pin the pure parsing and comparison functions those twins are
built from, so the checks can be trusted without a live launchd job.
"""

from __future__ import annotations

import platform
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import doctor as doc


# ── launchctl print parsing ───────────────────────────────────────────────────
# Real output shape of `launchctl print gui/501/com.islibasha.linkedin-banner`:
# an indented "key = value" block. A job that was never bootstrapped makes
# launchctl exit non-zero with nothing useful on stdout.

LOADED_PRINT = """com.islibasha.linkedin-banner = {
	active count = 0
	path = /Users/islibasha/Library/LaunchAgents/com.islibasha.linkedin-banner.plist
	type = LaunchAgent
	state = not running

	program = /bin/bash
	last exit code = 0

	runs = 3
}
"""


def test_parse_launchctl_print_reads_state_and_exit_code_of_a_loaded_job():
    job = doc.parse_launchctl_print(LOADED_PRINT, returncode=0)

    assert job.loaded is True
    assert job.state == "not running"
    assert job.last_exit_code == 0


def test_parse_launchctl_print_reads_a_failing_exit_code():
    job = doc.parse_launchctl_print(
        LOADED_PRINT.replace("last exit code = 0", "last exit code = 1"), returncode=0
    )

    assert job.last_exit_code == 1


def test_parse_launchctl_print_treats_nonzero_return_as_not_loaded():
    job = doc.parse_launchctl_print("", returncode=113)

    assert job.loaded is False
    assert job.last_exit_code is None


def test_parse_launchctl_print_survives_a_job_that_never_ran():
    # launchctl prints this literal placeholder until the first invocation.
    job = doc.parse_launchctl_print(
        LOADED_PRINT.replace("last exit code = 0", "last exit code = (never exited)"),
        returncode=0,
    )

    assert job.loaded is True
    assert job.last_exit_code is None


# ── plist parity (tracked repo copy vs installed copy) ────────────────────────
# install.sh copies the plist into ~/Library/LaunchAgents rather than symlinking
# it (launchd does not reliably reload symlinked plists), so the installed copy
# CAN drift from the repo — which is exactly what this check exists to catch.

PLIST_BODY = '<plist version="1.0"><dict><key>Label</key><string>x</string></dict></plist>\n'


def test_launchd_parity_passes_when_installed_copy_matches_repo(tmp_path):
    tracked = tmp_path / "tracked.plist"
    installed = tmp_path / "installed.plist"
    tracked.write_text(PLIST_BODY)
    installed.write_text(PLIST_BODY)

    result = doc.check_launchd_parity(tracked, installed)

    assert result.status is doc.Status.PASS


def test_launchd_parity_fails_and_shows_the_diff_when_installed_copy_drifted(tmp_path):
    tracked = tmp_path / "tracked.plist"
    installed = tmp_path / "installed.plist"
    tracked.write_text(PLIST_BODY)
    installed.write_text(PLIST_BODY.replace("<string>x</string>", "<string>y</string>"))

    result = doc.check_launchd_parity(tracked, installed)

    assert result.status is doc.Status.FAIL
    assert "<string>y</string>" in result.detail


def test_launchd_parity_fails_when_the_agent_is_not_installed_at_all(tmp_path):
    tracked = tmp_path / "tracked.plist"
    tracked.write_text(PLIST_BODY)

    result = doc.check_launchd_parity(tracked, tmp_path / "absent.plist")

    assert result.status is doc.Status.FAIL
    assert "install.sh" in result.detail


def test_launchd_parity_warns_when_the_installed_plist_is_a_symlink(tmp_path):
    tracked = tmp_path / "tracked.plist"
    tracked.write_text(PLIST_BODY)
    installed = tmp_path / "installed.plist"
    installed.symlink_to(tracked)

    result = doc.check_launchd_parity(tracked, installed)

    assert result.status is doc.Status.WARN


# ── li_at in the dedicated banner-Chrome profile ──────────────────────────────
# On macOS the dedicated profile IS the persistent LinkedIn session (there is no
# regular Chrome to copy cookies from), so its Cookies DB is what doctor reads.
# Chrome stores expiry as microseconds since 1601-01-01 UTC.

CHROME_EPOCH_OFFSET_S = 11644473600


def chrome_timestamp(when: datetime) -> int:
    return int((when.timestamp() + CHROME_EPOCH_OFFSET_S) * 1_000_000)


def make_cookies_db(path: Path, rows: list[tuple[str, str, int]]) -> Path:
    """Minimal stand-in for Chrome's Cookies DB.

    Only the three columns doctor reads are recreated; the real table carries
    ~15 more (value / encrypted_value / samesite / …) that no check touches.
    """
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies (host_key TEXT NOT NULL, name TEXT NOT NULL, "
        "expires_utc INTEGER NOT NULL)"
    )
    con.executemany("INSERT INTO cookies VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_chrome_time_to_datetime_converts_1601_epoch_microseconds_to_utc():
    expiry = datetime(2027, 3, 4, 12, 30, tzinfo=timezone.utc)

    assert doc.chrome_time_to_datetime(chrome_timestamp(expiry)) == expiry


def test_chrome_time_to_datetime_reads_zero_as_a_session_cookie():
    assert doc.chrome_time_to_datetime(0) is None


def test_find_li_at_cookie_returns_the_linkedin_row(tmp_path):
    expiry = datetime(2027, 3, 4, 12, 30, tzinfo=timezone.utc)
    db = make_cookies_db(tmp_path / "Cookies", [
        (".example.com", "session", chrome_timestamp(expiry)),
        (".linkedin.com", "li_at", chrome_timestamp(expiry)),
    ])

    cookie = doc.find_li_at_cookie(db)

    assert cookie is not None
    assert cookie.host_key == ".linkedin.com"
    assert cookie.expires == expiry


def test_find_li_at_cookie_ignores_an_li_at_set_by_another_host(tmp_path):
    db = make_cookies_db(tmp_path / "Cookies", [
        (".notlinkedin.example", "li_at", chrome_timestamp(datetime.now(timezone.utc))),
    ])

    assert doc.find_li_at_cookie(db) is None


def test_find_li_at_cookie_returns_none_when_the_profile_has_no_cookies_db(tmp_path):
    assert doc.find_li_at_cookie(tmp_path / "never-launched" / "Cookies") is None


def test_banner_chrome_session_passes_on_a_live_li_at(tmp_path):
    now = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    db = make_cookies_db(tmp_path / "Cookies", [
        (".linkedin.com", "li_at", chrome_timestamp(now + timedelta(days=300))),
    ])

    result = doc.check_banner_chrome_session(db, now=now)

    assert result.status is doc.Status.PASS
    assert "300" in result.detail  # days left, so a fading session shows up early


def test_banner_chrome_session_fails_on_an_expired_li_at(tmp_path):
    now = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    db = make_cookies_db(tmp_path / "Cookies", [
        (".linkedin.com", "li_at", chrome_timestamp(now - timedelta(days=2))),
    ])

    result = doc.check_banner_chrome_session(db, now=now)

    assert result.status is doc.Status.FAIL
    assert "expired" in result.detail


def test_banner_chrome_session_fails_when_nobody_ever_logged_in(tmp_path):
    db = make_cookies_db(tmp_path / "Cookies", [])

    result = doc.check_banner_chrome_session(db, now=datetime.now(timezone.utc))

    assert result.status is doc.Status.FAIL
    assert "li_at" in result.detail


# ── recent-failure count from the run log ─────────────────────────────────────
# The Linux check counted journalctl lines. There is no journal on macOS, so
# run_scheduled.sh writes its own dated markers and doctor counts those.

def run_log(entries: list[tuple[str, int]]) -> str:
    lines: list[str] = []
    for stamp, code in entries:
        lines.append(f"[{stamp}] run start")
        lines.append("  → Pulling latest banner from GitHub…")
        lines.append(f"[{stamp}] run finish exit={code}")
    return "\n".join(lines) + "\n"


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S%z")


def test_parse_run_outcomes_reads_dated_finish_markers():
    text = run_log([("2026-08-30T21:00:04+0200", 0), ("2026-08-31T21:00:03+0200", 1)])

    outcomes = doc.parse_run_outcomes(text)

    assert [o.exit_code for o in outcomes] == [0, 1]
    assert outcomes[0].finished_at.year == 2026


def test_parse_run_outcomes_ignores_uploader_output_and_undated_markers():
    text = "✗  banner.png is identical to the last uploaded banner\n[nonsense] run finish exit=2\n"

    assert doc.parse_run_outcomes(text) == []


def test_count_recent_failures_counts_only_nonzero_exits():
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    outcomes = [
        doc.RunOutcome(now - timedelta(days=1), 1),
        doc.RunOutcome(now - timedelta(days=2), 0),
        doc.RunOutcome(now - timedelta(days=3), 1),
    ]

    assert doc.count_recent_failures(outcomes, now=now, window_days=14) == 2


def test_count_recent_failures_drops_runs_older_than_the_window():
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    outcomes = [
        doc.RunOutcome(now - timedelta(days=15), 1),
        doc.RunOutcome(now - timedelta(days=13, hours=23), 1),
    ]

    assert doc.count_recent_failures(outcomes, now=now, window_days=14) == 1


def test_launchd_recent_failures_warns_below_three_and_fails_at_three(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"

    log.write_text(run_log([(stamp(now - timedelta(days=1)), 1)]))
    assert doc.check_launchd_recent_failures(log, now=now).status is doc.Status.WARN

    log.write_text(run_log([(stamp(now - timedelta(days=d)), 1) for d in (1, 2, 3)]))
    assert doc.check_launchd_recent_failures(log, now=now).status is doc.Status.FAIL


def test_launchd_recent_failures_passes_on_a_clean_log(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(run_log([(stamp(now - timedelta(days=1)), 0)]))

    assert doc.check_launchd_recent_failures(log, now=now).status is doc.Status.PASS


def test_launchd_recent_failures_warns_when_the_log_does_not_exist_yet(tmp_path):
    result = doc.check_launchd_recent_failures(tmp_path / "absent.log")

    assert result.status is doc.Status.WARN


# ── CDP port owner (BSD lsof, no GNU flags) ───────────────────────────────────
# The Linux version piped lsof into `ps --no-headers` via `xargs -r`; neither
# flag exists in the BSD tools shipped with macOS, so the owner is parsed here.

LSOF_OUTPUT = """COMMAND     PID      USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
Google  40213 islibasha   30u  IPv4 0x4a2b1c9d8e7f6a5b      0t0  TCP 127.0.0.1:9222 (LISTEN)
"""


def test_parse_lsof_listeners_reports_command_and_pid():
    assert doc.parse_lsof_listeners(LSOF_OUTPUT) == ["Google (pid 40213)"]


def test_parse_lsof_listeners_returns_nothing_for_a_header_only_result():
    assert doc.parse_lsof_listeners("") == []
    assert doc.parse_lsof_listeners(LSOF_OUTPUT.splitlines()[0] + "\n") == []


def test_parse_lsof_listeners_reports_a_dual_stack_listener_once():
    # Verified against real BSD lsof on this Mac: one row per socket, so a
    # process bound to both IPv4 and IPv6 shows up twice.
    dual_stack = LSOF_OUTPUT + LSOF_OUTPUT.splitlines()[1].replace("IPv4", "IPv6") + "\n"

    assert doc.parse_lsof_listeners(dual_stack) == ["Google (pid 40213)"]


# ── platform-selected check list ──────────────────────────────────────────────

def test_darwin_runs_launchd_checks_and_no_systemd_checks():
    names = [c.__name__ for c in doc.checks_for_platform("Darwin")]

    assert "check_launchd_parity" in names
    assert "check_systemd_parity" not in names
    assert "check_network" in names


def test_linux_keeps_the_original_systemd_checks():
    names = [c.__name__ for c in doc.checks_for_platform("Linux")]

    assert "check_systemd_parity" in names
    assert "check_timer_state" in names
    assert "check_launchd_parity" not in names


# ── cloud upload path is a documented non-goal ────────────────────────────────

def test_github_secret_age_never_fails_because_the_cloud_upload_is_a_non_goal(monkeypatch):
    # The upload-banner job is workflow_dispatch-only (non-goal since
    # 2026-07-15), so a stale LINKEDIN_COOKIES secret cannot break the daily
    # pipeline and must not turn the whole report red.
    stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat().replace("+00:00", "Z")
    payload = '{"name": "LINKEDIN_COOKIES", "updated_at": "%s"}' % stale
    monkeypatch.setattr(
        doc, "_run",
        lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, 0, payload, ""),
    )

    result = doc.check_github_secret_age()

    assert result.status is doc.Status.WARN
    assert "workflow_dispatch" in result.detail


# ── Staleness, orphaned runs and bounded log reads ────────────────────────────
# A daily job that stops firing produces a log that simply stops growing. The
# failure count alone reads that as PASS, which is how a dead agent could look
# healthy indefinitely.

def test_recent_failures_fails_when_the_last_run_is_older_than_36h(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(run_log([(stamp(now - timedelta(days=30)), 0)]))

    result = doc.check_launchd_recent_failures(log, now=now)

    assert result.status is doc.Status.FAIL
    assert "has not fired since" in result.detail


def test_recent_failures_still_passes_a_run_from_last_night(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(run_log([(stamp(now - timedelta(hours=25)), 0)]))

    assert doc.check_launchd_recent_failures(log, now=now).status is doc.Status.PASS


def test_orphaned_run_start_counts_as_a_failure():
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    killed = now - timedelta(days=1)
    text = f"[{stamp(killed)}] run start\n"           # hard kill: no finish line

    orphans = doc.find_orphaned_starts(text, now)

    assert orphans == [killed]


def test_a_run_still_inside_its_wall_clock_budget_is_not_orphaned():
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    text = f"[{stamp(now - timedelta(minutes=40))}] run start\n"

    assert doc.find_orphaned_starts(text, now) == []


def test_a_start_followed_by_another_start_is_orphaned():
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    killed = now - timedelta(days=2)
    text = (f"[{stamp(killed)}] run start\n"
            f"[{stamp(now - timedelta(days=1))}] run start\n"
            f"[{stamp(now - timedelta(days=1))}] run finish exit=0\n")

    assert doc.find_orphaned_starts(text, now) == [killed]


def test_recent_failures_reports_an_orphaned_run(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(
        f"[{stamp(now - timedelta(hours=20))}] run start\n"
        f"[{stamp(now - timedelta(hours=20))}] run finish exit=0\n"
        f"[{stamp(now - timedelta(hours=10))}] run start\n"        # never finished
        f"[{stamp(now - timedelta(hours=5))}] run start\n"
        f"[{stamp(now - timedelta(hours=4))}] run finish exit=0\n"
    )

    result = doc.check_launchd_recent_failures(log, now=now)

    assert result.status is doc.Status.WARN
    assert "1 failure" in result.detail


def test_a_marker_sharing_a_line_with_unterminated_output_is_still_read():
    # Python's stdout is block-buffered into the log; a run that dies mid-write
    # leaves no trailing newline, so the next marker shares that line.
    text = "  → Pulling latest banner[2026-08-31T21:00:03+0200] run finish exit=1\n"

    assert [o.exit_code for o in doc.parse_run_outcomes(text)] == [1]


def test_read_log_tail_reads_only_the_last_megabyte(tmp_path):
    log = tmp_path / "banner.log"
    log.write_text("OLD-HEAD\n" + ("x" * 1_200_000) + "\nNEW-TAIL\n")

    tail = doc.read_log_tail(log)

    assert len(tail) <= doc.LOG_TAIL_BYTES
    assert "NEW-TAIL" in tail
    assert "OLD-HEAD" not in tail


def test_recent_failures_reads_a_huge_log_from_its_tail(tmp_path):
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(
        run_log([(stamp(now - timedelta(days=400)), 1)])      # scrolled out
        + ("padding line\n" * 90_000)
        + run_log([(stamp(now - timedelta(hours=3)), 0)])
    )

    result = doc.check_launchd_recent_failures(log, now=now)

    assert result.status is doc.Status.PASS


# ── launchctl exit codes that are not numbers ─────────────────────────────────

def test_a_job_killed_by_a_signal_warns_with_the_raw_text():
    job = doc.parse_launchctl_print(
        LOADED_PRINT.replace("last exit code = 0", "last exit code = (uncaught signal 9)"),
        returncode=0,
    )

    result = doc.describe_last_exit(job)

    assert job.last_exit_code is None
    assert result.status is doc.Status.WARN
    assert "uncaught signal 9" in result.detail


def test_a_job_that_never_ran_warns_about_the_first_fire():
    job = doc.parse_launchctl_print(
        LOADED_PRINT.replace("last exit code = 0", "last exit code = (never exited)"),
        returncode=0,
    )

    result = doc.describe_last_exit(job)

    assert result.status is doc.Status.WARN
    assert "never run yet" in result.detail


# ── Cookie DB: unreadable is not the same as logged out ───────────────────────

def test_an_unreadable_cookies_db_is_reported_as_unreadable_not_logged_out(tmp_path):
    db = tmp_path / "Cookies"
    db.write_bytes(b"this is not a sqlite database")

    outcome = doc.find_li_at_cookie(db)

    assert isinstance(outcome, doc.CookieDbUnreadable)
    assert doc.check_banner_chrome_session(db).status is doc.Status.WARN


def test_a_lookalike_domain_does_not_satisfy_the_session_check(tmp_path):
    now = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    db = make_cookies_db(tmp_path / "Cookies", [
        (".evil-linkedin.com", "li_at", chrome_timestamp(now + timedelta(days=300))),
    ])

    assert doc.find_li_at_cookie(db) is None
    assert doc.check_banner_chrome_session(db, now=now).status is doc.Status.FAIL


def test_a_www_subdomain_li_at_is_accepted(tmp_path):
    now = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc)
    db = make_cookies_db(tmp_path / "Cookies", [
        ("www.linkedin.com", "li_at", chrome_timestamp(now + timedelta(days=300))),
    ])

    cookie = doc.find_li_at_cookie(db)

    assert isinstance(cookie, doc.LiAtCookie)
    assert cookie.host_key == "www.linkedin.com"


# ── The notifier must treat a log line as data, never as AppleScript ──────────
# run_scheduled.sh feeds the last ✗ line of the log into osascript. That line
# can contain anything upload_local.py or a LinkedIn error page produced, so
# the argv construction below is load-bearing: interpolating the same text into
# the -e script body would make `do shell script` reachable from a log line.

OSASCRIPT_NOTIFY_ARGV = [
    "osascript",
    "-e", "on run {message_text, heading}",
    "-e", "display notification message_text with title heading",
    "-e", "end run",
    "--",
]
WRAPPER_PATH = Path(__file__).parent / "launchd" / "run_scheduled.sh"


@pytest.mark.skipif(platform.system() != "Darwin", reason="osascript is macOS-only")
def test_a_hostile_log_line_cannot_reach_do_shell_script(tmp_path):
    # Posts one real notification (that is the construction under test).
    marker = tmp_path / "PWNED"
    payload = f'" & (do shell script "touch {marker}") & "'

    subprocess.run(
        OSASCRIPT_NOTIFY_ARGV + [payload, "LinkedIn Banner Uploader"],
        capture_output=True, text=True, timeout=30,
    )

    assert not marker.exists(), "log text reached the shell through osascript"


def test_the_wrapper_still_uses_that_exact_argv_construction():
    wrapper = WRAPPER_PATH.read_text()

    for fragment in OSASCRIPT_NOTIFY_ARGV[2::2]:
        assert f"-e '{fragment}'" in wrapper


def test_staleness_names_unfinished_runs_when_starts_were_orphaned(tmp_path):
    # A scheduler that fires but whose runs die reads differently from one that
    # stopped firing, and the fix differs too.
    now = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    log = tmp_path / "banner.log"
    log.write_text(
        run_log([(stamp(now - timedelta(days=5)), 0)])
        + f"[{stamp(now - timedelta(days=2))}] run start\n"
        + f"[{stamp(now - timedelta(days=1))}] run start\n"
    )

    result = doc.check_launchd_recent_failures(log, now=now)

    assert result.status is doc.Status.FAIL
    assert "no run has finished since" in result.detail
    assert "2 run(s) started without finishing" in result.detail
