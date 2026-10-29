import json
import logging
from pathlib import Path

import pytest
from fakes import FakeStore

from kbsync.convert import CONVERTER_VERSION, convert
from kbsync.errors import ConfigError, ListingError, run
from kbsync.logs import setup_logging
from kbsync.pipeline import NEXT_HINT
from kbsync.pipeline import run as pipeline
from kbsync.sync import StoredFile
from kbsync.zendesk import Article, Listing

KEY = "sk-testkey1234567890abcdef"
FIXTURES = sorted((Path(__file__).parent / "fixtures" / "articles").glob("*.json"))
SUMMARY = "added={a} updated={u} skipped={s} deleted={d} failed={f} files={n} chunks≈{c} in {t}s"


def load(path):
    d = json.loads(path.read_text())
    return Article(d["id"], d["title"], d["body"], d["html_url"], d["updated_at"], False)


FIXTURE_ARTICLES = [load(p) for p in FIXTURES]


def synthetic(n):
    return [
        Article(i, f"Title {i}", f"<p>body {i}</p>", f"https://x/{i}", "2026-01-01", False)
        for i in range(1, n + 1)
    ]


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("PUBLIC_DIR", str(tmp_path / "public"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.delenv("FULL_REBUILD", raising=False)
    monkeypatch.chdir(tmp_path)


def go(articles=None, store=None, argv=(), complete=True, fetch=None, pings=None, **kw):
    articles = FIXTURE_ARTICLES if articles is None else articles
    store = store if store is not None else FakeStore()
    seen = {}

    def default_fetch(limit=None):
        seen["limit"] = limit
        chosen = articles[:limit] if limit else articles
        return Listing(list(chosen), complete and not limit)

    def ping_get(url, timeout):
        if pings is not None:
            pings.append(url)
        return 200

    ticks = iter([0.0, 48.4, 48.4, 48.4, 48.4])
    pipeline(
        list(argv),
        fetch=fetch or default_fetch,
        make_store=lambda cfg: store,
        smoke=lambda s: None,
        ping_get=ping_get,
        clock=lambda: next(ticks),
        **kw,
    )
    return store


def exit_of(capsys, **kw):
    with pytest.raises(SystemExit) as info:
        run(lambda: go(**kw))
    return info.value.code, capsys.readouterr().err


def last_summary(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("added=")][-1]


def test_first_run_then_second_run_summary(caplog):
    caplog.set_level(logging.INFO, logger="kbsync")
    store = go()
    n = len(FIXTURE_ARTICLES)
    first = last_summary(caplog)
    assert first.startswith(f"added={n} updated=0 skipped=0 deleted=0 failed=0 files={n} chunks≈")
    assert first.endswith(" in 48s")
    assert caplog.records[-1].getMessage() == first
    caplog.clear()
    go(store=store)
    second = last_summary(caplog)
    assert second.startswith(f"added=0 updated=0 skipped={n} deleted=0 failed=0 files={n} chunks≈")
    assert store.calls.count(("upload_many", [])) == 0
    assert [c for c in store.calls if c[0] == "upload_many"] == [
        ("upload_many", [a.id for a in FIXTURE_ARTICLES])
    ]


def test_summary_format_is_exact(caplog):
    caplog.set_level(logging.INFO, logger="kbsync")
    go(synthetic(3))
    msg = last_summary(caplog)
    assert msg == "added=3 updated=0 skipped=0 deleted=0 failed=0 files=3 chunks≈3 in 48s"


def test_chunks_use_thousands_separator(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="kbsync")
    monkeypatch.setattr("kbsync.pipeline.estimate_chunks", lambda *a: 1020)
    go(synthetic(1))
    assert "chunks≈1,020 in 48s" in last_summary(caplog)


def test_files_is_the_store_count_not_local(caplog):
    caplog.set_level(logging.INFO, logger="kbsync")
    others = [StoredFile(f"v{i}", f"f{i}", {"article_id": str(900 + i)}) for i in range(5)]
    go(synthetic(10), store=FakeStore(others))
    assert " files=15 " in last_summary(caplog)


def test_error_lines_and_codes(capsys):
    # missing key
    import os

    os.environ.pop("API_KEY")
    code, err = exit_of(capsys)
    assert (code, err) == (
        2,
        "API_KEY is not set. Copy .env.sample to .env or pass -e API_KEY=...\n",
    )


def test_key_rejected(capsys):
    class Rejecting(FakeStore):
        def list_files(self):
            raise ConfigError("API_KEY was rejected by OpenAI. Check the key and try again.")

    code, err = exit_of(capsys, store=Rejecting())
    assert (code, err) == (2, "API_KEY was rejected by OpenAI. Check the key and try again.\n")


def test_listing_failed(capsys):
    def fetch(limit):
        raise ListingError("Zendesk listing failed: HTTP 503 from https://x after 5 attempts")

    code, err = exit_of(capsys, fetch=fetch)
    assert code == 3
    assert err == (
        "Zendesk listing failed: HTTP 503 from https://x after 5 attempts. "
        "Nothing was changed or deleted. Try again later.\n"
    )


def test_listing_incomplete(capsys):
    def fetch(limit):
        raise ListingError("Zendesk listing incomplete: got 300 of 415 articles.")

    code, err = exit_of(capsys, fetch=fetch)
    assert code == 3
    assert err == (
        "Zendesk listing incomplete: got 300 of 415 articles. "
        "Nothing was changed or deleted. Try again later.\n"
    )


def test_store_unreachable(capsys):
    from kbsync.errors import StoreError

    class Down(FakeStore):
        def list_files(self):
            raise StoreError(
                "Cannot reach the OpenAI vector store: Connection error. Check API_KEY and network."
            )

    code, err = exit_of(capsys, store=Down())
    assert (code, err) == (
        4,
        "Cannot reach the OpenAI vector store: Connection error. Check API_KEY and network.\n",
    )


def test_too_many_uploads_failed(capsys):
    code, err = exit_of(capsys, articles=synthetic(10), store=FakeStore(fail_ids=[1, 2, 3]))
    assert (code, err) == (
        5,
        "Too many uploads failed: 3 of 10 (limit 20%). "
        "Failed articles keep their old version and are retried on the next run.\n",
    )


def test_nothing_converted_is_a_failure(capsys, monkeypatch):
    def boom(article):
        raise ValueError("bad html")

    monkeypatch.setattr("kbsync.pipeline.convert", boom)
    code, err = exit_of(capsys, articles=synthetic(4))
    assert code == 5
    assert err.startswith("Too many articles failed: 0 of 4 could be converted.")


def test_no_key_exits_before_network(capsys, monkeypatch):
    monkeypatch.delenv("API_KEY")

    def fetch(limit):
        raise AssertionError("network used")

    code, err = exit_of(capsys, fetch=fetch)
    assert code == 2


def test_two_of_fifty_failures_exit_zero(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="kbsync")
    go(synthetic(50), store=FakeStore(fail_ids=[7, 8]))
    assert "failed=2" in last_summary(caplog)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert [r.getMessage() for r in warnings] == [
        "failed article 7 (Title 7): upload failed",
        "failed article 8 (Title 8): upload failed",
    ]
    report = json.loads((tmp_path / "public/runs/latest.json").read_text())
    assert report["status"] == "ok" and report["failed"] == 2
    assert report["failures"] == [
        {"article_id": 7, "title": "Title 7", "reason": "upload failed"},
        {"article_id": 8, "title": "Title 8", "reason": "upload failed"},
    ]


def test_failure_report_and_fail_ping(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/abc")
    pings = []
    code, _ = exit_of(
        capsys, articles=synthetic(10), store=FakeStore(fail_ids=[1, 2, 3]), pings=pings
    )
    assert code == 5
    report = json.loads((tmp_path / "public/runs/latest.json").read_text())
    assert report["status"] == "failed"
    assert "files" not in report and "chunks_estimate" not in report
    assert report["error"].startswith("Too many uploads failed: 3 of 10")
    assert pings == ["https://hc.example/abc/fail"]


def test_success_report_and_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/abc")
    pings = []
    go(synthetic(3), pings=pings)
    report = json.loads((tmp_path / "public/runs/latest.json").read_text())
    assert report["status"] == "ok" and report["added"] == 3
    assert pings == ["https://hc.example/abc"]


def test_missing_key_still_writes_failed_report(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("API_KEY")
    exit_of(capsys)
    report = json.loads((tmp_path / "public/runs/latest.json").read_text())
    assert report["status"] == "failed" and report["error"].startswith("API_KEY is not set")


def test_unwritable_public_dir_keeps_exit_code(capsys, caplog, tmp_path, monkeypatch):
    monkeypatch.delenv("API_KEY")
    blocker = tmp_path / "ro"
    blocker.write_text("not a directory")
    monkeypatch.setenv("PUBLIC_DIR", str(blocker))
    code, err = exit_of(capsys)
    assert code == 2
    assert err == "API_KEY is not set. Copy .env.sample to .env or pass -e API_KEY=...\n"
    assert "Traceback" not in err
    assert "Could not write the run report to" in caplog.text


def test_limit_deletes_nothing(caplog):
    caplog.set_level(logging.INFO, logger="kbsync")
    others = [StoredFile(f"v{i}", f"f{i}", {"article_id": str(1000 + i)}) for i in range(50)]
    store = go(synthetic(20), store=FakeStore(others), argv=["--limit", "5"])
    assert [c for c in store.calls if c[0] == "upload_many"] == [("upload_many", [1, 2, 3, 4, 5])]
    assert not [c for c in store.calls if c[0] == "delete"]
    assert any(r.getMessage() == "Limited run: nothing is deleted." for r in caplog.records)
    assert " deleted=0 " in last_summary(caplog)


@pytest.mark.parametrize("mode", ["flag", "env"])
def test_full_rebuild_reconverts_and_does_not_reupload_unchanged(mode, monkeypatch):
    store = go()
    if mode == "env":
        monkeypatch.setenv("FULL_REBUILD", "true")
    converted = []
    real = convert
    monkeypatch.setattr("kbsync.pipeline.convert", lambda a: converted.append(a.id) or real(a))
    go(store=store, argv=["--full"] if mode == "flag" else [])
    assert sorted(converted) == sorted(a.id for a in FIXTURE_ARTICLES)
    assert [c for c in store.calls if c[0] == "upload_many"] == [
        ("upload_many", [a.id for a in FIXTURE_ARTICLES])
    ]


def test_files_written_to_out_dir_articles(tmp_path):
    go()
    written = sorted(p.name for p in (tmp_path / "out" / "articles").glob("*.md"))
    assert written == sorted(convert(a).filename for a in FIXTURE_ARTICLES)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "public"]


def test_next_hint_is_logged(caplog):
    caplog.set_level(logging.INFO, logger="kbsync")
    go(synthetic(1))
    assert NEXT_HINT in [r.getMessage() for r in caplog.records]
    assert NEXT_HINT.startswith('Next: uv run ask.py "How do I add a YouTube video?"')


def test_attributes_use_converter_version():
    store = go(synthetic(1))
    assert store.uploaded_attributes[1]["converter_version"] == CONVERTER_VERSION


def test_full_run_never_prints_the_key(capsys, tmp_path):
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    setup_logging()
    go(synthetic(50), store=FakeStore(fail_ids=[2]))
    out = capsys.readouterr()
    root.handlers[:], root.level = saved
    assert out.err.strip()
    assert KEY not in out.out + out.err
    for p in tmp_path.rglob("*.json"):
        assert KEY not in p.read_text()


def test_openai_store_stats_is_called_before_summary(caplog):
    from kbsync.store import OpenAIStore

    class StatsStore(FakeStore, OpenAIStore):
        def __init__(self):
            FakeStore.__init__(self)
            self.stats_calls = []

        def stats(self, chunks):
            self.stats_calls.append(chunks)
            logging.getLogger("kbsync").info("Vector store x: files=3")

    caplog.set_level(logging.INFO, logger="kbsync")
    store = StatsStore()
    go(synthetic(3), store=store)
    assert store.stats_calls == [3]
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs.index("Vector store x: files=3") < msgs.index(NEXT_HINT) < len(msgs) - 1
