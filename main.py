from kbsync.errors import run
from kbsync.logs import setup_logging
from kbsync.pipeline import run as pipeline


def main() -> None:
    setup_logging()
    pipeline()


if __name__ == "__main__":
    run(main)
