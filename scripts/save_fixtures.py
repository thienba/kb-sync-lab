"""One-off: save raw Zendesk article JSON as offline test fixtures (public API, no auth)."""

import json
import re
import sys
from pathlib import Path

from kbsync.zendesk import LISTING_URL, get_page

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "articles"
MAX_BYTES = 150_000
# One article per element the golden tests need to cover.
WANTED = {
    "h2": r"<h2", "h3": r"<h3", "ol": r"<ol", "ul": r"<ul", "table": r"<table",
    "img": r"<img", "youtube": r"<iframe[^>]+youtube", "code": r"<pre|<code",
}  # fmt: skip


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    chosen: dict[int, dict] = {}
    missing = set(WANTED)
    url: str | None = LISTING_URL
    while url and missing:
        page = get_page(url)
        for item in page["articles"]:
            body = item.get("body") or ""
            if item.get("draft") or len(json.dumps(item)) > MAX_BYTES:
                continue
            hits = {k for k in missing if re.search(WANTED[k], body, re.I)}
            if hits:
                chosen[item["id"]] = item
                missing -= hits
        url = page.get("next_page")
    if len(chosen) < 8:  # pad with the first short articles so the set is varied
        for item in get_page(LISTING_URL)["articles"]:
            if not item.get("draft") and len(json.dumps(item)) < MAX_BYTES:
                chosen.setdefault(item["id"], item)
            if len(chosen) >= 8:
                break
    for article_id, item in chosen.items():
        (OUT / f"{article_id}.json").write_text(json.dumps(item, indent=2, ensure_ascii=False))
    print(f"saved {len(chosen)} articles to {OUT}; missing elements: {sorted(missing)}")
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
