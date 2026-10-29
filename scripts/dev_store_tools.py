"""Dev store helpers: `drop <article_id>` and `ghost`; only for stores named optibot-kb-dev*."""

import argparse
import sys

import openai

from kbsync.config import load_config
from kbsync.convert import ConvertedArticle
from kbsync.errors import ConfigError, run
from kbsync.logs import setup_logging
from kbsync.store import OpenAIStore

DEV_PREFIX = "optibot-kb-dev"
GHOST_ID = 999999999


def _store() -> OpenAIStore:
    cfg = load_config()
    if not cfg.vector_store_name.startswith(DEV_PREFIX) or cfg.vector_store_id:
        raise ConfigError(
            f"Refusing to run: VECTOR_STORE_NAME must start with {DEV_PREFIX} "
            "and VECTOR_STORE_ID must be empty."
        )
    return OpenAIStore(openai.OpenAI(api_key=cfg.api_key), cfg.vector_store_name, create=False)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    drop = sub.add_parser("drop", help="delete one article's file from the dev store")
    drop.add_argument("article_id")
    sub.add_parser("ghost", help="upload a file whose article_id is not in the listing")
    args = parser.parse_args()
    store = _store()
    if args.command == "drop":
        found = [f for f in store.list_files() if f.attributes.get("article_id") == args.article_id]
        if not found:
            raise ConfigError(f"No file with article_id {args.article_id} in {store.name}.")
        for stored in found:
            store.delete(stored)
        print(f"dropped {len(found)} file(s) for article {args.article_id}")
    else:
        item = ConvertedArticle(
            GHOST_ID,
            "Ghost",
            "https://support.optisigns.com/hc/en-us/articles/999999999-ghost",
            "2026-01-01T00:00:00Z",
            f"{GHOST_ID}-ghost.md",
            "# Ghost\n\nTemporary file for the delta check.\n",
            "ghost",
        )
        result = store.upload_many([item])[GHOST_ID]
        if not result.ok:
            raise ConfigError(f"Ghost upload failed: {result.error}")
        print("uploaded ghost article 999999999")


if __name__ == "__main__":
    sys.argv[0] = "dev_store_tools.py"
    run(main)
