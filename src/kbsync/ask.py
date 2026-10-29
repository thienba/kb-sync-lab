import argparse
import os
from pathlib import Path

import openai

from kbsync.config import load_config
from kbsync.errors import ConfigError
from kbsync.store import OpenAIStore, _translated

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "optibot.md"
NO_SOURCES = "Sources: none (the bot did not cite a file)"


def cited_file_ids(response) -> list[str]:
    ids: list[str] = []
    for item in response.output:
        if item.type != "message":
            continue
        for part in item.content:
            if part.type != "output_text":
                continue
            for note in part.annotations:
                if note.type == "file_citation" and note.file_id not in ids:
                    ids.append(note.file_id)
    return ids


def ask(question: str, prompt_file: str | None = None, client: openai.OpenAI | None = None) -> str:
    path = Path(prompt_file or os.environ.get("PROMPT_FILE") or DEFAULT_PROMPT_FILE)
    if not path.is_file():
        raise ConfigError(f"Prompt file not found: {path}")
    instructions = path.read_text(encoding="utf-8")
    cfg = load_config()
    client = client or openai.OpenAI(api_key=cfg.api_key)
    store = OpenAIStore(client, cfg.vector_store_name, cfg.vector_store_id, create=False)
    store_id = store.store_id
    with _translated():
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL,
            instructions=instructions,
            input=question,
            tools=[{"type": "file_search", "vector_store_ids": [store_id]}],
        )
        urls: list[str] = []
        for file_id in cited_file_ids(response):
            found = client.vector_stores.files.retrieve(file_id, vector_store_id=store_id)
            url = str((found.attributes or {}).get("url", ""))
            if url and url not in urls:
                urls.append(url)
    sources = "Sources:\n" + "\n".join(f"- {u}" for u in urls) if urls else NO_SOURCES
    return f"{response.output_text}\n\n{sources}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ask OptiBot a question against the vector store.")
    parser.add_argument("question")
    parser.add_argument(
        "--prompt-file", metavar="FILE", help="system prompt (default: PROMPT_FILE)"
    )
    args = parser.parse_args(argv)
    print(ask(args.question, args.prompt_file))
