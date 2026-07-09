#!/usr/bin/env python3
"""
doctor.py — one-command health check for the LinkedIn banner automation
pipeline (Path A: GitHub Actions, Path B: local systemd).

Usage:
    .venv/bin/python3 doctor.py            # human-readable report
    .venv/bin/python3 doctor.py --json     # machine-readable

Exit code: 0 if no FAIL, 1 if any check FAILed (WARN does not affect exit code).
"""

from __future__ import annotations

import argparse
import difflib
import json
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import requests

REPO_DIR = Path(__file__).parent.resolve()
INSTALLED_DIR = Path.home() / ".config/systemd/user"
UNIT_NAMES = ["linkedin-banner.service", "linkedin-banner.timer"]
GH_REPO = "IsliBasha/linkedin-banner"
SECRET_NAME = "LINKEDIN_COOKIES"
CDP_PORT = 9222


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


# ── Checks ─────────────────────────────────────────────────────────────────

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
    proc = _run(["gh", "api", f"repos/{GH_REPO}/actions/secrets/{SECRET_NAME}"], timeout=15)
    if proc.returncode != 0:
        return CheckResult("github_secret_age", Status.WARN, f"gh api failed: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
        updated = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return CheckResult("github_secret_age", Status.WARN, f"could not parse secret metadata: {exc}")
    age_days = (datetime.now(timezone.utc) - updated).total_seconds() / 86400
    status = Status.PASS if age_days < 1 else Status.WARN if age_days < 2 else Status.FAIL
    return CheckResult("github_secret_age", status,
        f"{SECRET_NAME} last updated {age_days:.1f}d ago (real-world lifetime is ~2d)")


def check_cdp_port() -> CheckResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        bound = s.connect_ex(("127.0.0.1", CDP_PORT)) == 0
    if not bound:
        return CheckResult("cdp_port", Status.PASS, f"port {CDP_PORT} free")
    owner = _run(["bash", "-c",
        f"lsof -i :{CDP_PORT} -sTCP:LISTEN -t 2>/dev/null | xargs -r ps -o pid,etimes,cmd --no-headers -p"]).stdout.strip()
    return CheckResult("cdp_port", Status.WARN, f"port {CDP_PORT} bound: {owner or 'unknown process'}")


CHECKS = [
    check_systemd_parity,
    check_timer_state,
    check_last_run,
    check_recent_failure_count,
    check_network,
    check_git_repo_state,
    check_local_chrome_session,
    check_github_secret_age,
    check_cdp_port,
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results: list[CheckResult] = []
    for check in CHECKS:
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
