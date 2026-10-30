import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "status" / "index.html").read_text()
FIXTURES = ROOT / "status" / "fixtures"
COUNTS = {"added", "updated", "skipped", "deleted", "failed"}
REPORT_KEYS = COUNTS | {"status", "started_at", "duration_s", "changes"}
INDEX_KEYS = COUNTS | {"date", "status", "started_at", "files"}


def test_page_is_safe_and_self_contained():
    assert "innerHTML" not in PAGE and "insertAdjacentHTML" not in PAGE
    assert not re.search(r"<script[^>]*\ssrc=", PAGE)
    assert not re.search(r"<link[^>]*rel=[\"']stylesheet", PAGE)
    assert not re.search(r"@import|url\(", PAGE)


def test_page_constants():
    assert "RUN_HOUR_UTC = 2, RUN_MINUTE_UTC = 17" in PAGE
    assert "STALE_HOURS = 26" in PAGE
    # The public read-only badge, never a ping URL.
    assert 'HC_BADGE_URL = "https://healthchecks.io/badge/' in PAGE
    assert "hc-ping.com" not in PAGE


@pytest.mark.parametrize("name", ["fresh", "stale", "hostile"])
def test_fixture_matches_contract(name):
    runs = FIXTURES / name / "runs"
    latest = json.loads((runs / "latest.json").read_text())
    assert REPORT_KEYS <= latest.keys()
    index = json.loads((runs / "index.json").read_text())
    assert [e["date"] for e in index] == sorted((e["date"] for e in index), reverse=True)
    for entry in index:
        assert INDEX_KEYS <= entry.keys()
        day = json.loads((runs / f"{entry['date']}.json").read_text())
        assert REPORT_KEYS <= day.keys()


def test_empty_and_broken_fixtures():
    assert not (FIXTURES / "empty" / "runs" / "latest.json").exists()
    with pytest.raises(json.JSONDecodeError):
        json.loads((FIXTURES / "broken" / "runs" / "latest.json").read_text())


def test_hostile_fixture_has_attack_strings():
    text = (FIXTURES / "hostile" / "runs" / "latest.json").read_text()
    assert "<img src=x onerror=alert(1)>" in text
    assert "javascript:alert(1)" in text
