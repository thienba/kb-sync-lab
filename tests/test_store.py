import logging
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest

from kbsync.config import load_config
from kbsync.convert import ConvertedArticle
from kbsync.errors import ConfigError, StoreError
from kbsync.store import OpenAIStore, estimate_chunks, smoke_search
from kbsync.sync import StoredFile

REQ = httpx2.Request("GET", "https://api.openai.com/v1/x")


def status_error(cls, code):
    return cls("boom", response=httpx2.Response(code, request=REQ), body=None)


def page(items, has_more=False):
    return NS(data=items, has_more=has_more)


class Calls:
    """Records calls; each leaf is a method returning scripted values."""

    def __init__(self):
        self.log = []

    def rec(self, call, /, **kw):
        self.log.append((call, kw))

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


class FakeClient:
    def __init__(self):
        self.c = Calls()
        self.stores_pages = []
        self.created_id = "vs_new"
        self.retrieve_error = None
        self.file_counter = 0
        self.batch_statuses = ["completed"]
        self.failed_files = {}
        self.vs_files_pages = []
        self.file_delete_error = None
        self.search_result = None
        self.search_error = None
        self.create_error = None
        c = self
        self.vector_stores = NS(
            retrieve=c._vs_retrieve,
            list=c._vs_list,
            create=c._vs_create,
            search=c._search,
            files=NS(list=c._vsf_list, delete=c._vsf_delete),
            file_batches=NS(
                create=c._batch_create, retrieve=c._batch_retrieve, list_files=c._batch_files
            ),
        )
        self.files = NS(create=c._file_create, delete=c._file_delete)
        self.batches = []

    def _vs_retrieve(self, store_id):
        self.c.rec("vs.retrieve", id=store_id)
        if self.retrieve_error:
            raise self.retrieve_error
        return NS(
            id=store_id,
            file_counts=NS(completed=3, failed=1, in_progress=0, total=4, cancelled=0),
            usage_bytes=1234567,
        )

    def _vs_list(self, **kw):
        self.c.rec("vs.list", **kw)
        return self.stores_pages.pop(0)

    def _vs_create(self, **kw):
        self.c.rec("vs.create", **kw)
        return NS(id=self.created_id)

    def _file_create(self, **kw):
        self.c.rec("files.create", **kw)
        if self.create_error:
            raise self.create_error
        self.file_counter += 1
        return NS(id=f"file_{self.file_counter}")

    def _file_delete(self, file_id):
        self.c.rec("files.delete", id=file_id)
        if self.file_delete_error:
            raise self.file_delete_error

    def _vsf_delete(self, file_id, **kw):
        self.c.rec("vsf.delete", id=file_id, **kw)

    def _vsf_list(self, store_id, **kw):
        self.c.rec("vsf.list", **kw)
        return self.vs_files_pages.pop(0)

    def _batch_create(self, store_id, **kw):
        self.c.rec("batch.create", store=store_id, **kw)
        self.batches.append(kw["files"])
        return NS(id=f"batch_{len(self.batches)}")

    def _batch_retrieve(self, batch_id, **kw):
        self.c.rec("batch.retrieve", id=batch_id)
        status = self.batch_statuses.pop(0) if len(self.batch_statuses) > 1 else "completed"
        if self.batch_statuses == ["in_progress"]:
            status = "in_progress"
        return NS(id=batch_id, status=status)

    def _batch_files(self, batch_id, **kw):
        self.c.rec("batch.list_files", id=batch_id, **kw)
        entries = self.batches[int(batch_id.split("_")[1]) - 1]
        out = []
        for e in entries:
            err = self.failed_files.get(e["file_id"])
            out.append(
                NS(
                    id=e["file_id"],
                    status="failed" if err else "completed",
                    last_error=NS(message=err) if err else None,
                )
            )
        return page(out)

    def _search(self, store_id, **kw):
        self.c.rec("search", **kw)
        if self.search_error:
            raise self.search_error
        return NS(data=self.search_result)


def item(n, url="https://support.optisigns.com/a"):
    return ConvertedArticle(n, f"T{n}", url, "2026-01-01T00:00:00Z", f"{n}-t.md", "# md", f"h{n}")


def make(client, **kw):
    kw.setdefault("sleep", lambda s: None)
    return OpenAIStore(client, **kw)


def test_store_id_set_is_retrieved_only():
    c = FakeClient()
    assert make(c, store_id="vs_1").store_id == "vs_1"
    assert [n for n, _ in c.c.log] == ["vs.retrieve"]


def test_store_id_not_found():
    c = FakeClient()
    c.retrieve_error = status_error(openai.NotFoundError, 404)
    with pytest.raises(StoreError) as e:
        _ = make(c, store_id="vs_x").store_id
    assert str(e.value) == "VECTOR_STORE_ID vs_x was not found for this key."


def test_store_found_by_name_over_three_pages():
    c = FakeClient()
    c.stores_pages = [
        page([NS(id="a", name="x")], True),
        page([NS(id="b", name="y")], True),
        page([NS(id="c", name="optibot-kb")]),
    ]
    assert make(c).store_id == "c"
    assert c.c.count("vs.list") == 3
    assert c.c.count("vs.create") == 0
    assert c.c.log[1][1]["after"] == "a"


def test_store_found_returns_its_id():
    c = FakeClient()
    c.stores_pages = [page([NS(id="c", name="optibot-kb")])]
    assert make(c).store_id == "c"


def test_store_created_once_when_missing():
    c = FakeClient()
    c.stores_pages = [page([NS(id="a", name="other")])]
    s = make(c)
    assert s.store_id == "vs_new"
    assert s.store_id == "vs_new"
    assert c.c.count("vs.create") == 1
    assert [kw for n, kw in c.c.log if n == "vs.create"] == [{"name": "optibot-kb"}]


def test_upload_many_files_form():
    c = FakeClient()
    s = make(c, store_id="vs_1", chunk_max_tokens=700, chunk_overlap_tokens=100)
    res = s.upload_many([item(1), item(2), item(3)])
    assert c.c.count("files.create") == 3
    assert c.c.count("batch.create") == 1
    entries = c.batches[0]
    assert [e["file_id"] for e in entries] == ["file_1", "file_2", "file_3"]
    assert set(entries[0]["attributes"]) == {
        "article_id",
        "content_hash",
        "converter_version",
        "source_updated_at",
        "url",
    }
    assert all(isinstance(v, str) and len(v) <= 256 for v in entries[0]["attributes"].values())
    assert entries[0]["chunking_strategy"] == {
        "type": "static",
        "static": {"max_chunk_size_tokens": 700, "chunk_overlap_tokens": 100},
    }
    assert all(r.ok for r in res.values()) and set(res) == {1, 2, 3}


def test_1200_items_in_three_batches():
    c = FakeClient()
    res = make(c, store_id="vs_1").upload_many([item(n) for n in range(1200)])
    assert [len(b) for b in c.batches] == [500, 500, 200]
    assert len(res) == 1200


def test_poll_until_completed():
    c = FakeClient()
    c.batch_statuses = ["in_progress", "in_progress", "completed"]
    make(c, store_id="vs_1").upload_many([item(1)])
    assert c.c.count("batch.retrieve") == 3


def test_poll_timeout():
    c = FakeClient()
    c.batch_statuses = ["in_progress"]
    now = [0.0]

    def sleep(s):
        now[0] += s

    s = make(c, store_id="vs_1", poll_interval=100, sleep=sleep, clock=lambda: now[0])
    with pytest.raises(StoreError) as e:
        s.upload_many([item(1)])
    assert str(e.value) == "Timed out waiting for the vector store batch after 600s."


def test_failed_file_reported_others_ok():
    c = FakeClient()
    c.failed_files = {"file_2": "unsupported"}
    res = make(c, store_id="vs_1").upload_many([item(1), item(2), item(3)])
    assert res[2].ok is False and res[2].error == "unsupported"
    assert res[1].ok and res[3].ok


def test_list_files_three_pages():
    c = FakeClient()
    files = [NS(id=f"f{i}", attributes={"article_id": str(i)}) for i in range(250)]
    c.vs_files_pages = [page(files[:100], True), page(files[100:200], True), page(files[200:])]
    out = make(c, store_id="vs_1").list_files()
    assert len(out) == 250 and out[7] == StoredFile("f7", "f7", {"article_id": "7"})
    calls = [kw for n, kw in c.c.log if n == "vsf.list"]
    assert [k.get("after") for k in calls] == [None, "f99", "f199"]
    assert all(k["limit"] == 100 for k in calls)


def test_delete_order_and_warning_on_file_failure(caplog):
    c = FakeClient()
    c.file_delete_error = status_error(openai.BadRequestError, 400)
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        make(c, store_id="vs_1").delete(StoredFile("f1", "f1", {}))
    assert [n for n, _ in c.c.log if n.endswith("delete")] == ["vsf.delete", "files.delete"]
    assert "Could not delete file f1: boom" in caplog.text


def test_config_chunk_validation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "99")
    with pytest.raises(ConfigError, match="CHUNK_MAX_TOKENS must be between 100 and 4096"):
        load_config()
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "600")
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "301")
    with pytest.raises(ConfigError, match="CHUNK_OVERLAP_TOKENS must be between 0 and 300"):
        load_config()
    monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "300")
    assert load_config().chunk_overlap_tokens == 300


@pytest.mark.parametrize(
    ("tokens", "expected"), [(0, 0), (500, 1), (600, 1), (601, 2), (1050, 2), (1051, 3)]
)
def test_estimate_chunks(tokens, expected):
    assert estimate_chunks(tokens, 600, 150) == expected


def test_stats_logs_line(caplog):
    c = FakeClient()
    with caplog.at_level(logging.INFO, logger="kbsync"):
        st = make(c, store_id="vs_1").stats(chunks=9)
    assert st.file_counts == {"completed": 3, "failed": 1, "in_progress": 0, "total": 4}
    assert st.usage_bytes == 1234567
    assert "Vector store optibot-kb: files=4 usage_bytes=1,234,567 chunks≈9 (estimate)" in (
        caplog.text
    )


def test_smoke_search_logs_url(caplog):
    c = FakeClient()
    c.search_result = [NS(attributes={"url": "https://x/y"})]
    with caplog.at_level(logging.INFO, logger="kbsync"):
        url = smoke_search(make(c, store_id="vs_1"), "How do I add a YouTube video?")
    assert url == "https://x/y"
    assert 'Smoke check: top hit for "How do I add a YouTube video?" -> https://x/y' in caplog.text


def test_smoke_search_error_is_warning(caplog):
    c = FakeClient()
    c.search_error = status_error(openai.BadRequestError, 400)
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        assert smoke_search(make(c, store_id="vs_1")) is None
    assert "Smoke check skipped: boom" in caplog.text


def test_401_becomes_config_error():
    c = FakeClient()
    c.retrieve_error = status_error(openai.AuthenticationError, 401)
    with pytest.raises(ConfigError) as e:
        _ = make(c, store_id="vs_1").store_id
    assert str(e.value) == "API_KEY was rejected by OpenAI. Check the key and try again."


@pytest.mark.parametrize(
    "err",
    [openai.APIConnectionError(request=REQ), status_error(openai.InternalServerError, 500)],
)
def test_connection_and_5xx_become_store_error(err):
    c = FakeClient()
    c.create_error = err
    s = make(c, store_id="vs_1")
    with pytest.raises(StoreError, match="Cannot reach the OpenAI vector store: .*API_KEY and net"):
        s.upload_many([item(1)])


def test_no_key_in_error_messages():
    c = FakeClient()
    c.create_error = openai.APIConnectionError(
        message="bad " + "sk-" + "abcdefghijklmnopqrstuvwxyz", request=REQ
    )
    with pytest.raises(StoreError) as e:
        make(c, store_id="vs_1").upload_many([item(1)])
    assert "sk-abc" not in str(e.value)


@pytest.mark.parametrize(
    "err", [status_error(openai.RateLimitError, 429), status_error(openai.BadRequestError, 400)]
)
def test_other_status_errors_on_batch_create_become_store_error(err):
    c = FakeClient()
    c._batch_create = lambda store_id, **kw: (_ for _ in ()).throw(err)
    c.vector_stores.file_batches.create = c._batch_create
    with pytest.raises(StoreError, match="OpenAI rejected the vector store request: boom"):
        make(c, store_id="vs_1").upload_many([item(1)])


@pytest.mark.parametrize(
    "err",
    [status_error(openai.InternalServerError, 500), openai.APIConnectionError(request=REQ)],
)
def test_file_delete_failure_only_warns(err, caplog):
    c = FakeClient()
    c.file_delete_error = err
    with caplog.at_level(logging.WARNING, logger="kbsync"):
        make(c, store_id="vs_1").delete(StoredFile("f1", "f1", {}))
    assert "Could not delete file f1" in caplog.text


def test_file_delete_401_is_config_error():
    c = FakeClient()
    c.file_delete_error = status_error(openai.AuthenticationError, 401)
    with pytest.raises(ConfigError):
        make(c, store_id="vs_1").delete(StoredFile("f1", "f1", {}))


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_batch_failed_or_cancelled_marks_every_file(status):
    c = FakeClient()
    c.vector_stores.file_batches.list_files = lambda batch_id, **kw: page(
        [NS(id=f"file_{n}", status=status, last_error=None) for n in (1, 2)]
    )
    c.batch_statuses = [status]
    res = make(c, store_id="vs_1").upload_many([item(1), item(2)])
    assert [r.ok for r in res.values()] == [False, False]
    assert res[1].error == f"file {status}"
