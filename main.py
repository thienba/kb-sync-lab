import argparse

from kbsync.config import load_config
from kbsync.errors import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the knowledge base into the vector store.")
    parser.add_argument("--limit", type=int, metavar="N", help="process at most N articles")
    parser.add_argument("--full", action="store_true", help="ignore saved state and rebuild all")
    parser.parse_args()
    load_config()


if __name__ == "__main__":
    run(main)
