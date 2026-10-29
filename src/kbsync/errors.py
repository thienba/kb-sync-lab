import sys
from collections.abc import Callable


class KbSyncError(Exception):
    exit_code = 1


class ConfigError(KbSyncError):
    exit_code = 2


class ListingError(KbSyncError):
    exit_code = 3


class StoreError(KbSyncError):
    exit_code = 4


class TooManyFailures(KbSyncError):
    exit_code = 5


def run(fn: Callable[[], None]) -> None:
    """Run fn; print a KbSyncError as one plain stderr line and exit with its code."""
    try:
        fn()
    except KbSyncError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(exc.exit_code)
