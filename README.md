# kb-sync-lab

A daily job that pulls help-center articles, converts them to clean Markdown, and keeps an OpenAI vector store in sync by uploading only what changed. A small CLI asks the knowledge base questions with cited sources.

## Build, run and test
```
uv sync --locked
uv run pytest -q
cp .env.sample .env   # then set API_KEY
uv run main.py
docker build -t kbsync . && docker run --rm -e API_KEY=... kbsync
```

## Chunking

Files are chunked statically at 600 tokens with 150 overlap (`CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS`).
Measured against 800/400 on 10 questions, both found the expected article 9/10 times; see `docs/chunking.md`.

_The full README (setup, run locally, daily job logs, screenshot) is coming next._
