import http.client
import json
import logging
import re
import subprocess
from datetime import UTC, datetime

import pytest

from kbsync import report as report_mod
from kbsync.logs import setup_logging
from kbsync.report import build_report, git_sha, ping, write_report
from kbsync.sync import SyncResult

KEY = "sk-" + "test-1234567890abcdef"  # built at runtime so secret scanners skip it
OTHER_KEY = "sk-" + "abcdefghijklmnop1234"


def make(day=29, error=None):
    result = SyncResult(added=1, updated=2, skipped=3, deleted=4, failed=5)
    result.changes = [{"article_id": 1, "title": "t", "url": "u", "change": "added"}]
    started = datetime(2026, 10, day, 3, 0, 1, tzinfo=UTC)
    return build_report(result, started, 41.6, 10, 99, "2", error)


def test_write_report_creates_files_with_all_keys(tmp_path):
    rep = make()
    write_report(tmp_path / "public", rep)
    runs = tmp_path / "public" / "runs"
    day = json.loads((runs / "2026-10-29.json").read_text())
    assert day == json.loads((runs / "latest.json").read_text()) == rep
    types = {
        "status": str, "started_at": str, "duration_s": int, "added": int, "updated": int,
        "skipped": int, "deleted": int, "failed": int, "files": int, "chunks_estimate": int,
        "converter_version": str, "git_sha": str, "changes": list,
    }  # fmt: skip
    for key, kind in types.items():
        assert isinstance(day[key], kind), key
    assert day["started_at"] == "2026-10-29T03:00:01Z"
    assert day["duration_s"] == 42
    assert "error" not in day
    index = json.loads((runs / "index.json").read_text())
    assert index == [
        {"date": "2026-10-29", **{k: day[k] for k in report_mod.INDEX_KEYS}},
    ]


def test_crash_before_replace_keeps_previous_latest(tmp_path, monkeypatch):
    write_report(tmp_path, make(28))

    def boom(src, dst):
        raise RuntimeError("crash")

    monkeypatch.setattr(report_mod.os, "replace", boom)
    with pytest.raises(RuntimeError):
        write_report(tmp_path, make(29))
    latest = json.loads((tmp_path / "runs" / "latest.json").read_text())
    assert latest["started_at"].startswith("2026-10-28")


def test_same_date_replaces_and_next_date_adds_entry(tmp_path):
    write_report(tmp_path, make(29))
    second = make(29)
    second["added"] = 7
    write_report(tmp_path, second)
    write_report(tmp_path, make(30))
    runs = tmp_path / "runs"
    assert json.loads((runs / "2026-10-29.json").read_text())["added"] == 7
    index = json.loads((runs / "index.json").read_text())
    assert [e["date"] for e in index] == ["2026-10-30", "2026-10-29"]
    assert index[1]["added"] == 7


def test_failed_run_has_one_line_error_and_counts():
    rep = make(error="boom\nTraceback...")
    assert rep["status"] == "failed"
    assert rep["error"] == "boom"
    assert rep["added"] == 1


def test_git_sha_env_then_git_then_unknown(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123")
    assert git_sha() == "abc123"
    monkeypatch.delenv("GIT_SHA")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "def456\n", "")
    )
    assert git_sha() == "def456"

    def fail(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", fail)
    assert git_sha() == "unknown"


def test_ping_urls_timeout_and_trailing_slash():
    calls = []

    def get(url, timeout):
        calls.append((url, timeout))
        return 200

    ping("https://hc-ping.com/abc", True, get)
    ping("https://hc-ping.com/abc/", False, get)
    assert calls == [("https://hc-ping.com/abc", 10), ("https://hc-ping.com/abc/fail", 10)]


def test_ping_without_url_makes_no_request():
    def get(url, timeout):
        raise AssertionError("called")

    ping("", True, get)
    ping(None, False, get)


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), 500])
def test_ping_failure_logs_host_only_and_does_not_raise(failure, caplog):
    calls = []

    def get(url, timeout):
        calls.append(url)
        if isinstance(failure, Exception):
            raise failure
        return failure

    with caplog.at_level(logging.WARNING, logger="kbsync"):
        ping("https://hc-ping.com/secret-path", True, get)
    assert len(calls) == 3
    assert "Heartbeat ping failed:" in caplog.text
    assert "hc-ping.com" in caplog.text
    assert "secret-path" not in caplog.text


def test_logging_masks_key_everywhere(monkeypatch, capsys):
    monkeypatch.setenv("API_KEY", KEY)
    setup_logging(logging.DEBUG)
    log = logging.getLogger("kbsync.test")
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
        log.log(level, "msg %s", KEY)
        log.log(level, "Authorization: Bearer %s", KEY)
    log.error("other %s key", OTHER_KEY)
    try:
        raise ValueError(f"bad {KEY}")
    except ValueError:
        log.exception("failed")
    err = capsys.readouterr().err
    assert KEY not in err
    assert OTHER_KEY not in err
    assert "Bearer ***" in err
    assert "***" in err


def test_log_line_format(capsys):
    setup_logging()
    logging.getLogger("kbsync").info("hello")
    assert re.fullmatch(r"\d\d:\d\d:\d\d INFO hello\n", capsys.readouterr().err)


def test_error_in_public_report_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/secret-path")
    msg = f"bad {KEY} {OTHER_KEY} https://hc-ping.com/secret-path"
    write_report(tmp_path, make(error=msg))
    text = (tmp_path / "runs" / "latest.json").read_text()
    for leaked in (KEY, OTHER_KEY, "secret-path"):
        assert leaked not in text
    assert "***" in text


@pytest.mark.parametrize(
    "failure", [ValueError("unknown url type"), http.client.BadStatusLine("x")]
)
def test_ping_catches_malformed_url_and_http_exceptions(failure, caplog):
    def get(url, timeout):
        raise failure

    with caplog.at_level(logging.WARNING, logger="kbsync"):
        ping("https://hc-ping.com/secret-path", True, get)
    assert type(failure).__name__ in caplog.text
    assert "secret-path" not in caplog.text


def test_corrupt_index_is_rebuilt_with_warning(tmp_path, caplog):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "index.json").write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        write_report(tmp_path, make())
    index = json.loads((runs / "index.json").read_text())
    assert [e["date"] for e in index] == ["2026-10-29"]
    assert "corrupt" in caplog.text


def test_http_client_request_logs_are_quiet():
    setup_logging()
    for name in ("httpx", "httpx2", "httpcore", "openai"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
    assert logging.getLogger("kbsync").isEnabledFor(logging.INFO)
