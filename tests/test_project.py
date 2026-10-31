import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.filterwarnings("ignore:A test tried to use socket.socket")
def test_socket_is_blocked():
    with pytest.raises(Exception, match="(?i)socket"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", 9))


def test_env_sample_leaves_the_image_paths_alone():
    text = (ROOT / ".env.sample").read_text()
    assert not re.search(r"^(OUT_DIR|PUBLIC_DIR)=", text, re.M)
    assert "# PUBLIC_DIR=public" in text
    assert "# OUT_DIR=out" in text


def test_env_sample_has_no_real_key():
    text = (ROOT / ".env.sample").read_text()
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", text)
    for name in (
        "API_KEY",
        "VECTOR_STORE_ID",
        "VECTOR_STORE_NAME=optibot-kb-dev",
        "OPENAI_MODEL",
        "HEALTHCHECK_URL",
        "FULL_REBUILD",
    ):
        assert re.search(rf"^{name}", text, re.M)


def _main(*args, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT / "tests",
    )


def test_main_without_key_exits_2():
    result = _main()
    assert result.returncode == 2
    assert result.stderr == (
        "API_KEY is not set. Copy .env.sample to .env or pass -e API_KEY=...\n"
    )


def test_main_help_lists_flags():
    result = _main("--help")
    assert result.returncode == 0
    assert "--limit N" in result.stdout
    assert "--full" in result.stdout


def test_ci_workflow():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert ci["permissions"] == {"contents": "read"}
    steps = [s.get("run") for s in ci["jobs"]["test"]["steps"]]
    for cmd in (
        "uv sync --locked",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pytest -q",
    ):
        assert cmd in steps
