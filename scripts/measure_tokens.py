"""Fetch every non-draft article, convert it, write out/articles/ and print token statistics.

Needs the network (public Zendesk API, no key). Exits 1 if any output file is not clean.
"""

import logging
import statistics
import sys
from pathlib import Path

from kbsync.convert import convert_all, violations, write_all
from kbsync.tokens import token_counter
from kbsync.zendesk import fetch_articles

OUT = Path("out") / "articles"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    listing = fetch_articles()
    converted, failed = convert_all(listing.articles)
    failed.update(write_all(converted, OUT))
    count = token_counter()
    tokens = sorted(count(item.markdown) for item in converted)
    bad = {item.filename: violations(item.markdown) for item in converted}
    bad = {name: found for name, found in bad.items() if found}
    for article_id, reason in failed.items():
        print(f"failed {article_id}: {reason}", file=sys.stderr)
    for name, found in bad.items():
        print(f"unclean {name}: {'; '.join(found)}", file=sys.stderr)
    if tokens:
        p95 = tokens[min(len(tokens) - 1, int(len(tokens) * 0.95))]
        print(
            f"articles={len(tokens)} tokens min={tokens[0]} "
            f"median={int(statistics.median(tokens))} p95={p95} max={tokens[-1]} "
            f"total={sum(tokens)}"
        )
    return 1 if failed or bad or not tokens else 0


if __name__ == "__main__":
    sys.exit(main())
