"""Retrieval check for chunk settings: python scripts/chunk_check.py 600/150 800/400

For each max/overlap pair this uploads the corpus to a temporary store named
optibot-kb-dev-c<max>-<overlap>, searches the questions in tests/retrieval/questions.json,
prints a markdown table (expected article in top 1 / top 3 results) and deletes the store.
"""

import json
import sys
from pathlib import Path

import openai

from kbsync.config import load_config
from kbsync.convert import convert_all
from kbsync.logs import setup_logging
from kbsync.store import OpenAIStore
from kbsync.zendesk import fetch_articles

QUESTIONS = Path(__file__).resolve().parents[1] / "tests" / "retrieval" / "questions.json"


def check(client: openai.OpenAI, name: str, max_tokens: int, overlap: int, converted, questions):
    store = OpenAIStore(client, name, chunk_max_tokens=max_tokens, chunk_overlap_tokens=overlap)
    try:
        results = store.upload_many(converted)
        failed = [i for i, r in results.items() if not r.ok]
        if failed:
            raise RuntimeError(f"{len(failed)} uploads failed in {name}: {failed[:5]}")
        rows = []
        for q in questions:
            hits = client.vector_stores.search(
                store.store_id, query=q["question"], max_num_results=3
            )
            ids = [int(h.attributes["article_id"]) for h in hits.data]
            top1 = bool(ids) and ids[0] in q["expected"]
            top3 = any(i in q["expected"] for i in ids)
            rows.append((q["question"], top1, top3))
        return rows
    finally:
        for stored in store.list_files():
            store.delete(stored)
        client.vector_stores.delete(store.store_id)


def main(argv: list[str]) -> int:
    setup_logging()
    cfg = load_config()
    client = openai.OpenAI(api_key=cfg.api_key)
    questions = json.loads(QUESTIONS.read_text())
    converted, failed = convert_all(fetch_articles().articles)
    if failed:
        print(f"conversion failed for {sorted(failed)}", file=sys.stderr)
        return 1
    for pair in argv:
        max_tokens, overlap = (int(n) for n in pair.split("/"))
        rows = check(
            client,
            f"optibot-kb-dev-c{max_tokens}-{overlap}",
            max_tokens,
            overlap,
            converted,
            questions,
        )
        print(f"\n{pair}: top1={sum(r[1] for r in rows)}/10 top3={sum(r[2] for r in rows)}/10")
        for question, top1, top3 in rows:
            print(f"| {question} | {'yes' if top1 else 'no'} | {'yes' if top3 else 'no'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
