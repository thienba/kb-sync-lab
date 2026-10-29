import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from kbsync.convert import ConvertedArticle
from kbsync.zendesk import Article

log = logging.getLogger("kbsync")

ATTRIBUTE_MAX = 256
MAX_UPLOAD_FAILURE_RATE = 0.20


@dataclass(frozen=True)
class StoredFile:
    vs_file_id: str
    file_id: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    vs_file_id: str | None = None
    file_id: str | None = None
    error: str | None = None


class Store(Protocol):
    def list_files(self) -> list[StoredFile]: ...

    def upload_many(self, items: list[ConvertedArticle]) -> dict[int, UploadResult]: ...

    def delete(self, stored: StoredFile) -> None:
        """Remove the vector-store file and the File object."""


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    held_back: list[int] = field(default_factory=list)
    changes: list[dict[str, object]] = field(default_factory=list)
    failures: list[tuple[int, str]] = field(default_factory=list)
    too_many_failures: bool = False


def attributes_for(item: ConvertedArticle, converter_version: str) -> dict[str, str]:
    values = {
        "article_id": str(item.article_id),
        "content_hash": item.body_hash,
        "converter_version": converter_version,
        "source_updated_at": item.updated_at,
        "url": item.url,
    }
    return {key: value[:ATTRIBUTE_MAX] for key, value in values.items()}


def _cleanup(store: Store, files: Sequence[StoredFile], what: str) -> bool:
    ok = True
    for stored in files:
        try:
            store.delete(stored)
        except Exception as exc:  # noqa: BLE001 - leftovers are cleaned on a later run
            ok = False
            log.warning("Could not delete %s %s: %s", what, stored.vs_file_id, exc)
    return ok


def sync(
    articles: Sequence[Article],
    listing_complete: bool,
    store: Store,
    convert: Callable[[Article], ConvertedArticle],
    converter_version: str,
    full: bool = False,
    delete_threshold: float = 0.10,
) -> SyncResult:
    result = SyncResult()
    unique = list({a.id: a for a in reversed(articles)}.values())[::-1]
    if len(unique) != len(articles):
        log.warning("Listing has duplicate article ids; keeping the first of each")
    articles = unique
    stored_by_id: dict[str, list[StoredFile]] = {}
    for stored in store.list_files():
        key = stored.attributes.get("article_id")
        if key is not None and key.isdecimal():
            stored_by_id.setdefault(key, []).append(stored)
        else:
            log.warning("Ignoring stored file %s: bad article_id %r", stored.vs_file_id, key)

    to_upload: list[ConvertedArticle] = []
    old_versions: dict[int, list[StoredFile]] = {}
    duplicates: list[StoredFile] = []
    kinds: dict[int, str] = {}

    for article in articles:
        group = stored_by_id.get(str(article.id), [])
        if (
            not full
            and len(group) == 1
            and group[0].attributes.get("source_updated_at") == article.updated_at
            and group[0].attributes.get("converter_version") == converter_version
        ):
            result.skipped += 1
            continue
        try:
            item = convert(article)
        except Exception as exc:  # noqa: BLE001 - per-article isolation is the requirement
            result.failed += 1
            result.failures.append((article.id, f"{type(exc).__name__}: {exc}"))
            continue
        matching = [f for f in group if f.attributes.get("content_hash") == item.body_hash]
        if matching:
            result.skipped += 1
            duplicates.extend(f for f in group if f is not matching[0])
            continue
        to_upload.append(item)
        old_versions[article.id] = group
        kinds[article.id] = "updated" if group else "added"

    upload_failed = 0
    if to_upload:
        uploads = store.upload_many(to_upload)
        for item in to_upload:
            outcome = uploads.get(item.article_id)
            if outcome is None or not outcome.ok:
                upload_failed += 1
                result.failed += 1
                reason = (outcome.error if outcome else None) or "upload failed"
                result.failures.append((item.article_id, reason))
                continue
            if kinds[item.article_id] == "added":
                result.added += 1
            else:
                result.updated += 1
            result.changes.append(
                {
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "change": kinds[item.article_id],
                }
            )
            duplicates.extend(old_versions[item.article_id])
        result.too_many_failures = upload_failed / len(to_upload) > MAX_UPLOAD_FAILURE_RATE

    _cleanup(store, duplicates, "old file")

    if listing_complete and stored_by_id:
        listed = {str(a.id) for a in articles}
        missing = sorted(key for key in stored_by_id if key not in listed)
        if len(missing) / len(stored_by_id) <= delete_threshold:
            for key in missing:
                if _cleanup(store, stored_by_id[key], "missing article file"):
                    result.deleted += 1
                    result.changes.append(
                        {
                            "article_id": int(key),
                            "title": f"Article {key}",
                            "url": stored_by_id[key][0].attributes.get("url", ""),
                            "change": "deleted",
                        }
                    )
        elif missing:
            result.held_back = [int(key) for key in missing]
            log.warning(
                "Delete held back: %d of %d stored articles are missing from Zendesk "
                "(limit %d%%). Nothing deleted.",
                len(missing),
                len(stored_by_id),
                round(delete_threshold * 100),
            )
    return result
