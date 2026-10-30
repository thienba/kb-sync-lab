import logging
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import openai

from kbsync.convert import CONVERTER_VERSION, ConvertedArticle
from kbsync.errors import ConfigError, StoreError
from kbsync.logs import redact
from kbsync.sync import StoredFile, UploadResult, attributes_for

log = logging.getLogger("kbsync")

BATCH_SIZE = 500
PAGE_SIZE = 100
SMOKE_QUESTION = "How do I add a YouTube video?"


@dataclass(frozen=True)
class StoreStats:
    file_counts: dict[str, int]
    usage_bytes: int


def estimate_chunks(tokens: int, max_tokens: int, overlap: int) -> int:
    """OpenAI reports no chunk count; this is an estimate for static chunking."""
    if tokens <= 0:
        return 0
    if tokens <= max_tokens:
        return 1
    return math.ceil((tokens - overlap) / (max_tokens - overlap))


FATAL = (openai.AuthenticationError, openai.APIConnectionError, openai.InternalServerError)


@contextmanager
def _translated() -> Iterator[None]:
    try:
        yield
    except openai.AuthenticationError:
        raise ConfigError("API_KEY was rejected by OpenAI. Check the key and try again.") from None
    except FATAL as exc:
        raise StoreError(
            f"Cannot reach the OpenAI vector store: {redact(str(exc))}. Check API_KEY and network."
        ) from None
    except openai.APIStatusError as exc:
        raise StoreError(f"OpenAI rejected the vector store request: {redact(str(exc))}.") from None


class OpenAIStore:
    """The Store protocol on an OpenAI vector store (one batch of at most 500 files at a time)."""

    def __init__(
        self,
        client: openai.OpenAI,
        name: str = "optibot-kb",
        store_id: str | None = None,
        create: bool = True,
        chunk_max_tokens: int = 600,
        chunk_overlap_tokens: int = 150,
        converter_version: str = CONVERTER_VERSION,
        poll_interval: float = 2.0,
        timeout: float = 600,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.name = name
        self._store_id = store_id
        self.create = create
        self._resolved = False
        self.chunking_strategy = {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": chunk_max_tokens,
                "chunk_overlap_tokens": chunk_overlap_tokens,
            },
        }
        self.converter_version = converter_version
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock

    @property
    def store_id(self) -> str:
        if not self._resolved:
            self._store_id = self._resolve()
            self._resolved = True
        assert self._store_id is not None
        return self._store_id

    def _resolve(self) -> str:
        stores = self.client.vector_stores
        with _translated():
            if self._store_id:
                try:
                    return stores.retrieve(self._store_id).id
                except openai.NotFoundError:
                    raise StoreError(
                        f"VECTOR_STORE_ID {self._store_id} was not found for this key."
                    ) from None
            after = None
            while True:
                page = stores.list(limit=PAGE_SIZE, **({"after": after} if after else {}))
                for found in page.data:
                    if found.name == self.name:
                        return found.id
                if not page.has_more:
                    break
                after = page.data[-1].id
            if not self.create:
                raise StoreError(f"Vector store '{self.name}' not found. Run main.py first.")
            log.info("Creating vector store %s", self.name)
            return stores.create(name=self.name).id

    def list_files(self) -> list[StoredFile]:
        files: list[StoredFile] = []
        after = None
        with _translated():
            while True:
                page = self.client.vector_stores.files.list(
                    self.store_id, limit=PAGE_SIZE, **({"after": after} if after else {})
                )
                files.extend(
                    StoredFile(f.id, f.id, {k: str(v) for k, v in (f.attributes or {}).items()})
                    for f in page.data
                )
                if not page.has_more or not page.data:
                    return files
                after = page.data[-1].id

    def upload_many(self, items: list[ConvertedArticle]) -> dict[int, UploadResult]:
        results: dict[int, UploadResult] = {}
        for start in range(0, len(items), BATCH_SIZE):
            results.update(self._upload_batch(items[start : start + BATCH_SIZE]))
        return results

    def _upload_batch(self, items: list[ConvertedArticle]) -> dict[int, UploadResult]:
        results: dict[int, UploadResult] = {}
        entries = []
        article_by_file: dict[str, int] = {}
        with _translated():
            for item in items:
                try:
                    created = self.client.files.create(
                        file=(item.filename, item.markdown.encode("utf-8")), purpose="assistants"
                    )
                except FATAL:
                    raise
                except openai.OpenAIError as exc:
                    results[item.article_id] = UploadResult(False, error=redact(str(exc)))
                    continue
                article_by_file[created.id] = item.article_id
                entries.append(
                    {
                        "file_id": created.id,
                        "attributes": attributes_for(item, self.converter_version),
                        "chunking_strategy": self.chunking_strategy,
                    }
                )
            if not entries:
                return results
            batch = self.client.vector_stores.file_batches.create(self.store_id, files=entries)
            self._wait(batch.id)
            after = None
            seen: set[str] = set()
            while True:
                page = self.client.vector_stores.file_batches.list_files(
                    batch.id,
                    vector_store_id=self.store_id,
                    limit=PAGE_SIZE,
                    **({"after": after} if after else {}),
                )
                for f in page.data:
                    seen.add(f.id)
                    article_id = article_by_file[f.id]
                    if f.status == "completed":
                        results[article_id] = UploadResult(True, f.id, f.id)
                    else:
                        message = f.last_error.message if f.last_error else f"file {f.status}"
                        results[article_id] = UploadResult(False, f.id, f.id, error=message)
                if not page.has_more or not page.data:
                    break
                after = page.data[-1].id
        for file_id, article_id in article_by_file.items():
            if file_id not in seen:
                results[article_id] = UploadResult(False, file_id, file_id, "missing from batch")
        return results

    def _wait(self, batch_id: str) -> None:
        deadline = self._clock() + self.timeout
        while True:
            batch = self.client.vector_stores.file_batches.retrieve(
                batch_id, vector_store_id=self.store_id
            )
            if batch.status != "in_progress":
                return
            if self._clock() >= deadline:
                raise StoreError(
                    f"Timed out waiting for the vector store batch after {int(self.timeout)}s."
                )
            self._sleep(self.poll_interval)

    def delete(self, stored: StoredFile) -> None:
        with _translated():
            self.client.vector_stores.files.delete(stored.vs_file_id, vector_store_id=self.store_id)
            try:
                self.client.files.delete(stored.file_id)
            except openai.AuthenticationError:
                raise
            except openai.OpenAIError as exc:
                log.warning("Could not delete file %s: %s", stored.file_id, redact(str(exc)))

    def stats(self, chunks: int) -> StoreStats:
        """Read file counts and size; `chunks` is the caller's estimate (see estimate_chunks).

        The counts lag right after deletes, so the log line leaves them out: the run's
        summary line reports the files it listed.
        """
        with _translated():
            vs = self.client.vector_stores.retrieve(self.store_id)
        counts = {
            "completed": vs.file_counts.completed,
            "failed": vs.file_counts.failed,
            "in_progress": vs.file_counts.in_progress,
            "total": vs.file_counts.total,
        }
        log.info(
            "Vector store %s: usage_bytes=%s chunks≈%d (estimate)",
            self.name,
            f"{vs.usage_bytes:,}",
            chunks,
        )
        return StoreStats(counts, vs.usage_bytes)


def smoke_search(store: OpenAIStore, question: str = SMOKE_QUESTION) -> str | None:
    try:
        hits = store.client.vector_stores.search(store.store_id, query=question, max_num_results=1)
        url = str((hits.data[0].attributes or {})["url"])
    except Exception as exc:  # noqa: BLE001 - the smoke check must never fail the run
        log.warning("Smoke check skipped: %s", redact(str(exc) or type(exc).__name__))
        return None
    log.info('Smoke check: top hit for "%s" -> %s', question, url)
    return url
