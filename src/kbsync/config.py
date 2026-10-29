import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from kbsync.errors import ConfigError

MISSING_KEY = "API_KEY is not set. Copy .env.sample to .env or pass -e API_KEY=..."


@dataclass(frozen=True)
class Config:
    api_key: str
    vector_store_name: str
    vector_store_id: str | None = None
    chunk_max_tokens: int = 600
    chunk_overlap_tokens: int = 150


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}.") from None


def load_config(env_file: str = ".env") -> Config:
    if Path(env_file).is_file():
        load_dotenv(env_file, override=False)
    api_key = os.environ.get("API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError(MISSING_KEY)
    max_tokens = _int_env("CHUNK_MAX_TOKENS", 600)
    overlap = _int_env("CHUNK_OVERLAP_TOKENS", 150)
    if not 100 <= max_tokens <= 4096:
        raise ConfigError(f"CHUNK_MAX_TOKENS must be between 100 and 4096, got {max_tokens}.")
    if not 0 <= overlap <= max_tokens // 2:
        raise ConfigError(
            f"CHUNK_OVERLAP_TOKENS must be between 0 and {max_tokens // 2} "
            f"(half of CHUNK_MAX_TOKENS), got {overlap}."
        )
    return Config(
        api_key=api_key,
        vector_store_name=os.environ.get("VECTOR_STORE_NAME") or "optibot-kb",
        vector_store_id=os.environ.get("VECTOR_STORE_ID") or None,
        chunk_max_tokens=max_tokens,
        chunk_overlap_tokens=overlap,
    )
