#!/usr/bin/env bash
# Walks the demo video in order against the DEV vector store.
# Set DEMO_NO_PAUSE=1 to run without waiting for Enter.
set -euo pipefail
set +x
cd "$(dirname "$0")/.."

# Same precedence as src/kbsync/config.py: the environment wins over .env.
store="${VECTOR_STORE_NAME:-}"
if [ -z "$store" ] && [ -f .env ]; then
  store="$(grep -E '^VECTOR_STORE_NAME=' .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"'\r' || true)"
fi
store="${store:-optibot-kb}"
case "$store" in
  optibot-kb-dev*) ;;
  *)
    echo "demo.sh uses the dev store: set VECTOR_STORE_NAME=optibot-kb-dev in .env" >&2
    exit 2
    ;;
esac

# A pinned store id wins over the name in the store adapter, so refuse it here.
store_id="${VECTOR_STORE_ID:-}"
if [ -z "$store_id" ] && [ -f .env ]; then
  store_id="$(grep -E '^VECTOR_STORE_ID=' .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"'\r' || true)"
fi
if [ -n "$store_id" ]; then
  echo "demo.sh uses the dev store by name: leave VECTOR_STORE_ID empty" >&2
  exit 2
fi

# Article id of "How do I add a YouTube video?" (tests/retrieval/questions.json).
YOUTUBE_ARTICLE=360051014713

step() {
  printf '\n== %s\n' "$1"
  if [ "${DEMO_NO_PAUSE:-0}" != "1" ]; then
    read -r -p "Press Enter to run... " _
  fi
}

step "Tests: the suite runs offline and green (test doc: tests, no hard-coded keys)"
uv run pytest -q

step "Scrape and sync: Zendesk to Markdown to the dev vector store (Part 1: scrape, convert, upload the delta)"
uv run main.py

step "Delta: drop one article and add a ghost, then one run shows added=1 and deleted=1 (Part 1: upload only what changed)"
uv run scripts/dev_store_tools.py drop "$YOUTUBE_ARTICLE"
uv run scripts/dev_store_tools.py ghost
uv run main.py

step "Ask: OptiBot answers with cited sources (Part 1: assistant answers from the vector store)"
uv run ask.py "How do I add a YouTube video?"

step "Daily job log: latest run report served publicly (Part 1: proof the job runs daily)"
curl -fsS https://kbsync.bahuynh.com/runs/latest.json || echo "(the public log is not reachable yet: the server job is not deployed)"
echo
