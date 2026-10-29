import argparse
import logging
import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openai

from kbsync.config import Config, load_config
from kbsync.convert import CONVERTER_VERSION, ConvertedArticle, convert, write_all
from kbsync.errors import KbSyncError, ListingError, TooManyFailures
from kbsync.logs import redact
from kbsync.report import PingGet, build_report, ping, write_report
from kbsync.report import _ping_get as default_ping_get
from kbsync.store import OpenAIStore, estimate_chunks, smoke_search
from kbsync.sync import MAX_UPLOAD_FAILURE_RATE, Store, SyncResult, sync
from kbsync.tokens import token_counter
from kbsync.zendesk import Article, Listing, fetch_articles

log = logging.getLogger("kbsync")

NEXT_HINT = (
    'Next: uv run ask.py "How do I add a YouTube video?" (or use the Playground, see README)'
)
TRUE_VALUES = {"1", "true", "yes"}


def _write_report(public_dir: Path, report: dict[str, Any]) -> None:
    """Write the run report; a failure to write it must not change the exit code."""
    try:
        write_report(public_dir, report)
    except OSError as exc:
        log.warning("Could not write the run report to %s: %s", public_dir, redact(str(exc)))


def _make_store(cfg: Config) -> Store:
    client = openai.OpenAI(api_key=cfg.api_key)
    return OpenAIStore(
        client,
        cfg.vector_store_name,
        cfg.vector_store_id,
        chunk_max_tokens=cfg.chunk_max_tokens,
        chunk_overlap_tokens=cfg.chunk_overlap_tokens,
    )


def _smoke(store: Store) -> None:
    if isinstance(store, OpenAIStore):
        smoke_search(store)


def _execute(
    args: argparse.Namespace,
    state: dict[str, Any],
    fetch: Callable[..., Listing],
    make_store: Callable[[Config], Store],
    smoke: Callable[[Store], None],
) -> tuple[SyncResult, int, int]:
    cfg = load_config()
    out_dir = Path(os.environ.get("OUT_DIR") or "out")
    full = args.full or os.environ.get("FULL_REBUILD", "").lower() in TRUE_VALUES
    store = make_store(cfg)

    try:
        listing = fetch(args.limit)
    except ListingError as exc:
        raise ListingError(
            f"{str(exc).rstrip('.')}. Nothing was changed or deleted. Try again later."
        ) from None
    articles = listing.articles
    titles = {a.id: a.title for a in articles}
    state["titles"] = titles
    log.info("Listed %d articles", len(articles))
    if args.limit is not None:
        log.info("Limited run: nothing is deleted.")

    converted: dict[int, ConvertedArticle] = {}
    errors: dict[int, Exception] = {}
    for article in articles:
        try:
            converted[article.id] = convert(article)
        except Exception as exc:  # noqa: BLE001 - per-article isolation is the requirement
            errors[article.id] = exc
    for article_id, reason in write_all(converted.values(), out_dir / "articles").items():
        log.warning("Could not write article %d: %s", article_id, reason)

    asked: set[int] = set()

    def memo_convert(article: Article) -> ConvertedArticle:
        asked.add(article.id)
        if article.id in errors:
            raise errors[article.id]
        return converted[article.id]

    count = token_counter()
    chunks = sum(
        estimate_chunks(count(c.markdown), cfg.chunk_max_tokens, cfg.chunk_overlap_tokens)
        for c in converted.values()
    )

    result = sync(articles, listing.complete, store, memo_convert, CONVERTER_VERSION, full=full)
    state["result"] = result
    for article_id, reason in result.failures:
        log.warning("failed article %d (%s): %s", article_id, titles.get(article_id, ""), reason)

    if articles and not converted:
        raise TooManyFailures(
            f"Too many articles failed: 0 of {len(articles)} could be converted. "
            "Failed articles keep their old version and are retried on the next run."
        )
    convert_failed = len(asked & errors.keys())
    upload_failed = result.failed - convert_failed
    if result.too_many_failures:
        attempted = result.added + result.updated + upload_failed
        raise TooManyFailures(
            f"Too many uploads failed: {upload_failed} of {attempted} "
            f"(limit {round(MAX_UPLOAD_FAILURE_RATE * 100)}%). "
            "Failed articles keep their old version and are retried on the next run."
        )

    if isinstance(store, OpenAIStore):
        store.stats(chunks)
    smoke(store)
    return result, len(store.list_files()), chunks


def run(
    argv: Sequence[str] | None = None,
    *,
    fetch: Callable[..., Listing] = fetch_articles,
    make_store: Callable[[Config], Store] = _make_store,
    smoke: Callable[[Store], None] = _smoke,
    ping_get: PingGet = default_ping_get,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    parser = argparse.ArgumentParser(description="Sync the knowledge base into the vector store.")
    parser.add_argument("--limit", type=int, metavar="N", help="process at most N articles")
    parser.add_argument("--full", action="store_true", help="ignore saved state and rebuild all")
    args = parser.parse_args(argv)

    started_at = datetime.now(UTC)
    started = clock()
    public_dir = Path(os.environ.get("PUBLIC_DIR") or "public")
    heartbeat = os.environ.get("HEALTHCHECK_URL")
    state: dict[str, Any] = {}
    try:
        result, files, chunks = _execute(args, state, fetch, make_store, smoke)
    except KbSyncError as exc:
        failed = build_report(
            state.get("result", SyncResult()),
            started_at,
            clock() - started,
            None,
            None,
            CONVERTER_VERSION,
            error=str(exc),
            titles=state.get("titles", {}),
        )
        _write_report(public_dir, failed)
        ping(heartbeat, ok=False, get=ping_get)
        raise
    duration = clock() - started
    _write_report(
        public_dir,
        build_report(
            result, started_at, duration, files, chunks, CONVERTER_VERSION, titles=state["titles"]
        ),
    )
    ping(heartbeat, ok=True, get=ping_get)
    log.info(NEXT_HINT)
    log.info(
        "added=%d updated=%d skipped=%d deleted=%d failed=%d files=%d chunks≈%s in %ds",
        result.added,
        result.updated,
        result.skipped,
        result.deleted,
        result.failed,
        files,
        f"{chunks:,}",
        round(duration),
    )
