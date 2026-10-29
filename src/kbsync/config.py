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


def load_config(env_file: str = ".env") -> Config:
    if Path(env_file).is_file():
        load_dotenv(env_file, override=False)
    api_key = os.environ.get("API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ConfigError(MISSING_KEY)
    return Config(
        api_key=api_key,
        vector_store_name=os.environ.get("VECTOR_STORE_NAME") or "optibot-kb",
    )
