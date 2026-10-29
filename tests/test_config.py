import pytest

from kbsync.config import load_config
from kbsync.errors import ConfigError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ("API_KEY", "OPENAI_API_KEY", "VECTOR_STORE_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_api_key_read(monkeypatch):
    monkeypatch.setenv("API_KEY", "a")
    assert load_config().api_key == "a"


def test_openai_api_key_alias(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    assert load_config().api_key == "b"


def test_api_key_wins_over_alias(monkeypatch):
    monkeypatch.setenv("API_KEY", "a")
    monkeypatch.setenv("OPENAI_API_KEY", "b")
    assert load_config().api_key == "a"


def test_missing_key_raises_config_error():
    with pytest.raises(ConfigError):
        load_config()


def test_default_store_name(monkeypatch):
    monkeypatch.setenv("API_KEY", "a")
    assert load_config().vector_store_name == "optibot-kb"


def test_dotenv_loaded_but_does_not_override(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("API_KEY=fromfile\nVECTOR_STORE_NAME=filestore\n")
    monkeypatch.setenv("VECTOR_STORE_NAME", "envstore")
    cfg = load_config()
    assert cfg.api_key == "fromfile"
    assert cfg.vector_store_name == "envstore"
