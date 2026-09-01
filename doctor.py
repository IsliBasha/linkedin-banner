#!/usr/bin/env python3
"""
doctor.py — one-command health check for the LinkedIn banner automation
pipeline (Path A: GitHub Actions generation, Path B: the local uploader).

Path B's scheduler is platform-specific: a systemd timer on Linux, a launchd
agent on macOS since the 2026-09-01 port. The scheduler-side checks are picked
by platform (see checks_for_platform); everything else runs on both.

Usage:
    .venv/bin/python3 doctor.py            # human-readable report
    .venv/bin/python3 doctor.py --json     # machine-readable

Exit code: 0 if no FAIL, 1 if any check FAILed (WARN does not affect exit code).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import requests

REPO_DIR = Path(__file__).parent.resolve()
INSTALLED_DIR = Path.home() / ".config/systemd/user"
UNIT_NAMES = ["linkedin-banner.service", "linkedin-banner.timer"]
GH_REPO = "IsliBasha/linkedin-banner"
SECRET_NAME = "LINKEDIN_COOKIES"
CDP_PORT = 9222

# ── launchd (macOS) ───────────────────────────────────────────────────────────
LAUNCHD_LABEL = "com.islibasha.linkedin-banner"
LAUNCHD_PLIST_NAME = f"{LAUNCHD_LABEL}.plist"
LAUNCH_AGENTS_DIR = Path.home() / "Library/LaunchAgents"
# run_scheduled.sh appends its dated markers here (plist StandardOutPath).
RUN_LOG = Path.home() / ".linkedin_banner.log"
# The dedicated banner-Chrome profile. On macOS this profile is the ONLY
# LinkedIn session store: launch_chrome_for_upload.sh copies nothing in from a
# regular Chrome there, so what is logged in here is what uploads.
BANNER_COOKIES_DB = Path.home() / ".config/linkedin-banner-chrome/Default/Cookies"
# Chrome stores cookie expiry as microseconds since 1601-01-01 UTC.
CHROME_EPOCH_OFFSET_S = 11644473600
# The run log is appended to forever; only its tail can still be relevant.
LOG_TAIL_BYTES = 1_048_576
# A daily job that has not finished a run in 36 h has missed at least one slot.
STALE_RUN_AFTER_H = 36
# A start with no finish is only a dead run once the wrapper's own 8400 s
# wall-clock cap has passed; before that the run is simply still going.
ORPHAN_AFTER_H = 3


class Status(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str


def _run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


# ── Checks: systemd scheduler (Linux) ──────────────────────────────────────

def check_systemd_parity() -> list[CheckResult]:
    results = []
    for name in UNIT_NAMES:
        tracked, installed = REPO_DIR / "systemd" / name, INSTALLED_DIR / name
        if not installed.exists():
            results.append(CheckResult(f"systemd_parity:{name}", Status.FAIL,
                f"{installed} does not exist — timer/service not installed"))
            continue
        if not tracked.exists():
            results.append(CheckResult(f"systemd_parity:{name}", Status.WARN,
                f"{tracked} missing from repo — cannot verify parity"))
            continue
        if installed.is_symlink() and installed.resolve() == tracked.resolve():
            results.append(CheckResult(f"systemd_parity:{name}", Status.PASS,
                "installed unit is a symlink to the repo-tracked copy (cannot drift)"))
            continue
        diff = "".join(difflib.unified_diff(
            tracked.read_text().splitlines(keepends=True),
            installed.read_text().splitlines(keepends=True),
            fromfile=str(tracked), tofile=str(installed)))
        if diff:
            results.append(CheckResult(f"systemd_parity:{name}", Status.FAIL,
                f"installed unit has drifted from repo:\n{diff}"))
        else:
            results.append(CheckResult(f"systemd_parity:{name}", Status.PASS,
                "installed unit content matches repo-tracked copy"))
    return results


def check_timer_state() -> CheckResult:
    en = _run(["systemctl", "--user", "is-enabled", "linkedin-banner.timer"]).stdout.strip()
    ac = _run(["systemctl", "--user", "is-active", "linkedin-banner.timer"]).stdout.strip()
    if en == "enabled" and ac == "active":
        return CheckResult("timer_state", Status.PASS, "enabled + active")
    return CheckResult("timer_state", Status.FAIL, f"enabled={en!r} active={ac!r}")


def check_last_run() -> CheckResult:
    props = _run(["systemctl", "--user", "show", "linkedin-banner.service",
                  "-p", "Result,ExecMainStartTimestamp,ExecMainExitTimestamp"]).stdout
    d = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
    result = d.get("Result", "unknown")
    try:
        start = datetime.strptime(d["ExecMainStartTimestamp"], "%a %Y-%m-%d %H:%M:%S %Z")
        end = datetime.strptime(d["ExecMainExitTimestamp"], "%a %Y-%m-%d %H:%M:%S %Z")
        duration = (end - start).total_seconds()
    except (KeyError, ValueError):
        duration = None
    if result == "success":
        detail = f"last run succeeded ({duration:.0f}s)" if duration is not None else "last run succeeded"
        return CheckResult("last_run", Status.PASS, detail)
    if duration is not None and duration < 3:
        return CheckResult("last_run", Status.FAIL,
            f"last run FAILED instantly ({duration:.0f}s) — likely network-not-ready at start, result={result}")
    return CheckResult("last_run", Status.FAIL, f"last run FAILED (result={result})")


def check_recent_failure_count(window_days: int = 14) -> CheckResult:
    # Verified wording from this machine's actual journal: systemd emits
    # "Failed with result 'exit-code'." as the single canonical per-invocation
    # failure marker line (distinct from the more verbose "Control process
    # exited..." line that can repeat per Exec directive).
    proc = _run(["journalctl", "--user", "-u", "linkedin-banner.service",
                 f"--since=-{window_days}d", "-g", "Failed with result", "--no-pager"], timeout=15)
    n = len([line for line in proc.stdout.splitlines() if line.strip()])
    if n == 0:
        return CheckResult("recent_failures", Status.PASS, f"0 failures in last {window_days}d")
    status = Status.FAIL if n >= 3 else Status.WARN
    return CheckResult("recent_failures", status, f"{n} failure(s) in last {window_days}d")


# ── Checks: launchd scheduler (macOS) ──────────────────────────────────────
# The Linux twins above read systemctl properties and the journal. Neither
# exists on macOS, so these read `launchctl print` and the dated markers
# run_scheduled.sh writes to the run log.

@dataclass(frozen=True)
class LaunchdJob:
    loaded: bool
    state: str | None
    last_exit_code: int | None
    last_exit_raw: str | None = None


def parse_launchctl_print(output: str, returncode: int) -> LaunchdJob:
    """Read `launchctl print gui/<uid>/<label>` into the facts we act on.

    launchctl exits non-zero (113, "Could not find service") when the job was
    never bootstrapped — that is the not-installed signal, not an error.
    """
    if returncode != 0:
        return LaunchdJob(loaded=False, state=None, last_exit_code=None)

    def field(key: str) -> str | None:
        match = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)$", output, re.MULTILINE)
        return match.group(1).strip() if match else None

    raw_exit = field("last exit code")
    exit_code = int(raw_exit) if raw_exit and raw_exit.lstrip("-").isdigit() else None
    return LaunchdJob(loaded=True, state=field("state"), last_exit_code=exit_code,
                      last_exit_raw=raw_exit)


def describe_last_exit(job: LaunchdJob) -> CheckResult:
    """How the job's last run ended.

    Only the literal "(never exited)" means never-run. Anything else launchctl
    puts here that is not a number is a real event worth reading — a job killed
    by a signal reports "(uncaught signal 9)" — so it is echoed rather than
    quietly filed under "has not run yet".
    """
    if job.last_exit_code == 0:
        return CheckResult("launchd_last_exit", Status.PASS, "last run exited 0")
    if job.last_exit_code is not None:
        return CheckResult("launchd_last_exit", Status.FAIL,
            f"last run exited {job.last_exit_code} — see {RUN_LOG}")
    if job.last_exit_raw is None or job.last_exit_raw == "(never exited)":
        return CheckResult("launchd_last_exit", Status.WARN,
            "job has never run yet — first fire is the next 21:00")
    return CheckResult("launchd_last_exit", Status.WARN,
        f"launchctl reports last exit as {job.last_exit_raw} — see {RUN_LOG}")


def check_launchd_parity(tracked: Path | None = None,
                         installed: Path | None = None) -> CheckResult:
    """Compare the repo-tracked plist with the one launchd actually loads."""
    tracked = tracked if tracked is not None else REPO_DIR / "launchd" / LAUNCHD_PLIST_NAME
    installed = installed if installed is not None else LAUNCH_AGENTS_DIR / LAUNCHD_PLIST_NAME

    if not installed.exists():
        return CheckResult("launchd_parity", Status.FAIL,
            f"{installed} does not exist — run launchd/install.sh to install the agent")
    if not tracked.exists():
        return CheckResult("launchd_parity", Status.WARN,
            f"{tracked} missing from repo — cannot verify parity")
    if installed.is_symlink():
        return CheckResult("launchd_parity", Status.WARN,
            f"{installed} is a symlink — launchd does not reliably reload symlinked "
            "plists; re-run launchd/install.sh to replace it with a copy")

    diff = "".join(difflib.unified_diff(
        tracked.read_text().splitlines(keepends=True),
        installed.read_text().splitlines(keepends=True),
        fromfile=str(tracked), tofile=str(installed)))
    if diff:
        return CheckResult("launchd_parity", Status.FAIL,
            f"installed plist has drifted from repo:\n{diff}")
    return CheckResult("launchd_parity", Status.PASS,
        "installed plist content matches repo-tracked copy")


def check_launchd_job() -> list[CheckResult]:
    """Is the agent bootstrapped, and how did its last run end?"""
    domain_target = f"gui/{os.getuid()}/{LAUNCHD_LABEL}"
    proc = _run(["launchctl", "print", domain_target])
    job = parse_launchctl_print(proc.stdout, proc.returncode)

    if not job.loaded:
        return [CheckResult("launchd_job_state", Status.FAIL,
            f"{domain_target} not loaded — run launchd/install.sh")]

    return [
        CheckResult("launchd_job_state", Status.PASS,
            f"loaded in gui/{os.getuid()} (state={job.state or 'unknown'})"),
        describe_last_exit(job),
    ]


@dataclass(frozen=True)
class RunOutcome:
    finished_at: datetime
    exit_code: int


# Not anchored with ^: launchd appends whatever the uploader wrote, and a
# process that ends without a newline leaves the next marker sharing a line.
_RUN_FINISH_RE = re.compile(r"\[(\S+)\]\s+run finish exit=(-?\d+)\s*$")
_RUN_START_RE = re.compile(r"\[(\S+)\]\s+run start\s*$")


def _marker_time(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def read_log_tail(log_path: Path, max_bytes: int = LOG_TAIL_BYTES) -> str:
    """Last max_bytes of the run log, decoded leniently.

    The log is appended to forever and never rotated, so reading all of it
    would grow unbounded — and nothing older than the tail changes a verdict.
    """
    with log_path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")


def parse_run_outcomes(log_text: str) -> list[RunOutcome]:
    """Extract run_scheduled.sh's dated finish markers from the run log.

    Everything else in the log is uploader output and is ignored; a marker
    without a parseable timestamp is dropped rather than counted at "now",
    which would silently inflate the failure window.
    """
    outcomes: list[RunOutcome] = []
    for line in log_text.splitlines():
        match = _RUN_FINISH_RE.search(line)
        if not match:
            continue
        finished_at = _marker_time(match.group(1))
        if finished_at is not None:
            outcomes.append(RunOutcome(finished_at, int(match.group(2))))
    return outcomes


def find_orphaned_starts(log_text: str, now: datetime, *,
                         orphan_after_h: int = ORPHAN_AFTER_H) -> list[datetime]:
    """Run starts that never reached a finish marker.

    The wrapper traps TERM/INT/HUP, so a start with no finish means it died
    without a chance to say so — a hard kill, a panic, a pulled plug. Those
    runs uploaded nothing and are counted as failures. The newest start is
    exempt until the wrapper's own wall-clock cap has passed, otherwise a run
    that is simply still polling would be reported as dead.
    """
    events: list[tuple[datetime, bool]] = []
    for line in log_text.splitlines():
        finish = _RUN_FINISH_RE.search(line)
        start = _RUN_START_RE.search(line)
        if finish is None and start is None:
            continue
        stamp = _marker_time((finish or start).group(1))
        if stamp is not None:
            events.append((stamp, start is not None))

    orphans: list[datetime] = []
    for index, (stamp, is_start) in enumerate(events):
        if not is_start:
            continue
        followed_by_finish = any(not later_is_start
                                 for _, later_is_start in events[index + 1:index + 2])
        if followed_by_finish:
            continue
        is_last = index == len(events) - 1
        if is_last and (now - stamp) < timedelta(hours=orphan_after_h):
            continue          # still running, not dead
        orphans.append(stamp)
    return orphans


def count_recent_failures(outcomes: list[RunOutcome], now: datetime,
                          window_days: int = 14) -> int:
    cutoff = now - timedelta(days=window_days)
    return sum(1 for o in outcomes if o.exit_code != 0 and o.finished_at >= cutoff)


def check_launchd_recent_failures(log_path: Path | None = None, *,
                                  now: datetime | None = None,
                                  window_days: int = 14) -> CheckResult:
    log_path = log_path if log_path is not None else RUN_LOG
    now = now if now is not None else datetime.now(timezone.utc)
    try:
        log_text = read_log_tail(log_path)
    except FileNotFoundError:
        return CheckResult("recent_failures", Status.WARN,
            f"{log_path} does not exist yet — no scheduled run has been logged")

    outcomes = parse_run_outcomes(log_text)
    orphans = find_orphaned_starts(log_text, now)
    if not outcomes and not orphans:
        return CheckResult("recent_failures", Status.WARN,
            f"no run markers in {log_path} — no scheduled run has completed")

    # A silent agent is the failure this check was blind to: counting zero
    # failures in a log whose last entry is a month old used to read as PASS.
    if outcomes:
        newest = max(o.finished_at for o in outcomes)
        idle = now - newest
        if idle > timedelta(hours=STALE_RUN_AFTER_H):
            days, hours = idle.days, idle.seconds // 3600
            if orphans:
                # The agent HAS been firing; those runs died before finishing,
                # which is a different fault from a scheduler that never ran.
                detail = (f"no run has finished since {days}d {hours}h ago — "
                          f"{len(orphans)} run(s) started without finishing")
            else:
                detail = (f"last run finished {days}d {hours}h ago — "
                          "the agent has not fired since")
            return CheckResult("recent_failures", Status.FAIL, detail)

    cutoff = now - timedelta(days=window_days)
    n = (count_recent_failures(outcomes, now, window_days)
         + sum(1 for stamp in orphans if stamp >= cutoff))
    if n == 0:
        return CheckResult("recent_failures", Status.PASS, f"0 failures in last {window_days}d")
    status = Status.FAIL if n >= 3 else Status.WARN
    return CheckResult("recent_failures", status, f"{n} failure(s) in last {window_days}d")


@dataclass(frozen=True)
class LiAtCookie:
    host_key: str
    expires: datetime | None   # None = session cookie (dies with the browser)


@dataclass(frozen=True)
class CookieDbUnreadable:
    """A Cookies DB that could not be queried — distinct from one with no row.

    "Chrome has the file locked" and "you are not logged in" call for opposite
    reactions, so they must not collapse into the same verdict.
    """
    error: str


def chrome_time_to_datetime(chrome_us: int) -> datetime | None:
    """Chrome's 1601-epoch microseconds → aware UTC datetime (0 = session)."""
    if chrome_us <= 0:
        return None
    return datetime.fromtimestamp(chrome_us / 1_000_000 - CHROME_EPOCH_OFFSET_S,
                                  tz=timezone.utc)


def find_li_at_cookie(cookies_db: Path) -> LiAtCookie | CookieDbUnreadable | None:
    """Look up the LinkedIn session cookie in a Chrome Cookies database.

    Only host_key/name/expires_utc are read — all plaintext columns, so this
    works despite the cookie value itself being Keychain-encrypted on macOS.
    Opened read-only so a running Chrome is never disturbed. The host match is
    anchored: a bare '%linkedin.com' would also accept .evil-linkedin.com.
    """
    if not cookies_db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT host_key, expires_utc FROM cookies "
                "WHERE name = 'li_at' "
                "AND (host_key = '.linkedin.com' OR host_key LIKE '%.linkedin.com') "
                "ORDER BY expires_utc DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return CookieDbUnreadable(str(exc))
    return LiAtCookie(row[0], chrome_time_to_datetime(row[1])) if row else None


def check_banner_chrome_session(cookies_db: Path | None = None, *,
                                now: datetime | None = None) -> CheckResult:
    """The dedicated profile's own LinkedIn session — the thing uploads need.

    A cookie row proves the profile was logged in and the cookie has not run
    out; it cannot prove LinkedIn still honours it, which only an authenticated
    request would show. The wording says so rather than implying a liveness
    probe that never ran.
    """
    cookies_db = cookies_db if cookies_db is not None else BANNER_COOKIES_DB
    now = now if now is not None else datetime.now(timezone.utc)

    if not cookies_db.exists():
        return CheckResult("banner_chrome_session", Status.FAIL,
            f"{cookies_db} does not exist — launch ./launch_chrome_for_upload.sh "
            "and log in to LinkedIn once")

    cookie = find_li_at_cookie(cookies_db)
    if isinstance(cookie, CookieDbUnreadable):
        return CheckResult("banner_chrome_session", Status.WARN,
            f"could not read {cookies_db} ({cookie.error}) — "
            "banner-Chrome may be mid-write; re-run once it is closed")
    if cookie is None:
        return CheckResult("banner_chrome_session", Status.FAIL,
            "no li_at cookie in the banner-Chrome profile — log in to LinkedIn "
            "in the window ./launch_chrome_for_upload.sh opens")
    if cookie.expires is None:
        return CheckResult("banner_chrome_session", Status.WARN,
            "li_at is a session cookie — it will not survive a Chrome restart")

    days_left = (cookie.expires - now).total_seconds() / 86400
    if days_left <= 0:
        return CheckResult("banner_chrome_session", Status.FAIL,
            f"li_at expired {-days_left:.0f}d ago ({cookie.expires:%Y-%m-%d}) — log in again")
    status = Status.PASS if days_left >= 14 else Status.WARN
    return CheckResult("banner_chrome_session", status,
        f"unexpired li_at row present (not a liveness probe) — {days_left:.0f} more "
        f"days, expires {cookie.expires:%Y-%m-%d}")


# ── Checks: shared ─────────────────────────────────────────────────────────

def check_network() -> list[CheckResult]:
    results = []
    for host in ("github.com", "linkedin.com"):
        try:
            socket.getaddrinfo(host, 443)
        except socket.gaierror as exc:
            results.append(CheckResult(f"network:{host}", Status.FAIL, f"DNS failed: {exc}"))
            continue
        try:
            r = requests.head(f"https://{host}", timeout=5, allow_redirects=True)
            results.append(CheckResult(f"network:{host}", Status.PASS, f"HTTP {r.status_code}"))
        except requests.RequestException as exc:
            results.append(CheckResult(f"network:{host}", Status.FAIL, str(exc)))
    return results


def check_git_repo_state() -> CheckResult:
    if _run(["git", "-C", str(REPO_DIR), "status", "--porcelain"]).stdout.strip():
        return CheckResult("git_repo_state", Status.WARN, "working tree has uncommitted changes")
    fetch = _run(["git", "-C", str(REPO_DIR), "fetch", "--quiet"], timeout=20)
    if fetch.returncode != 0:
        return CheckResult("git_repo_state", Status.WARN, f"git fetch failed: {fetch.stderr.strip()}")
    local = _run(["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"]).stdout.strip()
    remote = _run(["git", "-C", str(REPO_DIR), "rev-parse", "@{u}"]).stdout.strip()
    if local != remote:
        return CheckResult("git_repo_state", Status.FAIL, f"HEAD {local[:7]} != origin {remote[:7]}")
    return CheckResult("git_repo_state", Status.PASS, "clean and in sync with origin")


def check_local_chrome_session() -> CheckResult:
    try:
        import browser_cookie3
    except ImportError:
        return CheckResult("local_chrome_session", Status.WARN, "browser_cookie3 not installed")
    try:
        cj = browser_cookie3.chrome(domain_name=".linkedin.com")
    except Exception as exc:
        return CheckResult("local_chrome_session", Status.WARN,
            f"could not read Chrome cookies ({exc}) — close Chrome and retry if this persists")
    jar = {c.name: c.value for c in cj if c.value}
    if "li_at" not in jar:
        return CheckResult("local_chrome_session", Status.FAIL,
            "li_at not found — not logged in to LinkedIn in Chrome")

    from check_cookie_expiry import SessionStatus, probe_session
    status, msg = probe_session(jar)
    mapped = {
        SessionStatus.VALID: Status.PASS,
        SessionStatus.EXPIRED: Status.FAIL,
        SessionStatus.AMBIGUOUS: Status.WARN,
    }[status]
    return CheckResult("local_chrome_session", mapped, msg)


def check_github_secret_age() -> CheckResult:
    """Age of the cloud path's cookie secret — informational only.

    Never FAILs: the upload-banner job that consumes LINKEDIN_COOKIES is
    workflow_dispatch-only because cookies replayed into a fresh CI browser get
    soft-rejected by LinkedIn regardless of freshness (documented non-goal since
    2026-07-15, see the comment above that job in
    .github/workflows/update_banner.yml). A stale secret therefore cannot break
    the daily upload, which runs from this machine's real Chrome profile.
    """
    proc = _run(["gh", "api", f"repos/{GH_REPO}/actions/secrets/{SECRET_NAME}"], timeout=15)
    if proc.returncode != 0:
        return CheckResult("github_secret_age", Status.WARN, f"gh api failed: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
        updated = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return CheckResult("github_secret_age", Status.WARN, f"could not parse secret metadata: {exc}")
    age_days = (datetime.now(timezone.utc) - updated).total_seconds() / 86400
    if age_days < 1:
        return CheckResult("github_secret_age", Status.PASS,
            f"{SECRET_NAME} last updated {age_days:.1f}d ago")
    return CheckResult("github_secret_age", Status.WARN,
        f"{SECRET_NAME} last updated {age_days:.1f}d ago (lifetime is ~2d) — only "
        "affects the workflow_dispatch cloud upload, not the daily local one")


def parse_lsof_listeners(output: str) -> list[str]:
    """`lsof -nP -iTCP:<port> -sTCP:LISTEN` → ["<command> (pid N)", …].

    Parsed here rather than piped through `ps --no-headers` / `xargs -r`: both
    flags are GNU-only and the BSD tools on macOS reject them. Deduplicated
    because lsof prints one row per socket, so a dual-stack listener reports
    the same process twice.
    """
    owners: list[str] = []
    for line in output.splitlines()[1:]:      # row 0 is lsof's header
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            owner = f"{fields[0]} (pid {fields[1]})"
            if owner not in owners:
                owners.append(owner)
    return owners


def check_cdp_port() -> CheckResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        bound = s.connect_ex(("127.0.0.1", CDP_PORT)) == 0
    if not bound:
        return CheckResult("cdp_port", Status.PASS, f"port {CDP_PORT} free")
    proc = _run(["lsof", "-nP", f"-iTCP:{CDP_PORT}", "-sTCP:LISTEN"])
    owners = parse_lsof_listeners(proc.stdout)
    return CheckResult("cdp_port", Status.WARN,
        f"port {CDP_PORT} bound: {', '.join(owners) or 'unknown process'}")


# Scheduler-side checks differ per platform; the rest run everywhere.
SYSTEMD_CHECKS = [
    check_systemd_parity,
    check_timer_state,
    check_last_run,
    check_recent_failure_count,
    check_local_chrome_session,
]
LAUNCHD_CHECKS = [
    check_launchd_parity,
    check_launchd_job,
    check_launchd_recent_failures,
    check_banner_chrome_session,
]
SHARED_CHECKS = [
    check_network,
    check_git_repo_state,
    check_github_secret_age,
    check_cdp_port,
]


def checks_for_platform(system: str | None = None) -> list:
    system = system if system is not None else platform.system()
    scheduler = LAUNCHD_CHECKS if system == "Darwin" else SYSTEMD_CHECKS
    return [*scheduler, *SHARED_CHECKS]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: list[CheckResult] = []
    for check in checks_for_platform():
        try:
            r = check()
        except Exception as exc:
            results.append(CheckResult(check.__name__, Status.FAIL, f"check crashed: {exc!r}"))
            continue
        results.extend(r if isinstance(r, list) else [r])

    if args.json:
        print(json.dumps([{**asdict(r), "status": r.status.value} for r in results], indent=2))
    else:
        icon = {Status.PASS: "✓", Status.WARN: "⚠", Status.FAIL: "✗"}
        print(f"LinkedIn Banner — Automation Doctor  ({datetime.now():%Y-%m-%d %H:%M})")
        print("=" * 70)
        for r in results:
            print(f"[{icon[r.status]} {r.status.value:4}] {r.name:28} {r.detail.splitlines()[0]}")
        counts = {s: sum(1 for r in results if r.status == s) for s in Status}
        print("-" * 70)
        print(f"{counts[Status.PASS]} PASS  {counts[Status.WARN]} WARN  {counts[Status.FAIL]} FAIL")

    sys.exit(1 if any(r.status == Status.FAIL for r in results) else 0)


if __name__ == "__main__":
    main()
