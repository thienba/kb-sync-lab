import logging
from dataclasses import replace

from fakes import FakeStore, stored_for

from kbsync.convert import CONVERTER_VERSION, convert
from kbsync.sync import StoredFile, sync
from kbsync.zendesk import Article


def art(i: int, updated="2026-01-01T00:00:00Z", body="<p>hello</p>") -> Article:
    return Article(i, f"Title {i}", body, f"https://support.optisigns.com/a/{i}", updated, False)


class Spy:
    def __init__(self, fn=convert, raises=()):
        self.fn, self.raises, self.count = fn, set(raises), 0

    def __call__(self, article):
        self.count += 1
        if article.id in self.raises:
            raise ValueError("boom")
        return self.fn(article)


def run(articles, store, spy=None, complete=True, **kw):
    spy = spy or Spy()
    return sync(articles, complete, store, spy, kw.pop("version", CONVERTER_VERSION), **kw), spy


def test_new_article_is_added_with_exact_attributes():
    store = FakeStore()
    result, _ = run([art(1)], store)
    assert result.added == 1
    assert result.changes == [
        {"article_id": 1, "title": "Title 1", "url": art(1).html_url, "change": "added"}
    ]
    attrs = store.uploaded_attributes[1]
    assert set(attrs) == {
        "article_id",
        "content_hash",
        "converter_version",
        "source_updated_at",
        "url",
    }
    assert all(isinstance(v, str) and len(v) <= 256 for v in attrs.values())


def test_attribute_values_are_capped_at_256_characters():
    store = FakeStore()
    long = replace(art(1), html_url="https://x/" + "a" * 400)
    run([long], store)
    assert len(store.uploaded_attributes[1]["url"]) == 256


def test_fast_path_skips_without_convert():
    a = art(1)
    store = FakeStore([stored_for(convert(a))])
    result, spy = run([a], store)
    assert (result.skipped, spy.count) == (1, 0)
    assert not [c for c in store.calls if c[0] == "upload_many"]


def test_changed_updated_at_same_hash_is_skipped():
    store = FakeStore([stored_for(convert(art(1)))])
    result, spy = run([art(1, updated="2026-02-02T00:00:00Z")], store)
    assert (result.skipped, spy.count) == (1, 1)
    assert not [c for c in store.calls if c[0] == "upload_many"]


def test_changed_hash_is_updated_with_upload_before_delete():
    old = stored_for(convert(art(1)))
    store = FakeStore([old])
    result, _ = run([art(1, updated="2026-02-02T00:00:00Z", body="<p>new</p>")], store)
    assert result.updated == 1
    assert store.calls[1:] == [
        ("upload_many", [1]),
        ("delete", old.vs_file_id),
        ("delete_file", old.file_id),
    ]


def test_failed_upload_keeps_old_file_and_retries_next_sync():
    old = stored_for(convert(art(1)))
    other = art(2)
    store = FakeStore([old], fail_ids={1})
    changed = art(1, updated="2026-02-02T00:00:00Z", body="<p>new</p>")
    result, _ = run([changed, other], store)
    assert (result.failed, result.added) == (1, 1)
    assert old in store.files
    assert ("delete", old.vs_file_id) not in store.calls
    store.fail_ids = set()
    retry, _ = run([changed, other], store)
    assert (retry.updated, retry.skipped) == (1, 1)


def test_older_converter_version_same_hash_is_skipped():
    store = FakeStore([stored_for(convert(art(1)), version="1")])
    result, spy = run([art(1)], store, version="2")
    assert (result.skipped, spy.count) == (1, 1)


def test_older_converter_version_different_hash_is_updated():
    old = stored_for(replace(convert(art(1)), body_hash="stale"), version="1")
    result, spy = run([art(1)], FakeStore([old]), version="2")
    assert (result.updated, spy.count) == (1, 1)


def files_for(n):
    return [stored_for(convert(art(i))) for i in range(1, n + 1)]


def test_delete_at_threshold_boundary():
    store = FakeStore(files_for(100))
    result, _ = run([art(i) for i in range(11, 101)], store)
    assert result.deleted == 10
    assert len(store.files) == 90


def test_delete_held_back_above_threshold(caplog):
    store = FakeStore(files_for(100))
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        result, _ = run([art(i) for i in range(12, 101)], store)
    assert result.deleted == 0
    assert sorted(result.held_back) == list(range(1, 12))
    assert (
        "Delete held back: 11 of 100 stored articles are missing from Zendesk "
        "(limit 10%). Nothing deleted."
    ) in caplog.messages
    assert len(store.files) == 100


def test_empty_store_never_deletes():
    result, _ = run([], FakeStore())
    assert (result.deleted, result.held_back) == (0, [])


def test_partial_listing_deletes_nothing_and_holds_nothing_back(caplog):
    store = FakeStore(files_for(2))
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        result, _ = run([art(1)], store, complete=False)
    assert (result.deleted, result.held_back) == (0, [])
    assert not caplog.messages


def test_full_converts_everything_and_uploads_nothing_when_identical():
    store = FakeStore(files_for(3))
    result, spy = run([art(i) for i in (1, 2, 3)], store, full=True)
    assert (result.skipped, spy.count) == (3, 3)
    assert not [c for c in store.calls if c[0] == "upload_many"]


def test_duplicate_keeps_matching_hash_and_deletes_other():
    good, stale = stored_for(convert(art(1)), n=0), stored_for(convert(art(1)), n=1)
    stale = replace(stale, attributes={**stale.attributes, "content_hash": "old"})
    store = FakeStore([stale, good])
    result, _ = run([art(1)], store)
    assert result.skipped == 1
    assert store.files == [good]


def test_duplicates_without_match_are_both_deleted_after_upload():
    a = StoredFile("v0", "f0", {"article_id": "1", "content_hash": "x"})
    b = StoredFile("v1", "f1", {"article_id": "1", "content_hash": "y"})
    store = FakeStore([a, b])
    result, _ = run([art(1)], store)
    assert result.updated == 1
    names = [c[0] for c in store.calls]
    assert names.index("upload_many") < names.index("delete")
    assert [f.vs_file_id for f in store.files] == ["vsf_new1"]


def test_convert_exception_is_isolated():
    old = stored_for(convert(art(1)))
    store = FakeStore([old])
    changed = art(1, updated="2026-02-02T00:00:00Z")
    result, _ = run([changed, art(2)], store, spy=Spy(raises={1}))
    assert (result.failed, result.added) == (1, 1)
    assert result.failures[0][0] == 1
    assert old in store.files


def failure_flag(total, bad):
    store = FakeStore(fail_ids=set(range(1, bad + 1)))
    result, _ = run([art(i) for i in range(1, total + 1)], store)
    return result.too_many_failures


def test_too_many_failures_threshold():
    assert failure_flag(5, 1) is False
    assert failure_flag(5, 2) is True
    assert failure_flag(1, 1) is True
    assert failure_flag(0, 0) is False


def test_upload_many_called_once_or_not_at_all():
    store = FakeStore()
    run([art(1), art(2), art(3)], store)
    assert [c[0] for c in store.calls].count("upload_many") == 1
    store2 = FakeStore(files_for(1))
    run([art(1)], store2)
    assert "upload_many" not in [c[0] for c in store2.calls]


def test_stored_file_with_non_numeric_article_id_is_ignored(caplog):
    bad = StoredFile("v0", "f0", {"article_id": "abc"})
    store = FakeStore([bad, *files_for(10)])
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        result, _ = run([art(i) for i in range(2, 11)], store)
    assert result.deleted == 1
    assert bad in store.files
    assert any("bad article_id" in m for m in caplog.messages)


def test_duplicate_listing_ids_are_uploaded_once(caplog):
    store = FakeStore()
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        result, _ = run([art(1, body="<p>first</p>"), art(1, body="<p>second</p>")], store)
    assert result.added == 1
    assert store.calls[1] == ("upload_many", [1])
    assert "first" in store.uploaded[0].markdown
    assert len([m for m in caplog.messages if "duplicate" in m]) == 1
