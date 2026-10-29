import logging
from collections.abc import Callable

log = logging.getLogger("kbsync")


def token_counter(load: Callable[[], object] | None = None) -> Callable[[str], int]:
    """Count tokens with tiktoken cl100k_base; fall back to len//4 if it cannot be loaded."""
    try:
        if load is None:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
        else:
            encoding = load()
    except Exception:  # noqa: BLE001 - offline or missing encoding file: approximate instead
        log.warning("Token counts are approximate.")
        return lambda text: len(text) // 4
    return lambda text: len(encoding.encode(text, disallowed_special=()))
