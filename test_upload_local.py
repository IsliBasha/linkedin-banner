"""
Tests for upload_local.py's stale-banner guard and poll-for-new-banner loop.

Driven by the 2026-07-16 incident: both daily runs "succeeded" (exit 0,
HTTP 200/201 from LinkedIn) while visibly changing nothing, because GitHub
ran the 06:00 UTC banner-generation cron ~2 h late and the uploader
re-uploaded the previous day's pixel-identical banner.png.

Requirements under test:
  1. The uploader records the content hash of what it last uploaded and
     refuses to silently re-upload an identical banner.
  2. When run by the systemd timer (POLL_FOR_NEW_BANNER=1) it waits for
     GitHub's delayed commit instead of racing it.
  3. Interactive runs fail fast with a clear message; --force overrides.
  4. A failed save-response wait must not mislabel arbitrary errors as a
     "180 s timeout" (that mislabel hid the real cause on 2026-07-16).
"""

from __future__ import annotations

import hashlib
import sys

import pytest

import upload_local as ul


# ── Hash guard ────────────────────────────────────────────────────────────────

def test_banner_sha256_matches_hashlib_digest(tmp_path):
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"fake-png-bytes")

    expected = hashlib.sha256(b"fake-png-bytes").hexdigest()

    assert ul.banner_sha256(banner) == expected


def test_is_banner_unchanged_false_when_nothing_recorded_yet(tmp_path):
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"day-1")
    state = tmp_path / "state"

    assert ul.is_banner_unchanged(banner, state) is False


def test_is_banner_unchanged_true_after_recording_identical_content(tmp_path):
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"day-1")
    state = tmp_path / "state"

    ul.record_uploaded_banner(banner, state)

    assert ul.is_banner_unchanged(banner, state) is True


def test_is_banner_unchanged_false_when_content_differs(tmp_path):
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"day-1")
    state = tmp_path / "state"
    ul.record_uploaded_banner(banner, state)

    banner.write_bytes(b"day-2")

    assert ul.is_banner_unchanged(banner, state) is False


# ── Poll-for-new-banner loop ──────────────────────────────────────────────────

class FakeClock:
    """Deterministic monotonic clock; sleeping advances time instantly."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_wait_for_new_banner_returns_true_once_new_banner_lands():
    clock = FakeClock()
    pulls: list[int] = []
    unchanged_sequence = iter([True, True, False])

    result = ul.wait_for_new_banner(
        budget_s=3600,
        interval_s=300,
        pull_fn=lambda: pulls.append(1),
        unchanged_fn=lambda: next(unchanged_sequence),
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert result is True
    assert len(pulls) == 3


def test_wait_for_new_banner_returns_false_when_budget_exhausted():
    clock = FakeClock()
    pulls: list[int] = []

    result = ul.wait_for_new_banner(
        budget_s=900,
        interval_s=300,
        pull_fn=lambda: pulls.append(1),
        unchanged_fn=lambda: True,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert result is False
    assert len(pulls) == 3  # 900 s budget / 300 s interval → exactly 3 cycles
    assert clock.now == pytest.approx(900)


def test_wait_for_new_banner_sleeps_interval_between_pulls():
    clock = FakeClock()

    ul.wait_for_new_banner(
        budget_s=900,
        interval_s=300,
        pull_fn=lambda: None,
        unchanged_fn=lambda: True,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert clock.sleeps == [300, 300, 300]


# ── Save-wait warning label ───────────────────────────────────────────────────

def test_save_wait_warning_reports_real_timeout_as_180s():
    from playwright.sync_api import TimeoutError as PWTimeout

    msg = ul.save_wait_warning(PWTimeout("deadline exceeded"))

    assert "180 s" in msg


def test_save_wait_warning_does_not_mislabel_other_errors_as_timeout():
    msg = ul.save_wait_warning(ValueError("boom"))

    assert "180 s" not in msg
    assert "ValueError" in msg
    assert "boom" in msg


# ── main() guard flow ─────────────────────────────────────────────────────────

@pytest.fixture
def wired_main(monkeypatch, tmp_path):
    """main() with every side-effectful step replaced by a call recorder."""
    calls: list[str] = []
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"day-1")

    monkeypatch.setattr(ul, "BANNER_PATH", banner)
    monkeypatch.setattr(ul, "LAST_UPLOADED_STATE", tmp_path / "state")
    monkeypatch.setattr(ul, "pull_latest", lambda: calls.append("pull"))
    monkeypatch.setattr(ul, "wait_for_cdp", lambda: calls.append("cdp"))
    monkeypatch.setattr(ul, "upload_banner", lambda: calls.append("upload"))
    monkeypatch.setattr(
        ul, "wait_for_new_banner", lambda: calls.append("poll") or False
    )
    monkeypatch.setattr(sys, "argv", ["upload_local.py"])
    monkeypatch.delenv("POLL_FOR_NEW_BANNER", raising=False)
    return calls


def test_main_exits_loudly_when_banner_unchanged_interactive(wired_main):
    ul.record_uploaded_banner()  # current banner == last uploaded

    with pytest.raises(SystemExit) as excinfo:
        ul.main()

    assert "identical" in str(excinfo.value)
    assert "upload" not in wired_main


def test_main_force_flag_bypasses_unchanged_guard(wired_main, monkeypatch):
    ul.record_uploaded_banner()
    monkeypatch.setattr(sys, "argv", ["upload_local.py", "--force"])

    ul.main()

    assert "upload" in wired_main


def test_main_polls_then_exits_when_no_new_banner_arrives(wired_main, monkeypatch):
    ul.record_uploaded_banner()
    monkeypatch.setenv("POLL_FOR_NEW_BANNER", "1")

    with pytest.raises(SystemExit):
        ul.main()

    assert "poll" in wired_main
    assert "upload" not in wired_main


def test_main_uploads_once_poll_finds_new_banner(wired_main, monkeypatch):
    ul.record_uploaded_banner()
    monkeypatch.setenv("POLL_FOR_NEW_BANNER", "1")
    monkeypatch.setattr(
        ul, "wait_for_new_banner", lambda: wired_main.append("poll") or True
    )

    ul.main()

    assert wired_main.index("poll") < wired_main.index("upload")


def test_main_records_uploaded_banner_after_success(wired_main):
    # Nothing recorded yet → upload proceeds, then the hash must be saved
    ul.main()

    assert "upload" in wired_main
    assert ul.is_banner_unchanged() is True
