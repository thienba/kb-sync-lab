import hashlib
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import openai
import pytest

from kbsync.ask import DEFAULT_MODEL, main
from kbsync.errors import ConfigError, StoreError

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "optibot.md"
PROMPT_SHA256 = "0f62ced1eea92f562139fc2d6c365319eef8e7b4431e169f67ae7777a6a2f204"
VERBATIM = (
    "You are OptiBot, the customer-support bot for OptiSigns.com."
    "• Tone: helpful, factual, concise."
    "• Only answer using the uploaded docs."
    "• Max 5 bullet points; else link to the doc."
    '• Cite up to 3 "Article URL:" lines per reply.'
)
QUESTION = "How do I add a YouTube video?"


def response(*file_ids):
    notes = [NS(type="file_citation", file_id=f, filename=f, index=0) for f in file_ids]
    notes.insert(0, NS(type="url_citation", url="x"))
    msg = NS(type="message", content=[NS(type="output_text", text="Answer.", annotations=notes)])
    return NS(output=[NS(type="file_search_call"), msg], output_text="Answer.")


class FakeClient:
    def __init__(self, file_ids=(), stores=("vs_1",)):
        self.calls = []
        self.responses = NS(create=self._create)
        page = NS(data=[NS(id=s, name="optibot-kb") for s in stores], has_more=False)
        files = NS(
            retrieve=lambda fid, vector_store_id: NS(attributes={"url": f"https://u/{fid[:2]}"})
        )
        self.vector_stores = NS(list=lambda **kw: page, files=files)
        self.file_ids = file_ids

    def _create(self, **kw):
        self.calls.append(kw)
        return response(*self.file_ids)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in (
        "OPENAI_MODEL",
        "PROMPT_FILE",
        "VECTOR_STORE_ID",
        "VECTOR_STORE_NAME",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("API_KEY", "k")


@pytest.fixture
def client(monkeypatch):
    c = FakeClient(("aa1", "bb1", "aa1"))
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: c)
    return c


def test_prompt_file_is_verbatim():
    collapsed = " ".join(PROMPT.read_text(encoding="utf-8").split())
    assert collapsed.replace(" •", "•") == VERBATIM
    assert PROMPT.read_text(encoding="utf-8").count("\n• ") == 4


def test_prompt_sha256_is_pinned():
    assert hashlib.sha256(PROMPT.read_bytes()).hexdigest() == PROMPT_SHA256


def test_calls_responses_create_once(client, capsys):
    main([QUESTION])
    assert client.calls == [
        {
            "model": DEFAULT_MODEL,
            "instructions": PROMPT.read_text(encoding="utf-8"),
            "input": QUESTION,
            "tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}],
        }
    ]


def test_model_from_env(client, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "m-x")
    main([QUESTION])
    assert client.calls[0]["model"] == "m-x"


def test_output_lists_distinct_sources_in_order(client, capsys):
    main([QUESTION])
    assert capsys.readouterr().out == "Answer.\n\nSources:\n- https://u/aa\n- https://u/bb\n"


def test_no_citations(monkeypatch, capsys):
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: FakeClient())
    main([QUESTION])
    assert capsys.readouterr().out == ("Answer.\n\nSources: none (the bot did not cite a file)\n")


def test_prompt_file_env_and_flag(client, monkeypatch, tmp_path):
    Path("env.md").write_text("from env")
    Path("flag.md").write_text("from flag")
    monkeypatch.setenv("PROMPT_FILE", "env.md")
    main([QUESTION])
    assert client.calls[-1]["instructions"] == "from env"
    main([QUESTION, "--prompt-file", "flag.md"])
    assert client.calls[-1]["instructions"] == "from flag"


def test_missing_prompt_file(client):
    with pytest.raises(ConfigError, match="^Prompt file not found: other.md$"):
        main([QUESTION, "--prompt-file", "other.md"])


def test_store_not_found_is_not_created(monkeypatch):
    c = FakeClient(stores=())
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: c)
    with pytest.raises(
        StoreError, match=re.escape("Vector store 'optibot-kb' not found. Run main.py first.")
    ):
        main([QUESTION])
    assert c.calls == []


def test_missing_key(monkeypatch):
    monkeypatch.delenv("API_KEY")
    with pytest.raises(ConfigError, match="API_KEY is not set"):
        main([QUESTION])


def test_no_question_prints_usage_and_exits_2():
    r = subprocess.run(
        [sys.executable, str(ROOT / "ask.py")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src")},
    )
    assert r.returncode == 2
    assert "usage:" in r.stderr
