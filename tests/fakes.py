from dataclasses import replace

from kbsync.convert import CONVERTER_VERSION, ConvertedArticle
from kbsync.sync import StoredFile, UploadResult, attributes_for


class FakeStore:
    """In-memory Store that records every call, in order, in `calls`."""

    def __init__(self, files: list[StoredFile] | None = None, fail_ids=()):
        self.files = list(files or [])
        self.fail_ids = set(fail_ids)
        self.calls: list[tuple] = []
        self.uploaded: list[ConvertedArticle] = []
        self.uploaded_attributes: dict[int, dict[str, str]] = {}
        self._next = 0

    def list_files(self) -> list[StoredFile]:
        self.calls.append(("list_files",))
        return list(self.files)

    def upload_many(self, items: list[ConvertedArticle]) -> dict[int, UploadResult]:
        self.calls.append(("upload_many", [i.article_id for i in items]))
        results = {}
        for item in items:
            if item.article_id in self.fail_ids:
                results[item.article_id] = UploadResult(ok=False, error="upload failed")
                continue
            self._next += 1
            attrs = attributes_for(item, CONVERTER_VERSION)
            self.uploaded.append(item)
            self.uploaded_attributes[item.article_id] = attrs
            stored = StoredFile(f"vsf_new{self._next}", f"file_new{self._next}", attrs)
            self.files.append(stored)
            results[item.article_id] = UploadResult(True, stored.vs_file_id, stored.file_id)
        return results

    def delete(self, stored: StoredFile) -> None:
        self.calls.append(("delete", stored.vs_file_id))
        self.calls.append(("delete_file", stored.file_id))
        self.files = [f for f in self.files if f.vs_file_id != stored.vs_file_id]


def stored_for(item: ConvertedArticle, version: str = CONVERTER_VERSION, n: int = 0) -> StoredFile:
    return StoredFile(
        f"vsf_{item.article_id}_{n}", f"file_{item.article_id}_{n}", attributes_for(item, version)
    )


def with_hash(item: ConvertedArticle, body_hash: str) -> ConvertedArticle:
    return replace(item, body_hash=body_hash)
