import logging
import os
import re
import sys

KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
MASK = "***"


BEARER_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def redact(text: str) -> str:
    """Mask the configured keys, the heartbeat URL, `sk-` keys and Bearer tokens."""
    for name in ("API_KEY", "OPENAI_API_KEY", "HEALTHCHECK_URL"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, MASK)
    text = KEY_PATTERN.sub(MASK, text)
    return BEARER_PATTERN.sub(lambda m: m.group(1) + MASK, text)


class RedactingFormatter(logging.Formatter):
    """Formats the full record (message, args, traceback), then redacts it."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    # Masking applies to this handler's output (the only one setup_logging installs).
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # The HTTP clients log every request at INFO, which buries the run's own lines.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
