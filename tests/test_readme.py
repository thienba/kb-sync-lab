import re
from pathlib import Path

from kbsync.config import Config

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()

HEADINGS = [
    "Setup",
    "Run locally",
    "Run in Docker",
    "Chunking",
    "Links",
    "No Assistant object",
    "Daily job",
    "Screenshot",
    "Security",
    "Tests",
]


def test_one_page():
    assert len(README.splitlines()) <= 80
    assert len(README.split()) <= 500


def test_sections_in_order():
    found = re.findall(r"^## (.+)$", README, re.M)
    assert found == HEADINGS
    assert README.index("```mermaid") < README.index("## Setup")


def test_mermaid_flowchart_has_every_node():
    block = re.search(r"```mermaid\n(.*?)```", README, re.S).group(1)
    assert block.startswith("flowchart")
    for label in (
        "GitHub main",
        "deploy.sh",
        "Job image",
        "Zendesk API",
        "HTML → Markdown",
        "Find delta",
        "Upload delta",
        "Run report",
        "OpenAI vector store",
        "OptiBot answers",
        "Status page",
        "healthchecks.io",
    ):
        assert label in block
    assert block.count("-->") == 12


def test_relative_links_and_images_exist():
    targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", README)
    assert "docs/screenshot-youtube.png" in targets
    for target in targets:
        if re.match(r"[a-z]+:", target):
            continue
        assert (ROOT / target.split("#")[0]).exists(), target
    for path in re.findall(r"`((?:docs|prompts)/[\w./-]+)`", README):
        assert (ROOT / path).exists(), path


def test_required_strings():
    for text in (
        "https://kbsync.bahuynh.com/runs/latest.json",
        "docs/chunking.md",
        ".env.sample",
        "uv run main.py",
        "docker run",
        "docker run --rm -e API_KEY",
        "reduces the risk",
        "uv run pytest -q",
        "deploy/deploy.sh",
        "out/articles/",
        "02:17 UTC",
    ):
        assert text in README
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", README)


def test_chunk_numbers_match_config_defaults():
    cfg = Config(api_key="x", vector_store_name="x")
    assert f"{cfg.chunk_max_tokens} tokens with {cfg.chunk_overlap_tokens} overlap" in README
